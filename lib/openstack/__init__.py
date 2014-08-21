# -*- coding: utf-8 -*-
import requests
import base64
import json
import time
import socket
import os

import utils

class Session:
    def __init__(self, auth_url=None, login=None, password=None,
                 tenant_name=None, region_name=None, hostname_prefix=None):
        auth_url = auth_url or os.environ.get('OS_AUTH_URL')
        login = login or os.environ.get('OS_USERNAME')
        self.password = password or os.environ.get('OS_PASSWORD')
        region_name = region_name or os.environ.get('OS_REGION_NAME')
        tenant_name = tenant_name or os.environ.get('OS_TENANT_NAME')
        self.hostname_prefix = hostname_prefix or os.environ.get('OS_HOSTNAME_PREFIX')
        user_info = utils.get_user_info(auth_url, login, self.password, tenant_name)
        # Collect authorization token
        self.token_id = user_info['access']['token']['id']
        # Collect services' endpoints
        self.service_catalog = utils.get_service_catalog(user_info['access']['serviceCatalog'],
                                                         region_name)

    def get(self, url):
        headers = {'Accept': "application/json",
                   'X-Auth-Token': self.token_id}

        r = requests.get(url, headers=headers, timeout=utils.TIMEOUT)

        if r.status_code != requests.status_codes.codes.ok:
            raise utils.OpenStackApiError(r.json(), r.status_code)

        return r.json()

    def post(self, url, data):
        headers = {
            'Content-Type': "application/json",
            'Accept': "application/json",
            'X-Auth-Token': self.token_id
        }

        r = requests.post(url, data=json.dumps(data), headers=headers, timeout=utils.TIMEOUT)

        if r.status_code not in [requests.status_codes.codes.ok,
                                 requests.status_codes.codes.accepted]:
            raise utils.OpenStackApiError(r.json(), r.status_code)
        
        return r.json()

    def delete(self, url):
        headers = {'Content-Type': "application/json",
                   'Accept': "application/json",
                   'X-Auth-Token': self.token_id}

        r = requests.delete(url, headers=headers, timeout=utils.TIMEOUT)

        if r.status_code != 204:
            raise utils.OpenStackApiError(r.json(), r.status_code)

    def create_instances(self, config, check=True):
        # Waiting for DNS records update
        instances = []
        for instance_cfg in config['servers']:
            instances += utils.get_instances_names_from_conf(instance_cfg)

        for i in instances:
            try:
                while socket.gethostbyname(i):
                    print '.',
                    time.sleep(3)
            except socket.gaierror:
                print('\nA-record for {0} was deleted'.format(i))

        for instance_cfg in config['servers']:
            self.create_instance(data=instance_cfg)

        instances = []
        for instance_cfg in config['servers']:
            instances += utils.get_instances_names_from_conf(instance_cfg)

        if check:
            if not utils.check_availability(session=self, instances=instances):
                raise RuntimeError("Not all nodes available")

    def delete_instances(self, config): 
        for instance_cfg in config['servers']:
            instances = utils.get_instances_names_from_conf(instance_cfg)

            self.delete_instance(instance_cfg["name"])

            for i in instances:
                self.delete_instance(i)

    def rebuild_instances(self, config):
        instances = []
        for instance_cfg in config['servers']:
            instances += utils.get_instances_names_from_conf(instance_cfg)

        for i in instances:
            self.rebuild_instance(i)

        if not utils.check_availability(session=self, instances=instances):
            raise RuntimeError("Not all nodes available")

    def create_instance(self, data):
        """ Creates instance
        """
        data = self._get_data_from_config(data)
        url = utils.get_url(self.service_catalog['compute'], "SERVERS")

        instance_info = self.post(url, data)

        return instance_info

    def delete_instance(self, instance_name):
        """ Deletes instance
        """
        instance_info = self.get_instance_info(instance_name)
        if instance_info is None:
            return False
        else:
            instance_id = instance_info['id']

        url = utils.get_url(self.service_catalog['compute'], "SERVERS_SERVER",
                            instance_id=instance_id)

        self.delete(url)

        return True

    def rebuild_instance(self, instance_name):
        """ Fast recreating instance with the same name and image
        """
        instance_info = self.get_instance_info(instance_name)
        if instance_info is None:
            return False
        
        instance_id = instance_info['id']
        image_ref = instance_info['image']['links'][0]['href']

        data = {"rebuild": {"name": instance_name,
                            "imageRef": image_ref,
                            "adminPass": self.password}}

        url = utils.get_url(self.service_catalog['compute'], "ACTION",
                            server_id=instance_id)

        response = self.post(url, data)

        return response

    def get_images_list(self):
        """ Returns list of images
        """
        url = utils.get_url(self.service_catalog['compute'], "IMAGES")
        images_list = self.get(url)['images']
        return images_list

    def get_flavors_list(self):
        """ Returns list of flavors (CPU's, RAM, disk space)
        """
        url = utils.get_url(self.service_catalog['compute'], "FLAVORS")
        flavors_list = self.get(url)['flavors']
        return flavors_list

    def get_networks_list(self):
        """ Returns list of networks
        """
        url = utils.get_url(self.service_catalog['compute'], "NETWORKS")
        networks_list = self.get(url)['networks']
        return networks_list

    def get_image_id(self, image_name):
        images_list = self.get_images_list()
        for image in images_list:
            if image['name'] == image_name:
                return image['id']
        return None

    def get_flavor_id(self, flavor_name):
        flavors_list = self.get_flavors_list()
        for flavor in flavors_list:
            if flavor['name'] == flavor_name:
                return flavor['id']
        return None

    def get_networks_uuid_list(self, networks_label_list):
        networks_list = self.get_networks_list()
        uuid_list = []
        for network in networks_list:
            if network['label'] in networks_label_list:
                uuid_list.append({"uuid": str(network['id'])})
        return uuid_list

    def _get_data_from_config(self, config):
        return {
            "server": {
                "name": config['name'],
                "imageRef": self.get_image_id(config['image_name']),
                "key_name": config['key_name'],
                "flavorRef": self.get_flavor_id(config['flavor_name']),
                "max_count": config['max_count'],
                "min_count": config['min_count'],
                "networks": self.get_networks_uuid_list(config['networks_label_list']),
                "user_data": base64.b64encode(utils.USER_DATA)
                }
            }

    def get_instance_info(self, instance_name):
        # get instance's id
        created_instances = self.get_instances()
        for i in created_instances:
            if i['name'] == instance_name:
                instance_id = str(i['id'])
                break
        else:
            return None
                
        url = utils.get_url(self.service_catalog['compute'], "SERVERS_SERVER",
                            instance_id=instance_id)
        try:
            instance = self.get(url)['server']
        except utils.OpenStackApiError as e:
            if e.response_code == requests.status_codes.codes.not_found:
                return None
            else:
                raise
        
        return instance

    def get_instances(self):
        url = utils.get_url(self.service_catalog['compute'], "SERVERS")
        instances = self.get(url)['servers']
        return instances
