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

Log into Scalr at the scope you want to use this webhook in, and click on Webhooks in the main menu.
In the Endpoints section, create a new endpoint with URL: `http://<server-ip>:5010/bigip/`

Note down the signing key that Scalr generated, we will need it later.

In the Webhooks section, create a new Webhook with the following settings:

 - Name: BIG-IP integration
 - Endpoints: the endpoint you just created
 - Events: HostUp, BeforeHostTerminate, HostDown
 - Farms: All farms
 - Timeout: 10 sec
 - Max. delivery attempts: 3

and click on the save icon to save the webhook.

#### Webhook configuration

Edit `/etc/uwsgi.d/f5-bigip-webhook.ini` and fill in the configuration variables.

Then restart uwsgi to reload the configuration:
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

Next you should configure a Farm Role to be registered in a load balancing pool.
Go into a Farm Role's configuration, and click on Global Variables. Create a new global variable named `BIGIP_CONFIG`.
Click on the lock button to prevent it from being changed at a lower scope. The value should contain the following settings,
in this order, separated by commas:

 - pool name: the name of the load balancing pool
 - instance port: The port that the traffic should be forwarded to on the instances
 - virtual server name: the name of the virtual server
 - virtual server address: The address the virtual server should listen on
 - virtual server port: The port the virtual server should listen on
 - partition: OPTIONAL, defaults to `Common`
 - load balancing method: OPTIONAL, defaults to `least-connections-member`

Example value:
```
my_app_pool,8000,my_app_virtual_server,10.0.0.10,80
```

When the first instance is started, the webhook will create the virtual server and the pool in the BIG-IP system. 
As additional instances come up or go down, they will be added to or removed from the pool.
If no servers are left in the pool, the virtual server and the pool will be deleted.
