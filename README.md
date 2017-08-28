# F5 BIG-IP webhook

This is an integration between Scalr and the F5 BIG-IP load balancer. This webhook can be used to
automatically register and deregister Scalr instances from F5 load balancing pools.

## Setup instructions

The instructions below are written for RHEL 7 / Centos 7. Adapt as necessary for other distributions.

#### Webhook handler setup

- Install the required packages:
```
yum install epel-release
yum install git gcc python python-devel python-pip uwsgi uwsgi-plugin-python
```
- Retrieve the webhook code:
```
mkdir -p /opt/f5-bigip-webhook
cd /opt/f5-bigip-webhook
git clone https://github.com/scalr-integrations/f5-bigip-webhook.git .
```
- Install the Python dependencies
```
pip install -r requirements.txt
```
- Configure uwsgi to serve the webhook
```
cp uwsgi.ini /etc/uwsgi.d/f5-bigip-webhook.ini
chown uwsgi:uwsgi /etc/uwsgi.d/f5-bigip-webhook.ini
systemctl enable uwsgi
```
Uwsgi will  bind to 0.0.0.0 and serve the webhook on port 5010 by default. Edit the ini file to change
this behaviour.

#### Scalr webhook setup

Log into Scalr at the global scope, and click on Webhooks in the main menu.
In the Endpoints section, create a new endpoint with URL: `http://<server-ip>:5010/bigip/`

Note down the signing key that Scalr generated, we will need it later.

#### Webhook configuration

Create the production configuration file:
```
cp config.json config_prod.json
```

Edit the `config_prod.json` file and complete it with the Scalr signing key that Scalr generated.

Reload the configuration:
```
systemctl restart uwsgi
```

## Testing and troubleshooting

The uwsgi logs are appended to `/var/log/messages`.

To check that the web server is serving our webhook, run the following command on the webhook server:
```
curl -XPOST http://localhost:5010/bigip/
```

You should get a 403 error, because our request was not signed. If that is not the case, check for errors in the uwsgi logs.

