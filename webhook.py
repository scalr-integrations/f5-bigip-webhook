#!/usr/bin/env python

from flask import Flask
from flask import request
from flask import abort

import pytz
import json
import logging
import binascii
import dateutil.parser
import hmac
import os

from hashlib import sha1
from datetime import datetime

from f5.bigip import ManagementRoot


logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# Configuration variables, taken from the environment
SCALR_SIGNING_KEY = os.getenv('SCALR_SIGNING_KEY', '')
BIGIP_ADDRESS = os.getenv('BIGIP_ADDRESS', '')
BIGIP_PORT = os.getenv('BIGIP_PORT', '443')
BIGIP_USER = os.getenv('BIGIP_USER', '')
BIGIP_PASS = os.getenv('BIGIP_PASS', '')
# Optional config
BIGIP_CONFIG_VARIABLE = os.getenv('BIGIP_CONFIG_VARIABLE', 'BIGIP_CONFIG')
DEFAULT_UPSTREAM_IP = os.getenv('DEFAULT_UPSTREAM_IP', 'auto')
DEFAULT_LB_METHOD = os.getenv('DEFAULT_LB_METHOD', 'least-connections-member')
DEFAULT_PARTITION = os.getenv('DEFAULT_PARTITION', 'Common')

for var in ['SCALR_SIGNING_KEY', 'BIGIP_ADDRESS', 'BIGIP_PORT', 'BIGIP_USER', 'BIGIP_PASS',
            'BIGIP_CONFIG_VARIABLE', 'DEFAULT_LB_METHOD', 'DEFAULT_PARTITION']:
    logging.info('Config: %s = %s', var, globals()[var] if 'PASS' not in var else '*' * len(globals()[var]))


# This is the expected format of the configuration global variable. partition and lb_method
# will default to the values above (unless overridden in config_prod.json) if not specified.
# upstream_ip can be public, private, or auto. Auto = public or private if there is no public ip
config_format = 'pool_name,instance_port,vs_name,vs_address,vs_port[,upstream_ip][,partition][,lb_method]'

# BIG-IP API client
client = ManagementRoot(BIGIP_ADDRESS, BIGIP_USER, BIGIP_PASS, port=BIGIP_PORT)


@app.route("/bigip/", methods=['POST'])
def webhook_listener():
    if not validate_request():
        abort(403)

    data = json.loads(request.data)
    if 'eventName' not in data or 'data' not in data:
        logging.info('Invalid request received')
        abort(404)

    logging.info('Received %s event for server %s', data['eventName'], data['data']['SCALR_SERVER_ID'])

    if data['eventName'] == 'HostUp':
        return add_host(data['data'])
    elif data['eventName'] in ['BeforeHostTerminate', 'HostDown']:
        return delete_host(data['data'])
    else:
        logging.warning('Received request for unhandled event %s', data['eventName'])
        return ''


def parse_config_variable(data):
    lb_method = DEFAULT_LB_METHOD
    partition = DEFAULT_PARTITION
    upstream_ip = DEFAULT_UPSTREAM_IP
    config = [c.strip() for c in data[BIGIP_CONFIG_VARIABLE].split(',')]
    if len(config) < 5:
        # Invalid config
        logging.warning('Invalid config received: %s', str(config))
        abort(400, 'Invalid config passed: {}. Config format: {}'.format(config, config_format))
    pool_name = config[0]
    instance_port = config[1]
    vs_name = config[2]
    vs_address = config[3]
    vs_port = config[4]
    if len(config) > 5:
        upstream_ip = config[5]
        if upstream_ip.lower() not in ['public', 'external', 'private', 'internal', 'auto']:
            abort(400, 'Invalid upstream_ip value. Got {}, expected one of: '
                       'public, external, private, internal, auto'.format(upstream_ip))
    if len(config) > 6:
        partition = config[6]
    if len(config) > 7:
        lb_method = config[7]
    return pool_name, instance_port, vs_name, vs_address, vs_port, upstream_ip, partition, lb_method


def get_upstream_ip(upstream_ip, data):
    if upstream_ip.lower() == 'public' or upstream_ip.lower() == 'external':
        return data['SCALR_EXTERNAL_IP']
    elif upstream_ip.lower() == 'private' or upstream_ip.lower() == 'internal':
        return data['SCALR_INTERNAL_IP']
    elif upstream_ip.lower() == 'auto':
        return data['SCALR_EXTERNAL_IP'] or data['SCALR_INTERNAL_IP']


def add_host(data):
    if BIGIP_CONFIG_VARIABLE not in data:
        logging.info('This server should not be added in a BIG-IP pool, skipping.')
        return 'Skipped'

    pool_name, instance_port, vs_name, vs_address, vs_port, upstream_ip, partition, lb_method = parse_config_variable(data)

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
        logging.info('Pool %s already exists', pool_name)
        pool = client.tm.ltm.pools.pool.load(name=pool_name, partition=partition)

    if not client.tm.ltm.virtuals.virtual.exists(name=vs_name, partition=partition):
        logging.info('Virtual server %s not found, creating it.', vs_name)
        logging.info('Virtual server destination: %s', vs_destination)
        client.tm.ltm.virtuals.virtual.create(
            name=vs_name,
            partition=partition,
            description='Scalr-managed virtual server',
            destination=vs_destination,
            mask='255.255.255.255',
            ipProtocol='tcp',
            sourceAddressTranslation={'type': 'automap'},
            pool=pool_name)
    else:
        logging.info('Virtual server %s already exists, reusing it', vs_name)

    # Add server to pool
    server_ip = get_upstream_ip(upstream_ip, data)
    server_name = '%s:%s' % (server_ip, instance_port)
    logging.info('Adding member %s in pool %s', server_name, pool_name)
    pool.members_s.members.create(partition=partition, name=server_name)
    # TODO: in case of failure, check if it is because the server already exists
    return 'Ok'


def delete_host(data):
    if BIGIP_CONFIG_VARIABLE not in data:
        logging.info('This server should not be removed from a BIG-IP pool, skipping.')
        return 'Skipped'
    
    pool_name, instance_port, vs_name, vs_address, vs_port, upstream_ip, partition, lb_method = parse_config_variable(data)

    if not client.tm.ltm.pools.pool.exists(name=pool_name, partition=partition):
        logging.info('Pool %s doesn\'t exist, has already been deleted', pool_name)
        return 'Nothing to delete'
    pool = client.tm.ltm.pools.pool.load(name=pool_name, partition=partition)

    server_ip = get_upstream_ip(upstream_ip, data)
    server_name = '%s:%s' % (server_ip, instance_port)
    if pool.members_s.members.exists(partition=partition, name=server_name):
        logging.info('Removing %s from pool %s', server_name, pool_name)
        member = pool.members_s.members.load(partition=partition, name=server_name)
        member.delete()
    else:
        logging.info('Member %s not found in pool, already deleted.', server_name)

    # Delete virtual server and pool if no members are left
    members = pool.members_s.get_collection()
    if len(members) == 0:
        logging.info('Pool is now empty, deleting virtual server and pool')
        if client.tm.ltm.virtuals.virtual.exists(name=vs_name, partition=partition):
            logging.info('Deleting virtual server %s', vs_name)
            virtual_server = client.tm.ltm.virtuals.virtual.load(name=vs_name, partition=partition)
            virtual_server.delete()
        logging.info('Deleting pool %s', pool_name)
        pool.delete()
    else:
        logging.info('%d members remaining in pool, not deleting', len(members))

    return 'Ok'


def validate_request():
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
