import os
import sys
import json
import subprocess
import ConfigParser

import openstack
import teamcity_messages

class AnsiblePlaybookError(Exception):
    pass

def set_vars(vars_path, params):
    with open(vars_path, 'w') as f:
        json.dump(params, f)

def run_playbook(playbook, inventory=None):
    tc_block = "ANSIBLE: {0}({1})".format(os.path.basename(playbook),
                                          os.path.basename(inventory))
    with teamcity_messages.block(tc_block):
        cmd = "ansible-playbook -v -i {0} {1}.yml".format(inventory, playbook)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)

        for line in iter(p.stdout.readline, ''):
            sys.stdout.write(line)

        p.wait()
        if p.returncode:
            raise AnsiblePlaybookError("Playbook {} failed (exit code: {})".format(playbook,
                                                                                   p.returncode))

def generate_inventory(inventory_path, clients_count, servers_per_group,
                       groups, instances_names):
    inventory_host_record_template = '{0} ansible_ssh_user=root'
    servers_group_template = 'servers-{0}'

    groups_count = len(servers_per_group)
    servers_count = sum(servers_per_group)

    inventory = ConfigParser.ConfigParser(allow_no_value=True)

    # Add clients section
    inventory.add_section(groups["clients"])
    for name in instances_names["clients"][:clients_count]:
        host_record = inventory_host_record_template.format(name)
        inventory.set(groups["clients"], host_record)
    # Add alias for clients' group (to use it in playbooks)
    clients_general_group = _as_group_of_groups('clients')
    inventory.add_section(clients_general_group)
    inventory.set(clients_general_group, groups["clients"])

    # Add servers section
    server_name = (x for x in instances_names["servers"])
    for group in xrange(groups_count):
        # Ansible group will be named as "servers-(<group> + 1)"
        group_name = servers_group_template.format(group + 1)
        inventory.add_section(group_name)
        for _ in xrange(servers_per_group[group]):
            host_record = inventory_host_record_template.format(next(server_name))
            inventory.set(group_name, host_record)

    # Group all servers' groups in associated group
    servers_group_defenition = _as_group_of_groups(groups["servers"])
    inventory.add_section(servers_group_defenition)
    for group in xrange(groups_count):
        group_name = servers_group_template.format(group + 1)
        inventory.set(servers_group_defenition, group_name)
    # Add an alias for servers' group (to use it in playbooks)
    servers_general_group = _as_group_of_groups('servers')
    inventory.add_section(servers_general_group)
    inventory.set(servers_general_group, groups["servers"])

    # Group clients and servers in associated group
    test_group_defenition = _as_group_of_groups(groups["test"])
    inventory.add_section(test_group_defenition)
    inventory.set(test_group_defenition, groups["clients"])
    inventory.set(test_group_defenition, groups["servers"])
    # Add an alias for combining (servers and clients) group (to use it in playbooks)
    servers_general_group = _as_group_of_groups('test')
    inventory.add_section(servers_general_group)
    inventory.set(servers_general_group, groups["test"])

    with open(inventory_path, 'w') as inventory_file:
        inventory.write(inventory_file)

def _as_group_of_groups(group):
    return '{0}:children'.format(group)

def get_host_names(instance_name, count):
    if count == 1:
        result = ["{0}-1".format(instance_name)]
    else:
        config = {"name": instance_name, "max_count": count}
        result = openstack.utils.get_instances_names_from_conf(config)
    return result

def _get_groups_names(name):
    groups = {"clients": "clients-{0}".format(name),
              "servers": "servers-{0}".format(name),
              "test": "test-{0}".format(name)}
    return groups
