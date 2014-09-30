#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import os
import json
import sys
import fnmatch
import ConfigParser
import pytest
import subprocess
import logging
import traceback
import copy

from collections import OrderedDict

import ansible_manager
import instances_manager
import teamcity_messages
import config_template_renderer as cfg_renderer

# Exit codes
EXIT_OK = 0
EXIT_TESTSFAILED = 1
EXIT_INTERNALERROR = 3

# util functions
def qa_storage_upload(file_path):
    storage = "http://qa-storage.yandex-team.ru"
    build_name = os.environ['TEAMCITY_BUILDCONF_NAME']
    build_name = build_name.replace(' ', '_')
    build_number = os.environ['BUILD_NUMBER']
    file_name = os.path.basename(file_path)
    url = '{storage}/upload/elliptics-testing/{build_name}/{build_number}/{file_name}'
    url = url.format(storage=storage, build_name=build_name,
                     build_number=build_number, file_name=file_name)

    cmd = ["curl", url, "--data-binary", "@" + file_path]
    subprocess.call(cmd)

    url = url.replace("/upload/", "/get/")

    return url

class InfoFilter(logging.Filter):
    """Custom filter for runner_logger."""
    def filter(self, record):
        """Filters INFO level records."""
        return record.levelno is logging.INFO

def setup_loggers(teamcity, verbose):
    info_handler = logging.StreamHandler(sys.stdout)
    info_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    info_handler.setFormatter(formatter)
    info_handler.addFilter(InfoFilter())

    error_handler = logging.StreamHandler()
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    tc_logging_level = logging.INFO if teamcity else logging.ERROR
    tc_logger = logging.getLogger('teamcity_logger')
    tc_logger.setLevel(tc_logging_level)
    tc_logger.addHandler(info_handler)

    runner_logging_level = logging.INFO if verbose else logging.ERROR
    runner_logger = logging.getLogger('runner_logger')
    runner_logger.setLevel(runner_logging_level)
    runner_logger.addHandler(info_handler)
    runner_logger.addHandler(error_handler)

    conf_file = '../lib/test_helper/logger.ini'
    parser = ConfigParser.ConfigParser()
    parser.read([conf_file])

    tests_logging_level = "ERROR" if teamcity else "INFO"
    parser.set('logger_testLogger', 'level', tests_logging_level)

    with open(conf_file, "w") as conf:
        parser.write(conf)
#END of util functions

class TestError(Exception):
    pass

