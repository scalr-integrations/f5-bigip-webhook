FROM debian:jessie-slim
MAINTAINER Aloys Augustin <aloys@scalr.com>

RUN apt-get update && \
    apt-get install -y --no-install-recommends python python-dev python-pip uwsgi uwsgi-plugin-python && \
    groupadd uwsgi && \
    useradd -g uwsgi uwsgi

ADD ./requirements.txt /requirements.txt

RUN pip install -r /requirements.txt

ADD . /opt/f5-bigip-webhook

ENTRYPOINT ["/usr/bin/uwsgi", "--ini"]
CMD ["/opt/f5-bigip-webhook/uwsgi.ini"]

