#!/usr/bin/env python

from flask import Flask
from flask import request
from flask import abort

import pytz
import string
import random
import json
import logging
import binascii
import dateutil.parser
import hmac
import os

from hashlib import sha1
from datetime import datetime

from f5.bigip import ManagementRoot


config_file = './config_prod.json'

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# Configuration variables, taken from the environment
SCALR_SIGNING_KEY = os.getenv('SCALR_SIGNING_KEY', '')
BIGIP_ADDRESS = os.getenv('BIGIP_ADDRESS', '')
BIGIP_USER = os.getenv('BIGIP_USER', '')
BIGIP_PASS = os.getenv('BIGIP_PASS', '')
# Optional config
BIGIP_CONFIG_VARIABLE = os.getenv('BIGIP_CONFIG_VARIABLE', 'BIGIP_CONFIG')
DEFAULT_LB_METHOD = os.getenv('DEFAULT_LB_METHOD', 'least-connections-member')
DEFAULT_PARTITION = os.getenv('DEFAULT_PARTITION', 'Common')

# This is the expected format of the configuration global variable. partition and lb_method
# will default to the values above (unless overridden in config_prod.json) if not specified.
config_format = 'pool_name,instance_port,vs_name,vs_address,vs_port[,partition][,lb_method]'

# BIG-IP API client
client = ManagementRoot(BIGIP_ADDRESS, BIGIP_USER, BIGIP_PASS)


@app.route("/bigip/", methods=['POST'])
def webhook_listener():
    if not validate_request(request):
        abort(403)

    data = json.loads(request.data)
    if 'eventName' not in data or 'data' not in data:
        logging.info('Invalid request received')
        abort(404)

    logging.info('Received %s event', data['eventName'])

    if data['eventName'] == 'HostUp':
        return add_host(data['data'])
    elif data['eventName'] in ['BeforeHostTerminate', 'HostDown']:
        return delete_host(data['data'])
    else:
        logging.warning('Received request for unhandled event %s', data['eventName'])
        return ''


def add_host(data):
    if not BIGIP_CONFIG_VARIABLE in data:
        logging.info('This server should not be added in a BIG-IP pool, skipping.')
        return 'Skipped'
    lb_method = DEFAULT_LB_METHOD
    partition = DEFAULT_PARTITION
    config = [c.strip() for c in data[BIGIP_CONFIG_VARIABLE].split(',')]
    if len(config) < 5:
        # Invalid config
        logging.warning('Invalid config received: %s', str(config))
        abort(400, 'Invalid config passed: {}. Config format: {}'.format(config, config_format))
    pool_name = config[0]
    instance_port = config[1]
    vs_name  = config[2]
    vs_address = config[3]
    vs_port = config[4]
    if len(config) > 5:
        partition = config[5]
    if len(config) > 6:
        lb_method = config[6]

    vs_destination = '%s:%s' % (vs_address, vs_port)

    # Create pool and virtual server if they don't exist
    if not client.tm.ltm.pools.pool.exists(name=pool_name, partition=partition):
        logging.info('Pool %s not found, creating it.', pool_name)
        pool = client.tm.ltm.pools.pool.create(
            name=pool_name,
            partition=partition,
            description='Scalr-managed pool',
            loadBalancingMode=lb_method)
    else:
        pool = client.tm.ltm.pools.pool.load(name=pool_name, partition=partition)

    if not client.tm.ltm.virtuals.virtual.exists(name=vs_name, partition=partition):
        logging.info('Virtual server %s not found, creating it.', vs_name)
        logging.info('Virtual server destination: %s', vs_destination)
        virtual = client.tm.ltm.virtuals.virtual.create(
            name=vs_name,
            partition=partition,
            description='Scalr-manages virtual server',
            destination=vs_destination,
            mask='255.255.255.255',
            ipProtocol='tcp',
            pool=pool_name)

    # Add server to pool
    server_ip = data['SCALR_INTERNAL_IP']
    server_name = '%s:%s' % (server_ip, instance_port)
    logging.info('Adding member %s in pool %s', server_name, pool_name)
    member = pool.members_s.members.create(partition=partition, name=server_name)

    return 'Ok'


def delete_host(data):
    if not BIGIP_CONFIG_VARIABLE in data:
        logging.info('This server should not be removed from a BIG-IP pool, skipping.')
        return 'Skipped'
    lb_method = DEFAULT_LB_METHOD
    partition = DEFAULT_PARTITION
    config = [c.strip() for c in data[BIGIP_CONFIG_VARIABLE].split(',')]
    if len(config) < 5:
        # Invalid config
        logging.warning('Invalid config received: %s', str(config))
        abort(400, 'Invalid config passed: {}. Config format: {}'.format(config, config_format))
    pool_name = config[0]
    instance_port = config[1]
    vs_name  = config[2]
    vs_address = config[3]
    vs_port = config[4]
    if len(config) > 5:
        partition = config[5]
    if len(config) > 6:
        lb_method = config[6]

    if not client.tm.ltm.pools.pool.exists(name=pool_name, partition=partition):
        logging.info('Pool %s doesn\'t exist, has already been deleted', pool_name)
        return 'Nothing to delete'
    pool = client.tm.ltm.pools.pool.load(name=pool_name, partition=partition)

    server_ip = data['SCALR_INTERNAL_IP']
    server_name = '%s:%s' % (server_ip, instance_port)
    if pool.members_s.members.exists(partition=partition, name=server_name):
        logging.info('Removing %s from pool %s', server_name, pool_name)
        member = pool.members_s.members.load(partition=partition, name=server_name)
        member.delete()

    # Delete virtual server and pool if no members are left
    members = pool.members_s.get_collection()
    if len(members) == 0:
        logging.info('Pool is now empty, deleting virtual server and pool')
        if client.tm.ltm.virtuals.virtual.exists(name=vs_name, partition=partition):
            logging.info('Deleting virtual server %s', vs_name)
            virtual_server = client.tm.ltm.virtuals.virtual.load(name=vs_name, partition=partition)
            virtual_server.delete()
        pool.delete()

    return 'Ok'


def validate_request(request):
    if 'X-Signature' not in request.headers or 'Date' not in request.headers:
        logging.debug('Missing signature headers')
        return False
    date = request.headers['Date']
    body = request.data
    expected_signature = binascii.hexlify(hmac.new(SCALR_SIGNING_KEY, body + date, sha1).digest())
    if expected_signature != request.headers['X-Signature']:
        logging.debug('Signature does not match')
        return False
    date = dateutil.parser.parse(date)
    now = datetime.now(pytz.utc)
    delta = abs((now - date).total_seconds())
    return delta < 300


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0')