class TestRunner(object):
    def __init__(self, args):
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_dir = os.path.abspath(os.path.join(repo_dir, ".."))
        self.ansible_dir = os.path.join(self.project_dir, "ansible")
        self.configs_dir = args.configs_dir
        self.user = args.user
        if args.testsuite_params:
            with open(args.testsuite_params, 'r') as f:
                self.testsuite_params = json.load(f)
        else:
            self.testsuite_params = {}

        self.logger = logging.getLogger('runner_logger')
        self.teamcity = args.teamcity

        self.tests = self._get_ordered_tests(args.tags)
        self.inventory = self.get_inventory(args.inventory, args.instance_name)
        self.tests = self.expand_tests_configs()

        with teamcity_messages.block("PREPARE TEST ENVIRONMENT"):
            self.prepare_ansible_test_files()
            self.install_elliptics_packages()

    def _collect_tests(self, tags):
        """Collects tests' configs with given tags."""
        tests = {}
        for root, _, filenames in os.walk(self.configs_dir):
            for filename in fnmatch.filter(filenames, 'test_*.cfg'):
                path = os.path.abspath(os.path.join(root, filename))
                cfg = json.load(open(path))
                cfg["_running_path"] = path.replace(".cfg", ".run")
                if set(cfg["tags"]).intersection(set(tags)):
                    # test config name format: "test_NAME.cfg"
                    test_name = os.path.splitext(filename)[0][5:]
                    tests[test_name] = cfg
        return tests

    def _get_ordered_tests(self, tags):
        """Returns ordered tests with given tags."""

        def tests_with_order(tests, order):
            return [(test_name, params) for test_name, params in tests.items()
                    if params.get("order") == order]

        tests = self._collect_tests(tags)
        ordered_tests = OrderedDict()
        ordered_tests.update(tests_with_order(tests, "tryfirst"))
        ordered_tests.update(tests_with_order(tests, None))
        ordered_tests.update(tests_with_order(tests, "trylast"))
        return ordered_tests

    def create_cloud_instances(self, instance_name):
        """Creates cloud instances and returns a dictionary with their names."""
        instances_names = {'client': "{0}-client".format(instance_name),
                           'server': "{0}-server".format(instance_name)}

        instances_params = instances_manager.get_instances_params(self.tests.values())

        instances_cfg = instances_manager.get_instances_cfg(instances_params, instances_names)

        inventory = instances_manager.create(instances_cfg)
        if not inventory:
            raise RuntimeError("Not all nodes available")

        return inventory

    def get_inventory(self, inventory_path, instance_name):
        """Returns inventory for testrunner."""
        inventory = None
        if inventory_path:
            inventory = json.load(open(inventory_path))
        else:
            inventory = self.create_cloud_instances(instance_name)
        return inventory

    def expand_tests_configs(self):
        """Expands test configs with running configuration parameters."""
        tests = copy.deepcopy(self.tests)
        for name in self.tests:
            env_cfg = self.tests[name]["test_env_cfg"]
            tests[name]["running"] = cfg_renderer.get_running(self.tests[name]["_running_path"],
                                                              self.tests[name]["params"],
                                                              self.inventory,
                                                              env_cfg["clients"]["count"],
                                                              env_cfg["servers"]["count_per_group"])
        return tests

    def prepare_ansible_test_files(self):
        """Prepares ansible inventory and vars files for the tests."""
        # set global params for test suite
        if self.testsuite_params.get("_global"):
            ansible_manager.set_vars(vars_path=self._get_vars_path('test'),
                                     params=self.testsuite_params["_global"])

        for name, cfg in self.tests.items():
            groups = ansible_manager._get_groups_names(name)
            inventory_path = self.get_inventory_path(name)
            env = cfg["test_env_cfg"]

            ansible_manager.generate_inventory(inventory_path=inventory_path,
                                               clients_count=env["clients"]["count"],
                                               servers_per_group=env["servers"]["count_per_group"],
                                               groups=groups,
                                               instances_names=self.inventory,
                                               ssh_user=self.user)

            params = cfg["params"]
            if name in self.testsuite_params:
                params.update(self.testsuite_params[name])
            vars_path = self._get_vars_path(groups['test'])
            ansible_manager.set_vars(vars_path=vars_path, params=params)

    def install_elliptics_packages(self):
        """Installs elliptics packages on all servers and clients."""
        base_setup_playbook = "test-env-prepare"
        inventory_path = self.get_inventory_path(base_setup_playbook)
        groups = ansible_manager._get_groups_names("setup")

        ansible_manager.generate_inventory(inventory_path=inventory_path,
                                           clients_count=len(self.inventory['clients']),
                                           servers_per_group=[len(self.inventory['servers'])],
                                           groups=groups,
                                           instances_names=self.inventory,
                                           ssh_user=self.user)

        playbook = self.abspath(base_setup_playbook)
        ansible_manager.run_playbook(playbook, inventory_path)

    def generate_pytest_cfg(self, test_config):
        """Generates pytest.ini with test options."""
        pytest_config = ConfigParser.ConfigParser()
        pytest_config.add_section("pytest")
        pytest_config.set("pytest", "addopts", test_config["running"]["addopts"])

        self.logger.info("Test running options: {0}".format(test_config["running"]["addopts"]))
        with open("pytest.ini", "w") as config_file:
            pytest_config.write(config_file)

    def setup(self, test_name):
        test = self.tests[test_name]

        playbook = self.abspath(test["test_env_cfg"]["setup_playbook"])
        inventory = self.get_inventory_path(test_name)
        try:
            # Do prerequisite steps for a test
            ansible_manager.run_playbook(playbook, inventory)
        except ansible_manager.AnsiblePlaybookError:
            exc_info = traceback.format_exc()
            raise TestError("Setup for test {} raised exception: {}".format(test_name, exc_info))

        # Check if it's a pytest test
        if test["running"]["type"] == "pytest":
            self.generate_pytest_cfg(test)

        cfg_info = "Test environment configuration:\n\tclients: {0}\n\tservers per group: {1}"
        self.logger.info(cfg_info.format(test["test_env_cfg"]["clients"]["count"],
                                         test["test_env_cfg"]["servers"]["count_per_group"]))

    def run_playbook_test(self, test_name):
        try:
            ansible_manager.run_playbook(self.abspath(self.tests[test_name]["running"]["playbook"]),
                                         self.get_inventory_path(test_name))
        except ansible_manager.AnsiblePlaybookError as exc:
            self.logger.error(exc.message)
            return False
        return True

    def run_pytest_test(self, test_name):
        rsyncdir_opts = "--rsyncdir {0}/tests/ --rsyncdir {0}/lib/test_helper"
        rsyncdir_opts = rsyncdir_opts.format(self.project_dir)

        succeded = True
        clients_count = self.tests[test_name]["test_env_cfg"]["clients"]["count"]
        for client_name in self.inventory["clients"][:clients_count]:
            if self.teamcity:
                opts = '--teamcity'
            else:
                opts = ''
            opts += ' -d --tx ssh="{host} -l {user} -q" {rsyncdir_opts} {prj_dir}/tests/{target}/'

            opts = opts.format(host=client_name,
                               user=self.user,
                               rsyncdir_opts=rsyncdir_opts,
                               prj_dir=self.project_dir,
                               target=self.tests[test_name]["running"]["target"])
            self.logger.info(opts)

            exitcode = pytest.main(opts)
            if exitcode:
                succeded = False

        return succeded

    def run(self, test_name):
        if self.tests[test_name]["running"]["type"] == "ansible":
            return self.run_playbook_test(test_name)
        elif self.tests[test_name]["running"]["type"] == "pytest":
            return self.run_pytest_test(test_name)
        else:
            self.logger.info("Can't determine running method for {0} test.\n".format(test_name))
            return False

    def teardown(self, test_name):
        """Does clean-up steps after a test."""
        playbook = self.abspath(self.tests[test_name]["test_env_cfg"]["teardown_playbook"])
        inventory = self.get_inventory_path(test_name)
        try:
            ansible_manager.run_playbook(playbook, inventory)
        except ansible_manager.AnsiblePlaybookError:
            exc_info = traceback.format_exc()
            raise TestError("Teardown for test {} raised exception: {}".format(test_name, exc_info))

    def run_tests(self):
        testsfailed = 0
        for test_name in self.tests:
            with teamcity_messages.block("TEST: {0}".format(test_name)):
                self.setup(test_name)
                    
                if not self.run(test_name):
                    testsfailed += 1

                self.teardown(test_name)
        if testsfailed:
            return False
        else:
            return True

    def abspath(self, path):
        abs_path = os.path.join(self.ansible_dir, path)
        return abs_path

    def get_inventory_path(self, name):
        path = self.abspath("{0}.hosts".format(name))
        return path

    def _get_vars_path(self, name):
        path = self.abspath("group_vars/{0}.json".format(name))
        return path

