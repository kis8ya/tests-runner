# -*- coding: utf-8 -*-
from __future__ import print_function

import subprocess
import shlex
import signal
import socket
import requests
import json
import time

from collections import deque
from functools import wraps

TIMEOUT = 60

ENDPOINTS_INFO = {"COMPUTE": {'uri': {"IMAGES": 'images',
                                      "FLAVORS": 'flavors/detail',
                                      "NETWORKS": 'os-networks',
                                      "SERVERS": 'servers',
                                      "SERVERS_SERVER": 'servers/{instance_id}',
                                      "ACTION": 'servers/{server_id}/action'}},
                  "IDENTITY": {'uri': {"TOKENS": 'tokens'}}}

# cloud-init config for customization post-creation actions
#
# Options:
#   apt_preserve_sources_list: preserve existing /etc/apt/sources.list
USER_DATA = """#cloud-config
apt_preserve_sources_list: true
"""

class OpenStackApiError(Exception):
    def __init__(self, message, response_code):
        Exception.__init__(self, message)
        self.response_code = response_code

    def __str__(self):
        return json.dumps(self.message, indent=4)

class TimeoutError(Exception):
    pass

def with_timeout(timeout=300):
    """Raises the timeout exception for decorated function after specific execution time."""
    def _alarm_handler(signal, frame):
        raise TimeoutError()

    def wrapper(func):
        @wraps(func)
        def decorator(*args, **kwargs):
            # set timeout
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(timeout)

            try:
                result = func(*args, **kwargs)
            finally:
                # turn off timer when the function processed
                signal.alarm(0)

            return result
        return decorator
    return wrapper

@with_timeout()
def wait_till_active(session, instances):
    """ Waits till instances will be in ACTIVE status
    (and returns a list of dicts {instance_name: ip})
    """
    hosts_ip = {}
    queue = deque(instances)
    while queue:
        instance = queue.pop()
        instance_info = session.get_instance_info(instance)

        if instance_info['status'] == "ACTIVE":
            # get ip address
            network_name = instance_info['addresses'].keys()[0]
            ip = [address['addr']
                  for address in instance_info['addresses'][network_name]
                  if address['version'] == 4]
            iname = get_fqdn(instance, session.hostname_prefix)
            hosts_ip[iname] = ip[0]
            continue
        queue.appendleft(instance)
    return hosts_ip

@with_timeout()
def check_ssh_port(ip_list):
    """ Checks that instances' ssh ports are available
    """
    queue = deque(ip_list)
    while queue:
        ip = queue.pop()
        # availability check
        cmd = "nc -z -w1 {0} 22".format(ip)
        if subprocess.call(shlex.split(cmd)) == 0:
            continue
        # if it's not available yet then return the instance to the queue
        queue.appendleft(ip)

@with_timeout()
def check_host_name_resolving(hosts_ip):
    """ Checks that ip resolving returns the same ip
    (which one we got from OpenStack API)
    """
    queue = deque(hosts_ip.items())
    while queue:
        host, ip = queue.pop()
        try:
            resolved_ip = socket.gethostbyname(host)
            if resolved_ip == ip:
                continue
        except socket.error:
            pass
        queue.appendleft((host, ip))

def check_availability(session, instances):
    """ Checks that instances are available
    """
    try:
        print("Waiting for nodes to initialize...", end=' ')
        hosts_ip = wait_till_active(session, instances)
        print("[DONE]")

        print("Waiting for nodes to become available via SSH...", end=' ')
        check_ssh_port(hosts_ip.values())
        print("[DONE]")

        print("Waiting for nodes to start resolving to right IPs...", end=' ')
        check_host_name_resolving(hosts_ip)
        print("[DONE]")

        return True
    except TimeoutError:
        print("[FAILED] Timeout reached.")
        return False

def get_instances_names_from_conf(instance_cfg):
    """ Returns list of instances' names
    """
    name = instance_cfg['name']
    count = instance_cfg['max_count']
    # generate the names
    if count == 1:
        instances = [name]
    else:
        # add -N suffix if max_count != 1
        # (where N is an instance number)
        instances = [name + '-' + str(i) for i in range(1, count + 1)]
    return instances

def get_url(endpoint_url, service_type, endpoint_type="COMPUTE", **kwargs):
    """ Returns Service Endpoint URL
    """
    url = concat_url(endpoint_url, ENDPOINTS_INFO[endpoint_type]['uri'][service_type])
    url = url.format(**kwargs)
    return url

def concat_url(endpoint, url):
    """Concatenates endpoint and url ending."""
    return "{}/{}".format(endpoint.strip("/"), url.strip("/"))

def get_user_info(auth_url, login, password, tenant_name):
    """Returns information about user."""
    headers = {
        'Content-Type': "application/json",
        'Accept': "application/json"
    }

    data = {
        'auth': {
            'tenantName': tenant_name,
            'passwordCredentials': {
                'username': login,
                'password': password
            }
        }
    }

    url = concat_url(auth_url, ENDPOINTS_INFO["IDENTITY"]["uri"]["TOKENS"])
    r = requests.post(url, data=json.dumps(data), headers=headers, timeout=TIMEOUT)

    if r.status_code not in [requests.status_codes.codes.ok,
                             requests.status_codes.codes.accepted]:
        raise OpenStackApiError(r.json(), r.status_code)

    return r.json()

def get_service_catalog(services_list, region_name):
    """Returns the service catalog."""
    catalog = {}
    for service in services_list:
        service_url = [i['adminURL'] for i in service['endpoints']
                       if i['region'] == region_name]
        catalog[service['type']] = service_url[0] if service_url else None
    return catalog

@with_timeout(120)
def wait_till_deleted(session, instance_name):
    """Waits for instance deletion."""
    while session.get_instance_info(instance_name):
        time.sleep(1)

def get_fqdn(name, dns_zone):
    """Returns a fully qualified domain name for specified host name and DNS zone."""
    return name + dns_zone
