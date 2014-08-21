"""Test configuration templates rendering module.

This module processes rendering for test configuration files with Jinja2 templates.
It allows to use clients and servers variables in these test configs as well as
any variable from **params** test config's section.

"""

import json
import copy
import os

from jinja2 import Environment, FileSystemLoader

import ansible_manager

from test_helper.utils import Node, Client

def _get_clients(clients_count, clients_names):
    client_port = 1083
    clients = [Client(client_name, client_port)
               for client_name in clients_names[:clients_count]]

    return clients

def _get_servers(servers_per_group, servers_names):
    servers = []
    server_port = 1025
    groups_count = len(servers_per_group)
    server_name = iter(servers_names)

    for group in xrange(groups_count):
        for _ in xrange(servers_per_group[group]):
            servers.append(Node(next(server_name), server_port, group + 1))

    return servers

def get_running(path, params, instances_names, clients_count, servers_per_group):
    """Returns test config as dictionary."""
    variables = copy.deepcopy(params)
    variables["clients"] = _get_clients(clients_count, instances_names["clients"])
    variables["servers"] = _get_servers(servers_per_group, instances_names["servers"])
    # Render test config
    environment = Environment(loader=FileSystemLoader('/'))
    template = environment.get_template(path)
    out = template.render(**variables)

    cfg = json.loads(out)

    return cfg