if __name__ == "__main__":
    exitcode = EXIT_OK

    try:
        parser = argparse.ArgumentParser()

        parser.add_argument('--configs-dir', dest="configs_dir", required=True,
                            help="directory with tests' configs")
        parser.add_argument('--testsuite-params', dest="testsuite_params", default=None,
                            help="path to file with parameters which will override default "
                            "parameters for specified test suite.")
        parser.add_argument('--tag', action="append", dest="tags",
                            help="specifying which tests to run.")
        parser.add_argument('--verbose', '-v', action="store_true", dest="verbose",
                            help="increase verbosity")
        parser.add_argument('--teamcity', action="store_true", dest="teamcity",
                            help="will format output with Teamcity messages.")
        parser.add_argument('--user', default="root",
                            help="a user which will be used to connect via ssh to test machines.")

        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--inventory', help="path to inventory file.")
        group.add_argument('--instance-name', dest="instance_name", default="elliptics",
                           help="base name for the instances.")


        args = parser.parse_args()

        setup_loggers(args.teamcity, args.verbose)

        testrunner = TestRunner(args)
        if not testrunner.run_tests():
            exitcode = EXIT_TESTSFAILED

        # collect logs
        with teamcity_messages.block("LOGS: Collecting logs"):
            ansible_manager.run_playbook(testrunner.abspath("collect-logs"),
                                         testrunner.get_inventory_path("test-env-prepare"))

        if args.teamcity:
            with teamcity_messages.block("LOGS: Links"):
                path = "/tmp/logs-archive"
                for f in os.listdir(path):
                    print(qa_storage_upload(os.path.join(path, f)))
    except TestError:
        traceback.print_exc(file=sys.stderr)
        exitcode = EXIT_TESTSFAILED
    except:
        traceback.print_exc(file=sys.stderr)
        exitcode = EXIT_INTERNALERROR

    sys.exit(exitcode)
