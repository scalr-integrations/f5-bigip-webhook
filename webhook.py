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

from hashlib import sha1
from datetime import datetime



config_file = './config_prod.json'

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# will be overridden if present in config_file
SCALR_SIGNING_KEY = ''
F5_CONFIG_VARIABLE = 'F5_CONFIG'


@app.route("/bigip/", methods=['POST'])
def webhook_listener():
    if not validate_request(request):
        abort(403)

    data = json.loads(request.data)
    if 'eventName' not in data or 'data' not in data:
        logging.info('Invalid request received')
        abort(404)

    if data['eventName'] == 'HostUp':
        return add_host(data['data'])
    elif data['eventName'] in ['BeforeHostTerminate', 'HostDown']:
        return delete_host(data['data'])
    else:
        logging.info('Received request for unhandled event %s', data['eventName'])
        return ''


def add_host(data):
    if not F5_CONFIG_VARIABLE in data:
        return 'Skipped'
    config = data['F5_CONFIG_VARIABLE']
    # TODO
    return 'Ok'


def delete_host(data):
    if not F5_CONFIG_VARIABLE in data:
        return 'Skipped'
    config = data['F5_CONFIG_VARIABLE']
    # TODO
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


def load_config(filename):
    with open(filename) as f:
        options = json.loads(f.read())
        for key in options:
            if key in ['F5_CONFIG_VARIABLE']:
                logging.info('Loaded config: {}'.format(key))
                globals()[key] = options[key]
            elif key in ['SCALR_SIGNING_KEY']:
                logging.info('Loaded config: {}'.format(key))
                globals()[key] = options[key].encode('ascii')


load_config(config_file)

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0')
