import openstack
import copy
import itertools

session = openstack.Session()

flavors = {None: 0}
for f in session.get_flavors_list():
    flavors[f['name']] = f['ram']

def _get_flavor_name(flavor_id):
    flavor_list = session.get_flavors_list()
    for flavor in flavor_list:
        if flavor['id'] == flavor_id:
            return flavor['name']
    else:
        return None

def _satisfied(instance_name, flavor_name):
    #TODO: temporary fix for rebuild bug
    # rebuild isn't working; we are forcing to recreate instances instead of rebuilding them
    return False
    instance_info = session.get_instance_info(instance_name)
    if instance_info is None:
        return False
    else:
        current_flavor_name = _get_flavor_name(instance_info['flavor']['id'])
        return flavors[current_flavor_name] >= flavors[flavor_name]

def create(instances_cfg):
    instances_names = {}
    for instance_type, instance_cfg in instances_cfg.items():
        instances_names[instance_type] = openstack.utils.get_instances_names_from_conf(instance_cfg)
        for instance_name in instances_names[instance_type]:
            if _satisfied(instance_name, instance_cfg["flavor_name"]):
                session.rebuild_instance(instance_name)
            else:
                icfg = copy.deepcopy(instance_cfg)
                icfg["name"] = instance_name
                icfg["max_count"] = icfg["min_count"] = 1
                icfg = {"servers": [icfg]}

                session.delete_instance(instance_name)
                openstack.utils.wait_till_deleted(session, instance_name)
                session.create_instances(icfg, check=False)

    instances_names_list = list(itertools.chain.from_iterable(instances_names.values()))
    if openstack.utils.check_availability(session, instances_names_list):
        # Extending hostnames to FQDN
        for instance_type, instance_names in instances_names.items():
            instances_names[instance_type] = [openstack.utils.get_fqdn(name, session.hostname_prefix)
                                              for name in instance_names]
        return instances_names
    else:
        return None

def delete(instances_cfg):
    session.delete_instances(instances_cfg)

def _flavors_order(f):
    """ Ordering function for instance flavor
    (ordering by RAM)
    """
    return flavors[f]

def get_instances_cfg(instances_params, base_names):
    """ Prepares instances config for future usage
    """
    clients_conf = _get_cfg(base_names['client'],
                            instances_params["clients"]["flavor"],
                            instances_params["clients"]["count"],
                            instances_params["clients"]["image"])
    servers_conf = _get_cfg(base_names['server'],
                            instances_params["servers"]["flavor"],
                            instances_params["servers"]["count"],
                            instances_params["servers"]["image"])
    if servers_conf["max_count"] == 1:
        servers_conf["name"] += "-1"
    if clients_conf["max_count"] == 1:
        clients_conf["name"] += "-1"

    return {
        "clients": clients_conf,
        "servers": servers_conf
    }

def _get_cfg(name, flavor, count, image):
    return {
        "name": name,
        "image_name": image,
        "key_name": "",
        "flavor_name": flavor,
        "max_count": count,
        "min_count": count,
        "networks_label_list": [
            "SEARCHOPENSTACKVMNETS"
            ]
        }

def get_instances_params(test_configs):
    """Returns information about clients and servers."""
    instances_params = {'clients': {'count': 0, 'flavor': None, 'image': 'elliptics'},
                        'servers': {'count': 0, 'flavor': None, 'image': 'elliptics'}}

    clients_params = instances_params['clients']
    tests_params = [test_cfg['test_env_cfg']['clients'] for test_cfg in test_configs]

    clients_params['flavor'] = max((test_params['flavor'] for test_params in tests_params),
                                   key=_flavors_order)
    clients_params['count'] = max(test_params['count'] for test_params in tests_params)

    servers_params = instances_params['servers']
    tests_params = [test_cfg['test_env_cfg']['servers'] for test_cfg in test_configs]

    servers_params['flavor'] = max((test_params['flavor'] for test_params in tests_params),
                                   key=_flavors_order)
    servers_params['count'] = max(sum(test_params['count_per_group'])
                                  for test_params in tests_params)

    return instances_params
