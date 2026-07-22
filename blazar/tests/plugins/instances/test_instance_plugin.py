# Copyright (c) 2017 NTT.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
from unittest import mock
import uuid

import ddt
from novaclient import exceptions as nova_exceptions
from oslo_config import cfg
from oslo_config import fixture as conf_fixture
from oslo_utils import timeutils

from blazar import context
from blazar.db import api as db_api
from blazar.db import utils as db_utils
from blazar.manager import exceptions as mgr_exceptions
from blazar.plugins import instances
from blazar.plugins.instances import instance_plugin
from blazar.plugins import oshosts
from blazar import tests
from blazar.utils.openstack import nova

CONF = cfg.CONF


@ddt.ddt
class TestVirtualInstancePlugin(tests.TestCase):

    def setUp(self):
        super(TestVirtualInstancePlugin, self).setUp()

    def test_configuration(self):
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(os_admin_username='fake-user')
        self.cfg.config(os_admin_password='fake-passwd')
        self.cfg.config(os_admin_user_domain_name='fake-user-domain')
        self.cfg.config(os_admin_project_name='fake-pj-name')
        self.cfg.config(os_admin_project_domain_name='fake-pj-domain')
        plugin = instance_plugin.VirtualInstancePlugin()
        self.assertEqual("fake-user", plugin.username)
        self.assertEqual("fake-passwd", plugin.password)
        self.assertEqual("fake-user-domain", plugin.user_domain_name)
        self.assertEqual("fake-pj-name", plugin.project_name)
        self.assertEqual("fake-pj-domain", plugin.project_domain_name)

    def get_input_values(self, vcpus, memory, disk, amount, affinity,
                         start, end, lease_id, resource_properties):
        values = {'vcpus': vcpus, 'memory_mb': memory, 'disk_gb': disk,
                  'amount': amount, 'affinity': affinity, 'start_date': start,
                  'end_date': end, 'lease_id': lease_id,
                  'resource_properties': resource_properties}
        return values

    def generate_host_info(self, id, vcpus, memory, disk):
        return {'id': id, 'vcpus': vcpus,
                'memory_mb': memory, 'local_gb': disk}

    def generate_event(self, id, lease_id, event_type, time, status='UNDONE'):
        return {
            'id': id,
            'lease_id': lease_id,
            'event_type': event_type,
            'time': time,
            'status': status
            }

    def get_uuid(self):
        return str(uuid.uuid4())

    def generate_basic_events(self, lease_id, start, before_end, end):
        return [
            self.generate_event(self.get_uuid(), lease_id, 'start_lease',
                                datetime.datetime.strptime(start,
                                                           '%Y-%m-%d %H:%M')),
            self.generate_event(self.get_uuid(), lease_id, 'before_end_lease',
                                datetime.datetime.strptime(before_end,
                                                           '%Y-%m-%d %H:%M')),
            self.generate_event(self.get_uuid(), lease_id, 'end_lease',
                                datetime.datetime.strptime(end,
                                                           '%Y-%m-%d %H:%M')),
            ]

    def test_reserve_resource(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup_hosts = self.patch(plugin, 'pickup_hosts')
        mock_pickup_hosts.return_value = {'added': ['host1', 'host2'],
                                          'removed': []}

        mock_inst_create = self.patch(db_api, 'instance_reservation_create')
        fake_instance_reservation = {'id': 'instance-reservation-id1'}
        mock_inst_create.return_value = fake_instance_reservation

        mock_alloc_create = self.patch(db_api, 'host_allocation_create')

        mock_create_resources = self.patch(plugin, '_create_resources')
        mock_flavor = mock.MagicMock(id=1)
        mock_group = mock.MagicMock(id=2)
        mock_pool = mock.MagicMock(id=3)
        mock_create_resources.return_value = (mock_flavor,
                                              mock_group, mock_pool)

        mock_inst_update = self.patch(db_api, 'instance_reservation_update')

        inputs = self.get_input_values(2, 4018, 10, 1, False,
                                       '2030-01-01 08:00', '2030-01-01 08:00',
                                       'lease-1', '')

        expected_ret = 'instance-reservation-id1'

        ret = plugin.reserve_resource('res_id1', inputs)

        self.assertEqual(expected_ret, ret)
        pickup_hosts_value = {}
        for key in ['vcpus', 'memory_mb', 'disk_gb', 'amount', 'affinity',
                    'lease_id', 'start_date', 'end_date',
                    'resource_properties']:
            pickup_hosts_value[key] = inputs[key]
        mock_pickup_hosts.assert_called_once_with('res_id1',
                                                  pickup_hosts_value)

        mock_alloc_create.assert_any_call({'compute_host_id': 'host1',
                                           'reservation_id': 'res_id1'})
        mock_alloc_create.assert_any_call({'compute_host_id': 'host2',
                                           'reservation_id': 'res_id1'})
        mock_create_resources.assert_called_once_with(
            fake_instance_reservation)
        mock_inst_update.assert_called_once_with('instance-reservation-id1',
                                                 {'flavor_id': 1,
                                                  'server_group_id': 2,
                                                  'aggregate_id': 3})

    @ddt.data("abc", 2, "2")
    def test_affinity_error(self, value):
        plugin = instance_plugin.VirtualInstancePlugin()
        inputs = self.get_input_values(2, 4018, 10, 1, value,
                                       '2030-01-01 08:00', '2030-01-01 08:00',
                                       'lease-1', '')
        self.assertRaises(mgr_exceptions.MalformedParameter,
                          plugin.reserve_resource, 'reservation_id', inputs)
        self.assertRaises(mgr_exceptions.MalformedParameter,
                          plugin.update_reservation, 'reservation_id', inputs)

    @ddt.data(-1, 0, '0', 'one')
    def test_error_with_amount(self, value):
        plugin = instance_plugin.VirtualInstancePlugin()
        inputs = self.get_input_values(2, 4018, 10, value, False,
                                       '2030-01-01 08:00', '2030-01-01 08:00',
                                       'lease-1', '')
        self.assertRaises(mgr_exceptions.MalformedParameter,
                          plugin.reserve_resource, 'reservation_id', inputs)
        self.assertRaises(mgr_exceptions.MalformedParameter,
                          plugin.update_reservation, 'reservation_id', inputs)

    @ddt.data('vcpus', 'memory_mb', 'disk_gb', 'amount', 'affinity',
              'resource_properties')
    def test_create_reservation_with_missing_param(self, missing_param):
        plugin = instance_plugin.VirtualInstancePlugin()
        inputs = self.get_input_values(2, 4018, 10, 1, False,
                                       '2030-01-01 08:00', '2030-01-01 08:00',
                                       'lease-1', '')
        del inputs[missing_param]
        self.assertRaises(mgr_exceptions.MissingParameter,
                          plugin.reserve_resource, 'reservation_id', inputs)

    def test_filter_hosts_by_reservation_with_exclude(self):
        def fake_get_reservation_by_host(host_id, start, end):
            if host_id == 'host-1':
                return [
                    {'id': '1',
                     'resource_type': instances.RESOURCE_TYPE}]
            else:
                return []

        hosts_list = [self.generate_host_info('host-1', 4, 4096, 1000),
                      self.generate_host_info('host-2', 4, 4096, 1000),
                      self.generate_host_info('host-3', 4, 4096, 1000)]
        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host
        free = [{'host': hosts_list[0], 'reservations': []},
                {'host': hosts_list[1], 'reservations': []},
                {'host': hosts_list[2], 'reservations': []}]
        non_free = []

        plugin = instance_plugin.VirtualInstancePlugin()
        ret = plugin.filter_hosts_by_reservation(hosts_list,
                                                 '2030-01-01 08:00',
                                                 '2030-01-01 12:00', ['1'])
        self.assertEqual(free, ret[0])
        self.assertEqual(non_free, ret[1])

    def test_pickup_host_from_reserved_hosts(self):
        def fake_max_usages(host, reservations):
            if host['id'] == 'host-1':
                return 4, 4096, 2000
            else:
                return 0, 0, 0

        def fake_get_reservation_by_host(host_id, start, end):
            return [
                {'id': '1', 'resource_type': instances.RESOURCE_TYPE},
                {'id': '2', 'resource_type': instances.RESOURCE_TYPE}]

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 4, 4096, 1000),
                      self.generate_host_info('host-2', 4, 4096, 1000),
                      self.generate_host_info('host-3', 4, 4096, 1000)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host
        plugin.max_usages = fake_max_usages

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        values = {
            'vcpus': 1,
            'memory_mb': 1024,
            'disk_gb': 20,
            'amount': 2,
            'affinity': False,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }
        expected = {'added': ['host-2', 'host-3'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', values)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 1', 'memory_mb >= 1024', 'local_gb >= 20']
        mock_host_get_query.assert_called_once_with(expected_query)

    def test_pickup_host_from_free_hosts(self):
        def fake_get_reservation_by_host(host_id, start, end):
            return []

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 4, 4096, 1000),
                      self.generate_host_info('host-2', 4, 4096, 1000),
                      self.generate_host_info('host-3', 4, 4096, 1000)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')
        mock_get_reservations.side_effect = fake_get_reservation_by_host

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        values = {
            'vcpus': 1,
            'memory_mb': 1024,
            'disk_gb': 20,
            'amount': 2,
            'affinity': False,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00),
            }
        expected = {'added': ['host-1', 'host-2'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', values)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 1', 'memory_mb >= 1024', 'local_gb >= 20']
        mock_host_get_query.assert_called_once_with(expected_query)

    def test_pickup_host_from_free_and_reserved_host(self):
        def fake_get_reservation_by_host(host_id, start, end):
            if host_id in ['host-1', 'host-3']:
                return [
                    {'id': '1',
                     'resource_type': instances.RESOURCE_TYPE},
                    {'id': '2',
                     'resource_type': instances.RESOURCE_TYPE}
                    ]
            else:
                return []

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 4, 4096, 1000),
                      self.generate_host_info('host-2', 4, 4096, 1000),
                      self.generate_host_info('host-3', 4, 4096, 1000)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host

        mock_max_usages = self.patch(plugin, 'max_usages')
        mock_max_usages.return_value = (0, 0, 0)

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        params = {
            'vcpus': 1,
            'memory_mb': 1024,
            'disk_gb': 20,
            'amount': 2,
            'affinity': False,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }

        expected = {'added': ['host-1', 'host-3'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', params)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 1', 'memory_mb >= 1024', 'local_gb >= 20']
        mock_host_get_query.assert_called_once_with(expected_query)

    def test_pickup_host_with_affinity(self):
        def fake_get_reservation_by_host(host_id, start, end):
            if host_id in ['host-1', 'host-3']:
                return [
                    {'id': '1',
                     'resource_type': instances.RESOURCE_TYPE},
                    {'id': '2',
                     'resource_type': instances.RESOURCE_TYPE}
                    ]
            else:
                return []

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 8, 8192, 1000),
                      self.generate_host_info('host-2', 2, 2048, 500),
                      self.generate_host_info('host-3', 2, 2048, 500)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host

        mock_max_usages = self.patch(plugin, 'max_usages')
        mock_max_usages.return_value = (0, 0, 0)

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        params = {
            'vcpus': 2,
            'memory_mb': 2048,
            'disk_gb': 100,
            'amount': 2,
            'affinity': True,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }

        expected = {'added': ['host-1', 'host-1'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', params)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 2', 'memory_mb >= 2048', 'local_gb >= 100']
        mock_host_get_query.assert_called_once_with(expected_query)

    def test_pickup_host_with_anti_affinity(self):
        def fake_get_reservation_by_host(host_id, start, end):
            if host_id in ['host-1', 'host-3']:
                return [
                    {'id': '1',
                     'resource_type': instances.RESOURCE_TYPE},
                    {'id': '2',
                     'resource_type': instances.RESOURCE_TYPE}
                    ]
            else:
                return []

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 8, 8192, 1000),
                      self.generate_host_info('host-2', 2, 2048, 500)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host

        mock_max_usages = self.patch(plugin, 'max_usages')
        mock_max_usages.return_value = (0, 0, 0)

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        params = {
            'vcpus': 2,
            'memory_mb': 2048,
            'disk_gb': 100,
            'amount': 2,
            'affinity': False,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }

        expected = {'added': ['host-1', 'host-2'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', params)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 2', 'memory_mb >= 2048', 'local_gb >= 100']
        mock_host_get_query.assert_called_once_with(expected_query)

    @ddt.data('None', 'none', None)
    def test_pickup_host_with_no_affinity(self, value):
        def fake_get_reservation_by_host(host_id, start, end):
            return []

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 8, 8192, 1000),
                      self.generate_host_info('host-2', 2, 2048, 500),
                      self.generate_host_info('host-3', 2, 2048, 500)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host

        mock_max_usages = self.patch(plugin, 'max_usages')
        mock_max_usages.return_value = (0, 0, 0)

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = {
            'status': 'pending'
            }

        params = {
            'vcpus': 4,
            'memory_mb': 4096,
            'disk_gb': 200,
            'amount': 2,
            'affinity': value,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }

        expected = {'added': ['host-1', 'host-1'], 'removed': []}
        ret = plugin.pickup_hosts('reservation-id1', params)

        self.assertEqual(expected, ret)
        expected_query = ['vcpus >= 4', 'memory_mb >= 4096', 'local_gb >= 200']
        mock_host_get_query.assert_called_once_with(expected_query)

    def test_pickup_host_from_less_hosts(self):
        def fake_get_reservation_by_host(host_id, start, end):
            if host_id in ['host-1', 'host-3']:
                return [
                    {'id': '1',
                     'resource_type': oshosts.RESOURCE_TYPE},
                    {'id': '2',
                     'resource_type': instances.RESOURCE_TYPE}
                    ]
            else:
                return [
                    {'id': '1',
                     'resource_type': instances.RESOURCE_TYPE},
                    {'id': '2',
                     'resource_type': instances.RESOURCE_TYPE}
                    ]

        plugin = instance_plugin.VirtualInstancePlugin()

        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        hosts_list = [self.generate_host_info('host-1', 4, 4096, 1000),
                      self.generate_host_info('host-2', 4, 4096, 1000),
                      self.generate_host_info('host-3', 4, 4096, 1000)]
        mock_host_get_query.return_value = hosts_list

        mock_get_reservations = self.patch(db_utils,
                                           'get_reservations_by_host_id')

        mock_get_reservations.side_effect = fake_get_reservation_by_host
        mock_host_allocation_get = self.patch(
            db_api, 'host_allocation_get_all_by_values')
        mock_host_allocation_get.return_value = []

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False,
            'resource_properties': ''}
        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = old_reservation

        mock_lease_get = self.patch(db_api, 'lease_get')
        mock_lease_get.return_value = {'start_date': '2030-01-01 8:00',
                                       'end_date': '2030-01-01 12:00'}

        mock_max_usages = self.patch(plugin, 'max_usages')
        mock_max_usages.return_value = (1, 1024, 100)

        values = {
            'vcpus': 1,
            'memory_mb': 1024,
            'disk_gb': 20,
            'amount': 2,
            'affinity': False,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 00),
            'end_date': datetime.datetime(2030, 1, 1, 12, 00)
            }

        self.assertRaises(mgr_exceptions.NotEnoughHostsAvailable,
                          plugin.update_reservation, 'reservation-id1',
                          values)

    def test_max_usage_with_serial_reservation(self):
        def fake_event_get(sort_key, sort_dir, filters):
            if filters['lease_id'] == 'lease-1':
                return self.generate_basic_events('lease-1',
                                                  '2030-01-01 08:00',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 11:00')
            elif filters['lease_id'] == 'lease-2':
                return self.generate_basic_events('lease-2',
                                                  '2030-01-01 12:00',
                                                  '2030-01-01 13:00',
                                                  '2030-01-01 14:00')

        plugin = instance_plugin.VirtualInstancePlugin()
        reservations = [
            {
                'lease_id': 'lease-1',
                'instance_reservation': {
                    'vcpus': 2, 'memory_mb': 3072, 'disk_gb': 20}},
            {
                'lease_id': 'lease-2',
                'instance_reservation': {
                    'vcpus': 3, 'memory_mb': 2048, 'disk_gb': 30}}
            ]

        mock_event_get = self.patch(db_api, 'event_get_all_sorted_by_filters')
        mock_event_get.side_effect = fake_event_get

        expected = (3, 3072, 30)
        ret = plugin.max_usages('fake-host', reservations)

        self.assertEqual(expected, ret)

    def test_max_usage_with_parallel_reservation(self):
        def fake_event_get(sort_key, sort_dir, filters):
            if filters['lease_id'] == 'lease-1':
                return self.generate_basic_events('lease-1',
                                                  '2030-01-01 08:00',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 11:00')
            elif filters['lease_id'] == 'lease-2':
                return self.generate_basic_events('lease-2',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 13:00',
                                                  '2030-01-01 14:00')

        plugin = instance_plugin.VirtualInstancePlugin()
        reservations = [
            {
                'lease_id': 'lease-1',
                'instance_reservation': {
                    'vcpus': 2, 'memory_mb': 3072, 'disk_gb': 20}},
            {
                'lease_id': 'lease-2',
                'instance_reservation': {
                    'vcpus': 3, 'memory_mb': 2048, 'disk_gb': 30}},
            ]

        mock_event_get = self.patch(db_api, 'event_get_all_sorted_by_filters')
        mock_event_get.side_effect = fake_event_get

        expected = (5, 5120, 50)
        ret = plugin.max_usages('fake-host', reservations)

        self.assertEqual(expected, ret)

    def test_max_usage_with_multi_reservation(self):
        def fake_event_get(sort_key, sort_dir, filters):
            if filters['lease_id'] == 'lease-1':
                return self.generate_basic_events('lease-1',
                                                  '2030-01-01 08:00',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 11:00')

        plugin = instance_plugin.VirtualInstancePlugin()
        reservations = [
            {
                'lease_id': 'lease-1',
                'instance_reservation': {
                    'vcpus': 2, 'memory_mb': 3072, 'disk_gb': 20}},
            {
                'lease_id': 'lease-1',
                'instance_reservation': {
                    'vcpus': 3, 'memory_mb': 2048, 'disk_gb': 30}},
            ]

        mock_event_get = self.patch(db_api, 'event_get_all_sorted_by_filters')
        mock_event_get.side_effect = fake_event_get

        expected = (5, 5120, 50)
        ret = plugin.max_usages('fake-host', reservations)

        self.assertEqual(expected, ret)

    def test_max_usage_with_decrease_reservation(self):
        def fake_event_get(sort_key, sort_dir, filters):
            if filters['lease_id'] == 'lease-1':
                return self.generate_basic_events('lease-1',
                                                  '2030-01-01 08:00',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 11:00')
            elif filters['lease_id'] == 'lease-2':
                return self.generate_basic_events('lease-2',
                                                  '2030-01-01 10:00',
                                                  '2030-01-01 13:00',
                                                  '2030-01-01 14:00')
            elif filters['lease_id'] == 'lease-3':
                return self.generate_basic_events('lease-3',
                                                  '2030-01-01 15:00',
                                                  '2030-01-01 16:00',
                                                  '2030-01-01 17:00')

        plugin = instance_plugin.VirtualInstancePlugin()
        reservations = [
            {
                'lease_id': 'lease-1',
                'instance_reservation': {
                    'vcpus': 2, 'memory_mb': 3072, 'disk_gb': 20}},
            {
                'lease_id': 'lease-2',
                'instance_reservation': {
                    'vcpus': 1, 'memory_mb': 1024, 'disk_gb': 10}},
            {
                'lease_id': 'lease-3',
                'instance_reservation': {
                    'vcpus': 4, 'memory_mb': 2048, 'disk_gb': 40
                    }},
            ]

        mock_event_get = self.patch(db_api, 'event_get_all_sorted_by_filters')
        mock_event_get.side_effect = fake_event_get

        expected = (4, 4096, 40)
        ret = plugin.max_usages('fake-host', reservations)

        self.assertEqual(expected, ret)

    def test_create_resources(self):
        instance_reservation = {
            'reservation_id': 'reservation-id1',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 20,
            'affinity': False
            }

        plugin = instance_plugin.VirtualInstancePlugin()

        fake_client = mock.MagicMock()
        mock_nova_client = self.patch(nova, 'NovaClientWrapper')
        mock_nova_client.return_value = fake_client
        fake_server_group = mock.MagicMock(id='server_group_id1')
        fake_client.nova.server_groups.create.return_value = \
            fake_server_group

        self.set_context(context.BlazarContext(project_id='fake-project',
                                               auth_token='fake-token'))
        fake_flavor = mock.MagicMock(method='set_keys',
                                     flavorid='reservation-id1')
        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova
        mock_nova.nova.flavors.create.return_value = fake_flavor

        mock_create_reservation_class = self.patch(
            plugin.placement_client, 'create_reservation_class')

        fake_pool = mock.MagicMock(id='pool-id1')
        fake_agg = mock.MagicMock()
        fake_pool.create.return_value = fake_agg
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool

        expected = (fake_flavor, fake_server_group, fake_agg)

        ret = plugin._create_resources(instance_reservation)

        self.assertEqual(expected, ret)

        fake_client.nova.server_groups.create.assert_called_once_with(
            'reservation:reservation-id1', 'anti-affinity')
        mock_nova.nova.flavors.create.assert_called_once_with(
            flavorid='reservation-id1',
            name='reservation:reservation-id1',
            vcpus=2, ram=1024, disk=20, is_public=False)
        fake_flavor.set_keys.assert_called_once_with(
            {'aggregate_instance_extra_specs:reservation': 'reservation-id1',
             'affinity_id': 'server_group_id1',
             'resources:CUSTOM_RESERVATION_RESERVATION_ID1': '1'})
        fake_pool.create.assert_called_once_with(
            name='reservation-id1',
            metadata={'reservation': 'reservation-id1',
                      'filter_tenant_id': 'fake-project',
                      'affinity_id': 'server_group_id1'})
        mock_create_reservation_class.assert_called_once_with(
            'reservation-id1')

    def test_query_available_hosts(self):
        mock_host_get_query = self.patch(db_api,
                                         'reservable_host_get_all_by_queries')
        host1, host2, host3 = (self.generate_host_info(host_id, 4, 4096, 1000)
                               for host_id in ['host-1', 'host-2', 'host-3'])
        hosts_list = [host1, host2, host3]
        mock_host_get_query.return_value = hosts_list

        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_id')
        get_reservations.return_value = []

        plugin = instance_plugin.VirtualInstancePlugin()

        query_params = {
            'cpus': 1, 'memory': 1024, 'disk': 10,
            'resource_properties': '',
            'start_date': datetime.datetime(2020, 7, 7, 18, 0),
            'end_date': datetime.datetime(2020, 7, 7, 19, 0)
        }

        ret = plugin.query_available_hosts(**query_params)

        expected = [host1] * 4 + [host2] * 4 + [host3] * 4
        self.assertEqual(expected, ret)

    def test_pickup_hosts_for_update(self):
        reservation = {'id': 'reservation-id1', 'status': 'pending'}
        plugin = instance_plugin.VirtualInstancePlugin()

        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [
            {'compute_host_id': 'host-id1'}, {'compute_host_id': 'host-id2'},
            {'compute_host_id': 'host-id3'}]
        mock_query_available = self.patch(plugin, 'query_available_hosts')
        mock_query_available.return_value = [
            self.generate_host_info('host-id2', 2, 2024, 1000),
            self.generate_host_info('host-id3', 2, 2024, 1000),
            self.generate_host_info('host-id4', 2, 2024, 1000)]

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = reservation

        # case: new amount is less than old amount
        values = self.get_input_values(1, 1024, 10, 1, False,
                                       '2020-07-01 10:00', '2020-07-01 11:00',
                                       'lease-1', '')
        expect = {'added': [],
                  'removed': ['host-id1', 'host-id2', 'host-id3']}
        ret = plugin.pickup_hosts(reservation['id'], values)
        self.assertEqual(expect['added'], ret['added'])
        self.assertEqual(2, len(ret['removed']))
        self.assertTrue(all([h in expect['removed'] for h in ret['removed']]))
        query_params = {
            'cpus': 1, 'memory': 1024, 'disk': 10,
            'resource_properties': '',
            'start_date': '2020-07-01 10:00',
            'end_date': '2020-07-01 11:00',
            'excludes_res': ['reservation-id1']
            }
        mock_query_available.assert_called_with(**query_params)

        # case: new amount is same but change allocations
        values = self.get_input_values(1, 1024, 10, 3, False,
                                       '2020-07-01 10:00', '2020-07-01 11:00',
                                       'lease-1', '["==", "key1", "value1"]')
        expect = {'added': ['host-id4'], 'removed': ['host-id1']}
        ret = plugin.pickup_hosts(reservation['id'], values)
        self.assertEqual(expect['added'], ret['added'])
        self.assertEqual(expect['removed'], ret['removed'])
        query_params = {
            'cpus': 1, 'memory': 1024, 'disk': 10,
            'resource_properties': '["==", "key1", "value1"]',
            'start_date': '2020-07-01 10:00',
            'end_date': '2020-07-01 11:00',
            'excludes_res': ['reservation-id1']
            }
        mock_query_available.assert_called_with(**query_params)

        # case: new amount is greater than old amount
        host_ids = ('host-id1', 'host-id2', 'host-id3', 'host-id4')
        mock_query_available.return_value = [
            self.generate_host_info(host_id, 2, 2024, 1000)
            for host_id in host_ids]

        values = self.get_input_values(1, 1024, 10, 4, False,
                                       '2020-07-01 10:00', '2020-07-01 11:00',
                                       'lease-1', '')
        expect = {'added': ['host-id4'], 'removed': []}
        ret = plugin.pickup_hosts(reservation['id'], values)
        self.assertEqual(expect['added'], ret['added'])
        self.assertEqual(expect['removed'], ret['removed'])
        query_params = {
            'cpus': 1, 'memory': 1024, 'disk': 10,
            'resource_properties': '',
            'start_date': '2020-07-01 10:00',
            'end_date': '2020-07-01 11:00',
            'excludes_res': ['reservation-id1']
            }
        mock_query_available.assert_called_with(**query_params)

        # case: affinity is changed to True
        mock_query_available.return_value = [
            self.generate_host_info(host_id, 8, 8192, 1000)
            for host_id in host_ids * 8]

        values = self.get_input_values(1, 1024, 10, 4, True,
                                       '2020-07-01 10:00', '2020-07-01 11:00',
                                       'lease-1', '')
        ret = plugin.pickup_hosts(reservation['id'], values)

        # We don't care which host id (1-3) is picked up
        # Just make sure the same host is returned three times in "added"
        added = ret['added']
        self.assertEqual(3, len(added))
        self.assertEqual(1, len(set(added)))
        self.assertIn(added[0], ('host-id1', 'host-id2', 'host-id3'))

        # and make sure the other two hosts are removed
        removed = ret['removed']
        self.assertEqual(2, len(removed))
        self.assertEqual(2, len(set(removed)))
        expect_removed = set(host_ids) - set(added)
        for host_id in removed:
            self.assertIn(host_id, expect_removed)

        query_params = {
            'cpus': 1, 'memory': 1024, 'disk': 10,
            'resource_properties': '',
            'start_date': '2020-07-01 10:00',
            'end_date': '2020-07-01 11:00',
            'excludes_res': ['reservation-id1']
        }
        mock_query_available.assert_called_with(**query_params)

    def test_update_resources(self):
        reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'vcpus': 2, 'memory_mb': 1024,
            'disk_gb': 10, 'server_group_id': 'group-1'}
        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = reservation
        fake_client = mock.MagicMock()
        mock_nova_client = self.patch(nova, 'NovaClientWrapper')
        mock_nova_client.return_value = fake_client
        self.set_context(context.BlazarContext(project_id='fake-project',
                                               auth_token='fake-token'))
        plugin = instance_plugin.VirtualInstancePlugin()
        fake_flavor = mock.MagicMock(method='set_keys',
                                     flavorid='reservation-id1')
        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova
        mock_nova.nova.flavors.create.return_value = fake_flavor

        plugin.update_resources('reservation-id1')

        mock_reservation_get.assert_called_once_with('reservation-id1')
        mock_nova.nova.flavors.delete.assert_called_once_with(
            'reservation-id1')
        mock_nova.nova.flavors.create.assert_called_once_with(
            flavorid='reservation-id1',
            name='reservation:reservation-id1',
            vcpus=2, ram=1024, disk=10, is_public=False)
        fake_flavor.set_keys.assert_called_once_with(
            {'aggregate_instance_extra_specs:reservation': 'reservation-id1',
             'affinity_id': 'group-1',
             'resources:CUSTOM_RESERVATION_RESERVATION_ID1': '1'})

    def test_update_resources_in_active(self):
        def fake_host_get(host_id):
            return {'service_name': 'host' + host_id[-1],
                    'hypervisor_hostname': 'hypvsr' + host_id[-1]}

        reservation = {
            'id': 'reservation-id1',
            'status': 'active',
            'vcpus': 2, 'memory_mb': 1024,
            'disk_gb': 10, 'aggregate_id': 'aggregate-1'}

        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = reservation
        self.set_context(context.BlazarContext(project_id='fake-project'))
        plugin = instance_plugin.VirtualInstancePlugin()

        mock_update_reservation_inventory = self.patch(
            plugin.placement_client, 'update_reservation_inventory')

        fake_pool = mock.MagicMock()
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool

        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [
            {'compute_host_id': 'host-id1'}, {'compute_host_id': 'host-id2'},
            {'compute_host_id': 'host-id3'}, {'compute_host_id': 'host-id3'}]

        mock_host_get = self.patch(db_api, 'host_get')
        mock_host_get.side_effect = fake_host_get

        plugin.update_resources('reservation-id1')

        mock_reservation_get.assert_called_once_with('reservation-id1')
        for i in range(3):
            fake_pool.add_computehost.assert_any_call(
                'aggregate-1', 'host' + str(i + 1), stay_in=True)

        mock_update_reservation_inventory.assert_any_call(
            'hypvsr1', 'reservation-id1', 1)
        mock_update_reservation_inventory.assert_any_call(
            'hypvsr2', 'reservation-id1', 1)
        mock_update_reservation_inventory.assert_any_call(
            'hypvsr3', 'reservation-id1', 2)

    def test_update_reservation(self):
        plugin = instance_plugin.VirtualInstancePlugin()

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False,
            'resource_properties': ''}
        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = old_reservation

        mock_lease_get = self.patch(db_api, 'lease_get')
        mock_lease_get.return_value = {'start_date': '2020-07-07 18:00',
                                       'end_date': '2020-07-07 19:00'}

        mock_pickup_hosts = self.patch(plugin, 'pickup_hosts')
        mock_pickup_hosts.return_value = {
            'added': set(['host-id1']), 'removed': set(['host-id2'])}

        mock_inst_update = self.patch(db_api, 'instance_reservation_update')
        mock_inst_update.return_value = {
            'vcpus': 4, 'memory_mb': 1024, 'disk_gb': 200,
            'amount': 2, 'affinity': False}

        mock_update_alloc = self.patch(plugin, 'update_host_allocations')

        mock_update_resource = self.patch(plugin, 'update_resources')

        new_values = {'vcpus': 4, 'disk_gb': 200}
        plugin.update_reservation('reservation-id1', new_values)

        mock_pickup_hosts.assert_called_once_with(
            'reservation-id1',
            {'vcpus': 4, 'memory_mb': 1024, 'disk_gb': 200,
             'amount': 2, 'affinity': False, 'resource_properties': ''})
        mock_inst_update.assert_called_once_with(
            'instance-reservation-id1',
            {'vcpus': 4, 'memory_mb': 1024, 'disk_gb': 200,
             'amount': 2, 'affinity': False, 'resource_properties': ''})
        mock_update_alloc.assert_called_once_with(set(['host-id1']),
                                                  set(['host-id2']),
                                                  'reservation-id1')
        mock_update_resource.assert_called_once_with('reservation-id1')

    def test_update_reservation_reapplies_accel_constraints(self):
        # The update path rebuilds pickup_hosts values from a fixed key
        # list; the persisted accelerator constraints must be re-injected
        # or the pre-flight silently disappears on update.
        plugin = instance_plugin.VirtualInstancePlugin()

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False,
            'resource_properties': ''}
        self.patch(db_api, 'reservation_get').return_value = old_reservation
        self.patch(db_api, 'lease_get').return_value = {
            'start_date': '2020-07-07 18:00',
            'end_date': '2020-07-07 19:00'}
        mock_ir_get = self.patch(db_api, 'instance_reservation_get')
        mock_ir_get.return_value = {
            'accelerator_constraints': json.dumps({
                'accelerator_resources': {'PGPU': 1},
                'required_traits': [],
                'topology_locality': 'socket'})}

        mock_pickup_hosts = self.patch(plugin, 'pickup_hosts')
        mock_pickup_hosts.return_value = {
            'added': set(['host-id1']), 'removed': set()}
        mock_inst_update = self.patch(db_api, 'instance_reservation_update')
        self.patch(plugin, 'update_host_allocations')
        self.patch(plugin, 'update_resources')

        plugin.update_reservation('reservation-id1',
                                  {'vcpus': 4, 'disk_gb': 200})

        mock_ir_get.assert_called_once_with('instance-reservation-id1')
        values = mock_pickup_hosts.call_args[0][1]
        self.assertEqual({'PGPU': 1}, values['accelerator_resources'])
        self.assertEqual('socket', values['topology_locality'])
        # Empty list is falsy -> not injected.
        self.assertNotIn('required_traits', values)
        # The accel keys must not leak into the DB row update.
        updated = mock_inst_update.call_args[0][1]
        self.assertNotIn('accelerator_resources', updated)
        self.assertNotIn('topology_locality', updated)

    def test_update_reservation_caller_values_not_overwritten(self):
        # If the caller explicitly passes new accelerator kwargs, the
        # persisted blob must not clobber them.
        plugin = instance_plugin.VirtualInstancePlugin()

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False,
            'resource_properties': ''}
        self.patch(db_api, 'reservation_get').return_value = old_reservation
        self.patch(db_api, 'lease_get').return_value = {
            'start_date': '2020-07-07 18:00',
            'end_date': '2020-07-07 19:00'}
        self.patch(db_api, 'instance_reservation_get').return_value = {
            'accelerator_constraints': json.dumps({
                'accelerator_resources': {'PGPU': 1},
                'required_traits': ['CUSTOM_OLD'],
                'topology_locality': 'socket'})}

        mock_pickup_hosts = self.patch(plugin, 'pickup_hosts')
        mock_pickup_hosts.return_value = {
            'added': set(['host-id1']), 'removed': set()}
        self.patch(db_api, 'instance_reservation_update')
        self.patch(plugin, 'update_host_allocations')
        self.patch(plugin, 'update_resources')

        plugin.update_reservation(
            'reservation-id1',
            {'vcpus': 4, 'accelerator_resources': {'PGPU': 2}})

        values = mock_pickup_hosts.call_args[0][1]
        self.assertEqual({'PGPU': 2}, values['accelerator_resources'])
        self.assertEqual(['CUSTOM_OLD'], values['required_traits'])

    def test_select_host_reapplies_accel_constraints(self):
        # The heal path (_select_host) rebuilds values from a fixed spec
        # list; persisted accelerator constraints must be re-injected so
        # a lease is not healed onto a host lacking its accelerators.
        plugin = instance_plugin.VirtualInstancePlugin()

        reservation = {
            'id': 'reservation-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 1, 'affinity': False,
            'resource_properties': ''}
        lease = {'start_date': datetime.datetime(2030, 1, 1, 8, 0),
                 'end_date': datetime.datetime(2030, 1, 1, 12, 0)}
        self.patch(db_api, 'instance_reservation_get').return_value = {
            'accelerator_constraints': json.dumps({
                'accelerator_resources': {'PGPU': 1},
                'required_traits': ['CUSTOM_AMD_V620_VF'],
                'topology_locality': None})}
        mock_pickup_hosts = self.patch(plugin, 'pickup_hosts')
        mock_pickup_hosts.return_value = {'added': ['host-id9'],
                                          'removed': []}

        ret = plugin._select_host(reservation, lease)

        self.assertEqual('host-id9', ret)
        values = mock_pickup_hosts.call_args[0][1]
        self.assertEqual({'PGPU': 1}, values['accelerator_resources'])
        self.assertEqual(['CUSTOM_AMD_V620_VF'], values['required_traits'])
        # None is falsy -> not injected.
        self.assertNotIn('topology_locality', values)

    def test_update_reservation_not_enough_hosts(self):
        plugin = instance_plugin.VirtualInstancePlugin()

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'pending',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False,
            'resource_properties': ''}
        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = old_reservation

        mock_lease_get = self.patch(db_api, 'lease_get')
        mock_lease_get.return_value = {'start_date': '2020-07-07 18:00',
                                       'end_date': '2020-07-07 19:00'}

        # Mock that we have at least two hosts for (2 vcpus + 100 disk_gb),
        # but we have only one for (4 vcpus and 200 disk_gb)
        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [{'compute_host_id': 'host-id1'},
                                       {'compute_host_id': 'host-id2'}]

        mock_query_available = self.patch(plugin, 'query_available_hosts')
        mock_query_available.return_value = [
            self.generate_host_info('host-id1', 4, 2048, 1000)]

        new_values = {'vcpus': 4, 'disk_gb': 200,
                      'start_date': datetime.datetime(2020, 7, 7, 18, 0),
                      'end_date': datetime.datetime(2020, 7, 7, 19, 0),
                      'id': '00ee4f12-77c8-44d5-abca-06a543210a50'}
        self.assertRaises(mgr_exceptions.NotEnoughHostsAvailable,
                          plugin.update_reservation, 'reservation-id1',
                          new_values)

    def test_update_flavor_in_active(self):
        plugin = instance_plugin.VirtualInstancePlugin()

        old_reservation = {
            'id': 'reservation-id1',
            'status': 'active',
            'lease_id': 'lease-id1',
            'resource_id': 'instance-reservation-id1',
            'vcpus': 2, 'memory_mb': 1024, 'disk_gb': 100,
            'amount': 2, 'affinity': False}
        mock_reservation_get = self.patch(db_api, 'reservation_get')
        mock_reservation_get.return_value = old_reservation

        mock_lease_get = self.patch(db_api, 'lease_get')
        mock_lease_get.return_value = {'start_date': '2020-07-07 18:00',
                                       'end_date': '2020-07-07 19:00'}

        new_values = {'vcpus': 4, 'disk_gb': 200}
        self.assertRaises(mgr_exceptions.InvalidStateUpdate,
                          plugin.update_reservation,
                          'reservation-id1', new_values)

    def test_update_host_allocations(self):
        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [
            {'id': 'id10', 'compute_host_id': 'host-id10'},
            {'id': 'id11', 'compute_host_id': 'host-id11'},
            {'id': 'id12', 'compute_host_id': 'host-id11'},
            {'id': 'id13', 'compute_host_id': 'host-id11'},
            {'id': 'id14', 'compute_host_id': 'host-id12'}]

        mock_alloc_destroy = self.patch(db_api, 'host_allocation_destroy')
        mock_alloc_create = self.patch(db_api, 'host_allocation_create')

        plugin = instance_plugin.VirtualInstancePlugin()

        added_host = ['host-id1', 'host-id1', 'host-id2']
        removed_host = ['host-id10', 'host-id11', 'host-id11']

        plugin.update_host_allocations(added_host, removed_host,
                                       'reservation-id1')

        removed_calls = [mock.call('id10'), mock.call('id11')]
        mock_alloc_destroy.assert_has_calls(removed_calls)
        self.assertEqual(3, mock_alloc_destroy.call_count)

        added_calls = [
            mock.call({'compute_host_id': 'host-id1',
                       'reservation_id': 'reservation-id1'}),
            mock.call({'compute_host_id': 'host-id2',
                       'reservation_id': 'reservation-id1'})]
        mock_alloc_create.assert_has_calls(added_calls)
        self.assertEqual(3, mock_alloc_create.call_count)

    def test_on_start(self):
        def fake_host_get(host_id):
            return {'service_name': 'host' + host_id[-1],
                    'hypervisor_hostname': 'hypvsr' + host_id[-1]}

        self.set_context(context.BlazarContext(project_id='fake-project'))
        plugin = instance_plugin.VirtualInstancePlugin()

        mock_inst_get = self.patch(db_api, 'instance_reservation_get')
        mock_inst_get.return_value = {'reservation_id': 'reservation-id1',
                                      'aggregate_id': 'aggregate-id1'}

        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova

        fake_pool = mock.MagicMock()
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool

        mock_update_reservation_inventory = self.patch(
            plugin.placement_client, 'update_reservation_inventory')

        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [
            {'compute_host_id': 'host-id1'}, {'compute_host_id': 'host-id2'},
            {'compute_host_id': 'host-id3'}, {'compute_host_id': 'host-id3'}]

        mock_host_get = self.patch(db_api, 'host_get')
        mock_host_get.side_effect = fake_host_get

        plugin.on_start('resource-id1')

        mock_nova.flavor_access.add_tenant_access.assert_called_once_with(
            'reservation-id1', 'fake-project')
        for i in range(3):
            fake_pool.add_computehost.assert_any_call(
                'aggregate-id1', 'host' + str(i + 1), stay_in=True)

        mock_update_reservation_inventory.assert_any_call(
            'hypvsr1', 'reservation-id1', 1)
        mock_update_reservation_inventory.assert_any_call(
            'hypvsr2', 'reservation-id1', 1)
        mock_update_reservation_inventory.assert_any_call(
            'hypvsr3', 'reservation-id1', 2)

    def test_on_end(self):
        self.set_context(context.BlazarContext(project_id='fake-project-id'))

        plugin = instance_plugin.VirtualInstancePlugin()

        fake_instance_reservation = {'reservation_id': 'reservation-id1'}
        mock_inst_get = self.patch(db_api, 'instance_reservation_get')
        mock_inst_get.return_value = fake_instance_reservation

        mock_alloc_get = self.patch(db_api,
                                    'host_allocation_get_all_by_values')
        mock_alloc_get.return_value = [{'id': 'host-alloc-id1',
                                        'compute_host_id': 'host-id1'},
                                       {'id': 'host-alloc-id2',
                                        'compute_host_id': 'host-id2'}]

        mock_host_get = self.patch(db_api, 'host_get')
        mock_host_get.side_effect = [
            {'service_name': 'host1', 'hypervisor_hostname': 'hypvsr1'},
            {'service_name': 'host2', 'hypervisor_hostname': 'hypvsr2'}
        ]

        mock_delete_reservation_inventory = self.patch(
            plugin.placement_client, 'delete_reservation_inventory')
        mock_delete_reservation_class = self.patch(
            plugin.placement_client, 'delete_reservation_class')

        self.patch(db_api, 'host_allocation_destroy')

        fake_servers = [mock.MagicMock() for i in range(5)]
        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova
        # First, we return the fake servers to delete. Second, on the check in
        # _check_server_deletion(), we mock they are still in nova DB to
        # exercise retry and at last we mock they are deleted completely.
        mock_nova.servers.list.side_effect = [fake_servers, fake_servers, []]

        mock_cleanup_resources = self.patch(plugin, 'cleanup_resources')

        mock_log = self.patch(instance_plugin, 'LOG')
        mock_nova.servers.delete.side_effect = [nova_exceptions.NotFound(
            404, "The server doesn't exist in Nova"), Exception('Unknown'),
            None, None, None]

        plugin.on_end('resource-id1')

        mock_nova.flavor_access.remove_tenant_access.assert_called_once_with(
            'reservation-id1', 'fake-project-id')

        mock_nova.servers.list.assert_called_with(
            search_opts={'flavor': 'reservation-id1', 'all_tenants': 1},
            detailed=False)
        mock_nova.servers.list.call_count = 3
        self.assertEqual(5, mock_nova.servers.delete.call_count)
        mock_log.info.assert_any_call(
            "Could not find server '%s', may have been deleted concurrently.",
            fake_servers[0].id)
        mock_log.exception.assert_called_with(
            "Failed to delete server '%s': %s.", fake_servers[1].id, 'Unknown')
        for i in range(2):
            mock_delete_reservation_inventory.assert_any_call(
                'hypvsr' + str(i + 1), 'reservation-id1')
        mock_cleanup_resources.assert_called_once_with(
            fake_instance_reservation)
        mock_delete_reservation_class.assert_called_once_with(
            'reservation-id1')

    def test_heal_reservations_before_start_and_resources_changed(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_ids')
        get_reservations.return_value = [dummy_reservation]
        heal_reservation = self.patch(plugin, '_heal_reservation')
        heal_reservation.return_value = True

        result = plugin.heal_reservations(
            [failed_host],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        heal_reservation.assert_called_once_with(
            dummy_reservation, list(failed_host.values()))
        self.assertEqual({}, result)

    def test_heal_reservations_before_start_and_missing_resources(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_ids')
        get_reservations.return_value = [dummy_reservation]
        heal_reservation = self.patch(plugin, '_heal_reservation')
        heal_reservation.return_value = False

        result = plugin.heal_reservations(
            [failed_host],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        heal_reservation.assert_called_once_with(
            dummy_reservation, list(failed_host.values()))
        self.assertEqual(
            {dummy_reservation['id']: {'missing_resources': True}},
            result)

    def test_heal_active_reservations_and_resources_changed(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_ids')
        get_reservations.return_value = [dummy_reservation]
        heal_reservation = self.patch(plugin, '_heal_reservation')
        heal_reservation.return_value = True

        result = plugin.heal_reservations(
            [failed_host],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        heal_reservation.assert_called_once_with(
            dummy_reservation, list(failed_host.values()))
        self.assertEqual(
            {dummy_reservation['id']: {'resources_changed': True}},
            result)

    def test_heal_active_reservations_and_missing_resources(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'
            }]
        }
        get_reservations = self.patch(db_utils,
                                      'get_reservations_by_host_ids')
        get_reservations.return_value = [dummy_reservation]
        heal_reservation = self.patch(plugin, '_heal_reservation')
        heal_reservation.return_value = False

        result = plugin.heal_reservations(
            [failed_host],
            datetime.datetime(2020, 1, 1, 12, 00),
            datetime.datetime(2020, 1, 1, 13, 00))
        heal_reservation.assert_called_once_with(
            dummy_reservation, list(failed_host.values()))
        self.assertEqual(
            {dummy_reservation['id']: {'missing_resources': True}},
            result)

    def test_reallocate_before_start(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        new_host = {'id': '2'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'}]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.return_value = {'added': [new_host['id']], 'removed': []}
        alloc_update = self.patch(db_api, 'host_allocation_update')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 11, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        pickup_hosts.assert_called_once()
        alloc_update.assert_called_once_with(
            'alloc-1', {'compute_host_id': new_host['id']})
        self.assertEqual(True, result)

    def test_reallocate_active(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1',
                       'service_name': 'compute-1',
                       'hypervisor_hostname': 'hypvsr-1'}
        new_host = {'id': '2',
                    'service_name': 'compute-2',
                    'hypervisor_hostname': 'hypvsr-2'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'}]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        reservation_get = self.patch(db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        host_get = self.patch(db_api, 'host_get')
        host_get.side_effect = [failed_host, new_host]
        fake_pool = mock.MagicMock()
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.return_value = {'added': [new_host['id']], 'removed': []}
        alloc_update = self.patch(db_api, 'host_allocation_update')
        mock_delete_reservation_inventory = self.patch(
            plugin.placement_client, 'delete_reservation_inventory')
        mock_update_reservation_inventory = self.patch(
            plugin.placement_client, 'update_reservation_inventory')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 13, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        fake_pool.remove_computehost.assert_called_once_with(
            dummy_reservation['aggregate_id'],
            failed_host['service_name'])
        pickup_hosts.assert_called_once()
        alloc_update.assert_called_once_with(
            'alloc-1', {'compute_host_id': new_host['id']})
        fake_pool.add_computehost.assert_called_once_with(
            dummy_reservation['aggregate_id'],
            new_host['service_name'],
            stay_in=True)
        mock_delete_reservation_inventory.assert_called_once_with(
            'hypvsr-1', 'rsrv-1')
        mock_update_reservation_inventory.assert_called_once_with(
            'hypvsr-2', 'rsrv-1', 1, additional=True)
        self.assertEqual(True, result)

    def test_reallocate_missing_resources(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1',
                       'service_name': 'compute-1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': False,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [{
                'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                'reservation_id': 'rsrv-1'}]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        reservation_get = self.patch(db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.side_effect = mgr_exceptions.NotEnoughHostsAvailable
        alloc_destroy = self.patch(db_api, 'host_allocation_destroy')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 11, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        pickup_hosts.assert_called_once()
        alloc_destroy.assert_called_once_with('alloc-1')
        self.assertEqual(False, result)

    def test_reallocate_before_start_affinity(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1'}
        new_host = {'id': '2'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': True,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [
                {'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
                {'id': 'alloc-2', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
            ]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.return_value = {'added': [new_host['id']], 'removed': []}
        alloc_update = self.patch(db_api, 'host_allocation_update')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 11, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        pickup_hosts.assert_called_once()
        update_calls = [mock.call('alloc-1', {'compute_host_id': '2'}),
                        mock.call('alloc-2', {'compute_host_id': '2'})]
        alloc_update.assert_has_calls(update_calls)
        self.assertEqual(True, result)

    def test_reallocate_active_affinity(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1',
                       'service_name': 'compute-1',
                       'hypervisor_hostname': 'hypvsr-1'}
        new_host = {'id': '2',
                    'service_name': 'compute-2',
                    'hypervisor_hostname': 'hypvsr-2'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'active',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': True,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [
                {'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
                {'id': 'alloc-2', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
            ]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        reservation_get = self.patch(db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        host_get = self.patch(db_api, 'host_get')
        host_get.side_effect = [failed_host, new_host]
        fake_pool = mock.MagicMock()
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.return_value = {'added': [new_host['id']], 'removed': []}
        alloc_update = self.patch(db_api, 'host_allocation_update')
        mock_delete_reservation_inventory = self.patch(
            plugin.placement_client, 'delete_reservation_inventory')
        mock_update_reservation_inventory = self.patch(
            plugin.placement_client, 'update_reservation_inventory')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 13, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        fake_pool.remove_computehost.assert_called_once_with(
            dummy_reservation['aggregate_id'],
            failed_host['service_name'])
        pickup_hosts.assert_called_once()
        update_calls = [mock.call('alloc-1', {'compute_host_id': '2'}),
                        mock.call('alloc-2', {'compute_host_id': '2'})]
        alloc_update.assert_has_calls(update_calls)
        fake_pool.add_computehost.assert_called_once_with(
            dummy_reservation['aggregate_id'],
            new_host['service_name'],
            stay_in=True)
        mock_delete_reservation_inventory.assert_called_once_with(
            'hypvsr-1', 'rsrv-1')
        mock_update_reservation_inventory.assert_called_once_with(
            'hypvsr-2', 'rsrv-1', 2, additional=True)
        self.assertEqual(True, result)

    def test_reallocate_missing_resources_with_affinity(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        failed_host = {'id': '1',
                       'service_name': 'compute-1'}
        dummy_reservation = {
            'id': 'rsrv-1',
            'resource_type': instances.RESOURCE_TYPE,
            'lease_id': 'lease-1',
            'status': 'pending',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 256,
            'aggregate_id': 'agg-1',
            'affinity': True,
            'amount': 3,
            'resource_properties': '',
            'computehost_allocations': [
                {'id': 'alloc-1', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
                {'id': 'alloc-2', 'compute_host_id': failed_host['id'],
                 'reservation_id': 'rsrv-1'},
            ]
        }
        dummy_lease = {
            'name': 'lease-name',
            'start_date': datetime.datetime(2020, 1, 1, 12, 00),
            'end_date': datetime.datetime(2020, 1, 2, 12, 00),
            'trust_id': 'trust-1'
        }
        reservation_get = self.patch(db_api, 'reservation_get')
        reservation_get.return_value = dummy_reservation
        lease_get = self.patch(db_api, 'lease_get')
        lease_get.return_value = dummy_lease
        pickup_hosts = self.patch(plugin, 'pickup_hosts')
        pickup_hosts.side_effect = mgr_exceptions.NotEnoughHostsAvailable
        alloc_destroy = self.patch(db_api, 'host_allocation_destroy')

        with mock.patch.object(timeutils, 'utcnow') as patched:
            patched.return_value = datetime.datetime(2020, 1, 1, 11, 00)
            result = plugin._heal_reservation(
                dummy_reservation, list(failed_host.values()))

        pickup_hosts.assert_called_once()
        destroy_calls = [mock.call('alloc-1'), mock.call('alloc-2')]
        alloc_destroy.assert_has_calls(destroy_calls)
        self.assertEqual(False, result)

    @ddt.data(False, True, None)
    def test_cleanup_resources(self, affinity):
        instance_reservation = {
            'reservation_id': 'reservation-id1',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 20,
            'affinity': affinity
        }

        # Set server_group_id according to the affinity value
        server_group_id = 'group-1' if affinity is not None else None
        instance_reservation['server_group_id'] = server_group_id

        mock_nova_client = self.patch(nova, 'NovaClientWrapper')
        mock_nova_client.return_value = mock.MagicMock()
        mock_nova_pool = self.patch(nova, 'ReservationPool')
        mock_nova_pool.return_value = mock.MagicMock()
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova

        plugin.cleanup_resources(instance_reservation)

        if affinity is not None:
            mock_nova.nova.server_groups.delete.assert_called_once_with(
                'group-1')
        mock_nova.nova.flavors.delete.assert_called_once_with(
            'reservation-id1')


class FakePlacementClient(object):
    """In-memory stand-in for BlazarPlacementClient.

    Each test arranges hosts as dicts mapping hostname -> {
        'subtree_traits': set of trait strings present somewhere in tree,
        'anchors': set of anchor trait strings present in tree,
        'inventory': dict resource_class -> {'total', 'used'},
        'tree_present': True iff host_rp exists,
    }.
    """

    def __init__(self, hosts):
        self._hosts = hosts
        self.calls = []

    def get_host_rp_tree(self, host_name):
        self.calls.append(('tree', host_name))
        if not self._hosts.get(host_name, {}).get('tree_present', True):
            return {}
        return {'root': {'name': host_name, 'traits': [], 'inventory': {}}}

    def get_accelerator_inventory_for_host(self, host_name,
                                            resource_classes,
                                            rp_tree=None):
        inv = self._hosts.get(host_name, {}).get('inventory', {})
        result = {}
        for rc in resource_classes:
            result[rc] = inv.get(rc, {'total': 0, 'used': 0})
        return result

    def host_has_anchor(self, host_name, anchor_trait, rp_tree=None):
        return anchor_trait in self._hosts.get(
            host_name, {}).get('anchors', set())

    def host_subtree_has_traits(self, host_name, required_traits,
                                rp_tree=None):
        if not required_traits:
            return True
        return set(required_traits).issubset(
            self._hosts.get(host_name, {}).get('subtree_traits', set()))


class TestTopologyAwareQuery(tests.TestCase):
    """Tests for query_available_hosts with accelerator_resources,
    required_traits, and topology_locality kwargs."""

    def setUp(self):
        super(TestTopologyAwareQuery, self).setUp()

    def _build_plugin(self, hosts, placement_state):
        """Wire up a VirtualInstancePlugin with mocked DB and a fake
        placement client.

        :param hosts: list of host-row dicts to be returned by
            reservable_host_get_all_by_queries.
        :param placement_state: dict host_name -> per-host placement
            state for FakePlacementClient.
        """
        mock_host_get_query = self.patch(
            db_api, 'reservable_host_get_all_by_queries')
        mock_host_get_query.return_value = hosts
        self.patch(db_utils, 'get_reservations_by_host_id').return_value = []

        plugin = instance_plugin.VirtualInstancePlugin()
        plugin.placement_client = FakePlacementClient(placement_state)
        return plugin

    def _h(self, host_id, name='compute-01'):
        # Default host row Blazar's DB returns.
        return {'id': host_id, 'hypervisor_hostname': name,
                'vcpus': 16, 'memory_mb': 65536, 'local_gb': 1000}

    def _query(self, plugin, **overrides):
        kwargs = {
            'cpus': 4,
            'memory': 8192,
            'disk': 20,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 0),
            'end_date': datetime.datetime(2030, 1, 1, 12, 0),
        }
        kwargs.update(overrides)
        return plugin.query_available_hosts(**kwargs)

    # ---- defaults preserve old behavior --------------------------------

    def test_no_accelerator_kwargs_preserves_old_behavior(self):
        # Arrange: zero accelerator inventory but no accelerator constraint.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {}, 'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin)
        # Assert: host fits cpu/mem/disk; returned multiple times because
        # the bin-pack allows multiple instances.
        self.assertIn(host, ret)
        # And no placement calls happened.
        self.assertEqual([], plugin.placement_client.calls)

    # ---- accelerator inventory ----------------------------------------

    def test_positive_host_with_enough_pgpu_accepted(self):
        # Arrange
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        # Assert
        self.assertIn(host, ret)

    def test_filter_rejects_host_with_zero_pgpu(self):
        # Arrange
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 0, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        # Assert
        self.assertEqual([], ret)

    def test_boundary_exact_match_pgpu_accepted(self):
        # Arrange: host has exactly 1 PGPU, reservation needs exactly 1.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 1, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        # Assert
        self.assertIn(host, ret)

    def test_over_allocation_rejected(self):
        # Arrange: host has 2 PGPU, reservation asks 3.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 3})
        # Assert
        self.assertEqual([], ret)

    def test_used_inventory_subtracted_from_effective(self):
        # Arrange: host has total=2 used=1 (Nova has allocated 1).
        # Reservation for 2 must fail.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 1}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 2})
        # Assert
        self.assertEqual([], ret)

    def test_trait_mismatch_rejects(self):
        # Arrange: host has PGPU but no CUSTOM_AMD_V620_VF trait.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': set(),
                'subtree_traits': {'PGPU_FLAG'},
            }})
        # Act
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 1},
                          required_traits=['CUSTOM_AMD_V620_VF'])
        # Assert
        self.assertEqual([], ret)

    def test_required_traits_present_accepted(self):
        # Arrange
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': set(),
                'subtree_traits': {'CUSTOM_AMD_V620_VF',
                                    'CUSTOM_AMD_V620'},
            }})
        # Act
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 1},
                          required_traits=['CUSTOM_AMD_V620_VF'])
        # Assert
        self.assertIn(host, ret)

    def test_topology_anchor_missing_rejects(self):
        # Arrange: locality=socket required but no CUSTOM_SOCKET_ROOT.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': set(),
                'subtree_traits': set(),
            }})
        # Act
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 1},
                          topology_locality='socket')
        # Assert
        self.assertEqual([], ret)

    def test_topology_anchor_present_accepted(self):
        # Arrange
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': {'CUSTOM_SOCKET_ROOT'},
                'subtree_traits': set(),
            }})
        # Act
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 1},
                          topology_locality='socket')
        # Assert
        self.assertIn(host, ret)

    def test_topology_numa_anchor_uses_hw_numa_root(self):
        # Arrange: locality=numa expects HW_NUMA_ROOT, not socket.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': {'CUSTOM_SOCKET_ROOT'},  # socket only
                'subtree_traits': set(),
            }})
        # Act
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 1},
                          topology_locality='numa')
        # Assert: needs HW_NUMA_ROOT, not present -> rejected
        self.assertEqual([], ret)

    def test_missing_input_preserves_baseline(self):
        # Arrange: explicit None inputs must behave like absent.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {}, 'anchors': set(),
                            'subtree_traits': set()}})
        # Act
        ret = self._query(plugin,
                          accelerator_resources=None,
                          required_traits=None,
                          topology_locality=None)
        # Assert
        self.assertIn(host, ret)

    def test_already_reserved_host_pgpu_count_subtracted(self):
        # Arrange: host has 2 PGPU total, no placement usage, but another
        # Blazar reservation in the window already committed 1 PGPU.
        # Effective availability is 1; reservation for 2 must fail.
        host = self._h('h1', 'compute-01')
        other_reservation = {
            'id': 'r-other', 'lease_id': 'l-other',
            'resource_type': instances.RESOURCE_TYPE,
            'resource_id': 'ir-other',
            'instance_reservation': {
                'amount': 1,
                'accelerator_constraints': '{"accelerator_resources": '
                                           '{"PGPU": 1}, '
                                           '"required_traits": [], '
                                           '"topology_locality": null}',
            },
        }
        self.patch(db_api, 'reservable_host_get_all_by_queries').return_value\
            = [host]

        def fake_resv_by_host(host_id, start, end):
            return [other_reservation]

        self.patch(db_utils, 'get_reservations_by_host_id').side_effect = (
            fake_resv_by_host)
        # One allocation row = one instance slot held on this host.
        self.patch(db_api,
                   'host_allocation_get_all_by_values').return_value = (
            [{'id': 'alloc-1'}])

        plugin = instance_plugin.VirtualInstancePlugin()
        plugin.placement_client = FakePlacementClient({
            'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': set(),
                'subtree_traits': set(),
            }})

        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 2})

        # Assert
        self.assertEqual([], ret)

    def test_already_reserved_excluded_by_excludes_res(self):
        # Arrange: an overlapping reservation exists, but its id is in
        # excludes_res (which is what update-reservation does). Its
        # PGPU commit should not subtract.
        host = self._h('h1', 'compute-01')
        other_reservation = {
            'id': 'r-other', 'lease_id': 'l-other',
            'resource_type': instances.RESOURCE_TYPE,
            'resource_id': 'ir-other',
            'instance_reservation': {
                'amount': 1,
                'accelerator_constraints': '{"accelerator_resources": '
                                           '{"PGPU": 1}, '
                                           '"required_traits": [], '
                                           '"topology_locality": null}',
            },
        }
        self.patch(db_api, 'reservable_host_get_all_by_queries').return_value\
            = [host]
        self.patch(db_utils, 'get_reservations_by_host_id').return_value = (
            [other_reservation])

        plugin = instance_plugin.VirtualInstancePlugin()
        plugin.placement_client = FakePlacementClient({
            'compute-01': {
                'inventory': {'PGPU': {'total': 2, 'used': 0}},
                'anchors': set(),
                'subtree_traits': set(),
            }})

        # Act: ask for the full 2 PGPU but exclude the other reservation.
        ret = self._query(plugin,
                          accelerator_resources={'PGPU': 2},
                          excludes_res=['r-other'])
        # Assert
        self.assertIn(host, ret)

    @staticmethod
    def _multi_host_reservation(amount):
        # A reservation spanning several hosts: ``amount`` is
        # reservation-wide, per-host share comes from allocation rows.
        return {
            'id': 'r-multi', 'lease_id': 'l-multi',
            'resource_type': instances.RESOURCE_TYPE,
            'resource_id': 'ir-multi',
            'instance_reservation': {
                'amount': amount,
                'accelerator_constraints': '{"accelerator_resources": '
                                           '{"PGPU": 1}, '
                                           '"required_traits": [], '
                                           '"topology_locality": null}',
            },
        }

    def _arrange_committed(self, total_pgpu, other_reservation):
        host = self._h('h1', 'compute-01')
        self.patch(db_api, 'reservable_host_get_all_by_queries').return_value\
            = [host]
        self.patch(db_utils, 'get_reservations_by_host_id').return_value = (
            [other_reservation])
        # max_usages (cpu/mem/disk bin-packing) walks the overlapping
        # reservation's lease events; nothing to replay here.
        self.patch(db_api,
                   'event_get_all_sorted_by_filters').return_value = []
        plugin = instance_plugin.VirtualInstancePlugin()
        plugin.placement_client = FakePlacementClient({
            'compute-01': {
                'inventory': {'PGPU': {'total': total_pgpu, 'used': 0}},
                'anchors': set(),
                'subtree_traits': set(),
            }})
        return host, plugin

    def test_committed_counts_per_host_allocation_slots(self):
        # Arrange: reservation-wide amount=10 but only 2 instance slots
        # are allocated on *this* host. Host has 3 PGPU; commit must be
        # 1 PGPU x 2 slots = 2, leaving 1 for a new reservation.
        # (Charging 1 x 10 against this host would wrongly reject it.)
        host, plugin = self._arrange_committed(
            3, self._multi_host_reservation(amount=10))
        mock_alloc = self.patch(db_api, 'host_allocation_get_all_by_values')
        mock_alloc.return_value = [{'id': 'a1'}, {'id': 'a2'}]

        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})

        # Assert
        self.assertIn(host, ret)
        mock_alloc.assert_any_call(reservation_id='r-multi',
                                   compute_host_id='h1')

    def test_committed_zero_slots_on_host_not_charged(self):
        # Arrange: the overlapping reservation holds all its slots on
        # *other* hosts (no allocation rows here) -> commits nothing.
        host, plugin = self._arrange_committed(
            1, self._multi_host_reservation(amount=10))
        self.patch(db_api,
                   'host_allocation_get_all_by_values').return_value = []

        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})

        # Assert
        self.assertIn(host, ret)

    def test_committed_falls_back_to_amount_on_alloc_read_failure(self):
        # Arrange: allocation rows unreadable -> conservative fallback
        # to the reservation-wide amount (2), consuming both PGPUs.
        host, plugin = self._arrange_committed(
            2, self._multi_host_reservation(amount=2))
        self.patch(db_api,
                   'host_allocation_get_all_by_values').side_effect = (
            RuntimeError('db down'))

        # Act
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})

        # Assert
        self.assertEqual([], ret)

    def test_accelerator_aware_disabled_passes_through(self):
        # Arrange: turn off the feature entirely via config.
        from oslo_config import cfg as oslo_cfg  # noqa: re-import for clarity
        from oslo_config import fixture as conf_fixture
        self.cfg = self.useFixture(conf_fixture.Config(oslo_cfg.CONF))
        self.cfg.config(group='scheduler', accelerator_aware=False)

        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 0, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        # Act: even with PGPU=1 required, feature-disabled means we just
        # use the original cpu/mem/disk filter.
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        # Assert
        self.assertIn(host, ret)


class TestReserveResourceWithFlavor(tests.TestCase):
    """Tests for the flavor_id-based code path in reserve_resource."""

    def setUp(self):
        super(TestReserveResourceWithFlavor, self).setUp()

    def _patch_db(self):
        self.mock_inst_create = self.patch(db_api,
                                            'instance_reservation_create')
        self.mock_inst_create.return_value = {
            'id': 'instance-reservation-id1'}
        self.patch(db_api, 'host_allocation_create')
        self.patch(db_api, 'instance_reservation_update')

    def _patch_create_resources(self, plugin):
        m = self.patch(plugin, '_create_resources')
        m.return_value = (mock.MagicMock(id=1),
                          mock.MagicMock(id=2),
                          mock.MagicMock(id=3))

    def _patch_flavor(self, standard, extras):
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.return_value = (standard, extras)
        return fa

    def _values(self, **overrides):
        v = {
            'amount': 1,
            'affinity': 'False',
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 0),
            'end_date': datetime.datetime(2030, 1, 1, 12, 0),
            'lease_id': 'lease-1',
        }
        v.update(overrides)
        return v

    def test_flavor_id_no_extras_behaves_like_explicit_fields(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup = self.patch(plugin, 'pickup_hosts')
        mock_pickup.return_value = {'added': ['h1'], 'removed': []}
        self._patch_db()
        self._patch_create_resources(plugin)
        self._patch_flavor({'vcpus': 4, 'memory_mb': 8192,
                            'disk_gb': 20}, {})

        # Act
        ret = plugin.reserve_resource(
            'res-1', self._values(flavor_id='flavor-1'))

        # Assert: vcpus/memory_mb/disk_gb were filled in from the flavor.
        self.assertEqual('instance-reservation-id1', ret)
        # The DB row carries a constraint blob whose accelerator
        # fields are empty (flavor had no extras) but whose flavor_id
        # is set so operators can trace which flavor produced the
        # reservation. Pickup behaviour must be identical to passing
        # explicit cpu/mem/disk.
        recorded = self.mock_inst_create.call_args[0][0]
        self.assertEqual(4, recorded['vcpus'])
        self.assertEqual(8192, recorded['memory_mb'])
        self.assertEqual(20, recorded['disk_gb'])
        blob = json.loads(recorded['accelerator_constraints'])
        self.assertEqual({}, blob['accelerator_resources'])
        self.assertEqual([], blob['required_traits'])
        self.assertIsNone(blob['topology_locality'])
        self.assertEqual('flavor-1', blob['flavor_id'])
        # And pickup_hosts received empty (None-equivalent) accel
        # constraints, so query_available_hosts keeps original behaviour.
        pickup_values = mock_pickup.call_args[0][1]
        self.assertEqual({}, pickup_values['accelerator_resources'])
        self.assertEqual([], pickup_values['required_traits'])
        self.assertIsNone(pickup_values['topology_locality'])

    def test_flavor_id_with_resources_pgpu_extracted_to_constraints(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup = self.patch(plugin, 'pickup_hosts')
        mock_pickup.return_value = {'added': ['h1'], 'removed': []}
        self._patch_db()
        self._patch_create_resources(plugin)
        self._patch_flavor({'vcpus': 4, 'memory_mb': 8192,
                            'disk_gb': 20},
                           {'resources:PGPU': '1',
                            'trait:CUSTOM_AMD_V620': 'required'})

        # Act
        plugin.reserve_resource('res-1', self._values(flavor_id='flavor-1'))

        # Assert: accelerator_constraints persisted, pickup_hosts saw the
        # accelerator_resources value.
        recorded = self.mock_inst_create.call_args[0][0]
        self.assertIn('accelerator_constraints', recorded)
        blob = json.loads(recorded['accelerator_constraints'])
        self.assertEqual({'PGPU': 1}, blob['accelerator_resources'])
        self.assertEqual(['CUSTOM_AMD_V620'], blob['required_traits'])

        pickup_values = mock_pickup.call_args[0][1]
        self.assertEqual({'PGPU': 1},
                          pickup_values['accelerator_resources'])
        self.assertEqual(['CUSTOM_AMD_V620'],
                          pickup_values['required_traits'])

    def test_flavor_id_device_profile_expansion_merges_constraints(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup = self.patch(plugin, 'pickup_hosts')
        mock_pickup.return_value = {'added': ['h1'], 'removed': []}
        self._patch_db()
        self._patch_create_resources(plugin)
        # Flavor has BOTH a direct trait and a device profile name.
        self._patch_flavor({'vcpus': 4, 'memory_mb': 8192,
                            'disk_gb': 20},
                           {'trait:CUSTOM_FLAVOR_DIRECT': 'required',
                            'accel:device_profile': 'v620-singlevf'})
        # Fake Cyborg returns a profile with one group having a PGPU
        # and a CUSTOM_FROM_PROFILE trait.
        from blazar.utils.openstack import cyborg as cyborg_mod
        fake = self.patch(cyborg_mod, 'BlazarCyborgClient')
        fake.return_value.get_device_profile.return_value = {
            'name': 'v620-singlevf',
            'groups': [{
                'resources:CUSTOM_AMD_V620_VF': '1',
                'trait:CUSTOM_FROM_PROFILE': 'required',
            }],
        }

        # Act
        plugin.reserve_resource('res-1', self._values(flavor_id='flavor-1'))

        # Assert: both flavor and device-profile constraints merged.
        recorded = self.mock_inst_create.call_args[0][0]
        blob = json.loads(recorded['accelerator_constraints'])
        self.assertEqual({'CUSTOM_AMD_V620_VF': 1},
                          blob['accelerator_resources'])
        self.assertIn('CUSTOM_FLAVOR_DIRECT', blob['required_traits'])
        self.assertIn('CUSTOM_FROM_PROFILE', blob['required_traits'])

    def test_flavor_id_bad_flavor_raises_flavor_not_found(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.side_effect = (
            mgr_exceptions.FlavorNotFound(flavor='nope'))
        # Act / Assert
        self.assertRaises(
            mgr_exceptions.FlavorNotFound,
            plugin.reserve_resource, 'res-1',
            self._values(flavor_id='nope'))

    def test_cyborg_down_warns_and_proceeds_with_flavor_only(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup = self.patch(plugin, 'pickup_hosts')
        mock_pickup.return_value = {'added': ['h1'], 'removed': []}
        self._patch_db()
        self._patch_create_resources(plugin)
        self._patch_flavor({'vcpus': 4, 'memory_mb': 8192,
                            'disk_gb': 20},
                           {'resources:CUSTOM_OWN_RC': '1',
                            'accel:device_profile': 'will-fail'})
        from blazar.utils.openstack import cyborg as cyborg_mod
        fake = self.patch(cyborg_mod, 'BlazarCyborgClient')
        fake.return_value.get_device_profile.side_effect = (
            cyborg_mod.CyborgClientError("not deployed"))

        # Act: must not raise.
        plugin.reserve_resource('res-1', self._values(flavor_id='flavor-1'))

        # Assert: the flavor-direct resources:* still landed.
        recorded = self.mock_inst_create.call_args[0][0]
        blob = json.loads(recorded['accelerator_constraints'])
        self.assertEqual({'CUSTOM_OWN_RC': 1},
                          blob['accelerator_resources'])

    def test_no_flavor_id_path_does_not_touch_nova(self):
        # Arrange
        plugin = instance_plugin.VirtualInstancePlugin()
        mock_pickup = self.patch(plugin, 'pickup_hosts')
        mock_pickup.return_value = {'added': ['h1'], 'removed': []}
        self._patch_db()
        self._patch_create_resources(plugin)
        fa = self.patch(nova, 'FlavorAccessor')

        values = self._values(vcpus=2, memory_mb=2048, disk_gb=10)
        # Act
        plugin.reserve_resource('res-1', values)

        # Assert: no FlavorAccessor instantiation, no accelerator blob.
        self.assertFalse(fa.called)
        recorded = self.mock_inst_create.call_args[0][0]
        self.assertNotIn('accelerator_constraints', recorded)


class TestReservationFlavorAccelSpecs(tests.TestCase):
    """Gap G1: the reservation flavor must inherit the source flavor's
    accelerator extra specs (accel:device_profile / resources* /
    trait* / group_policy), with reservation-owned keys winning."""

    def _plugin_with_flavor_mocks(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        fake_flavor = mock.MagicMock(flavorid='res-1')
        mock_nova = mock.MagicMock()
        type(plugin).nova = mock_nova
        mock_nova.nova.flavors.create.return_value = fake_flavor
        return plugin, fake_flavor

    def test_source_flavor_accel_specs_filters_keys(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.return_value = (
            {'vcpus': 4, 'memory_mb': 8192, 'disk_gb': 20},
            {'accel:device_profile': 'amd-v620-vf',
             'resources:VGPU': '1',
             'resources1:CUSTOM_FOO': '2',
             'trait:CUSTOM_SOCKET_ROOT': 'required',
             'trait2:HW_NUMA_ROOT': 'required',
             'group_policy': 'none',
             'resources:CUSTOM_RESERVATION_OLD': '1',   # never inherited
             'hw:cpu_policy': 'dedicated',              # not accel-owned
             'aggregate_instance_extra_specs:reservation': 'old-id'})

        specs = plugin._source_flavor_accel_specs({'flavor_id': 'flavor-1'})

        self.assertEqual(
            {'accel:device_profile': 'amd-v620-vf',
             'resources:VGPU': '1',
             'resources1:CUSTOM_FOO': '2',
             'trait:CUSTOM_SOCKET_ROOT': 'required',
             'trait2:HW_NUMA_ROOT': 'required',
             'group_policy': 'none'},
            specs)

    def test_source_flavor_accel_specs_empty_blob(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        fa = self.patch(nova, 'FlavorAccessor')
        self.assertEqual({}, plugin._source_flavor_accel_specs(None))
        self.assertEqual({}, plugin._source_flavor_accel_specs({}))
        self.assertFalse(fa.called)

    def test_source_flavor_accel_specs_lookup_failure_returns_empty(self):
        plugin = instance_plugin.VirtualInstancePlugin()
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.side_effect = (
            nova_exceptions.ClientException(500))
        self.assertEqual(
            {}, plugin._source_flavor_accel_specs({'flavor_id': 'gone'}))

    def test_create_flavor_merges_accel_specs_reservation_keys_win(self):
        plugin, fake_flavor = self._plugin_with_flavor_mocks()

        plugin._create_flavor(
            'res-1', 4, 8192, 20,
            accel_extra_specs={
                'accel:device_profile': 'amd-v620-vf',
                'trait:CUSTOM_SOCKET_ROOT': 'required',
                # hostile input: must be overridden by reservation keys
                'aggregate_instance_extra_specs:reservation': 'spoof',
                'resources:CUSTOM_RESERVATION_RES_1': '9'})

        fake_flavor.set_keys.assert_called_once_with(
            {'accel:device_profile': 'amd-v620-vf',
             'trait:CUSTOM_SOCKET_ROOT': 'required',
             'aggregate_instance_extra_specs:reservation': 'res-1',
             'resources:CUSTOM_RESERVATION_RES_1': '1'})

    def test_create_resources_propagates_accel_specs(self):
        instance_reservation = {
            'reservation_id': 'res-1',
            'vcpus': 4,
            'memory_mb': 8192,
            'disk_gb': 20,
            'affinity': None,
            'accelerator_constraints': json.dumps(
                {'flavor_id': 'flavor-1',
                 'accelerator_resources': {'PGPU': 1},
                 'required_traits': [],
                 'topology_locality': None}),
            }
        plugin, fake_flavor = self._plugin_with_flavor_mocks()
        self.set_context(context.BlazarContext(project_id='fake-project',
                                               auth_token='fake-token'))
        self.patch(nova, 'NovaClientWrapper')
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.return_value = (
            {'vcpus': 4, 'memory_mb': 8192, 'disk_gb': 20},
            {'accel:device_profile': 'amd-v620-vf'})
        self.patch(plugin.placement_client, 'create_reservation_class')
        fake_pool = mock.MagicMock(id='pool-id1')
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = fake_pool

        plugin._create_resources(instance_reservation)

        fake_flavor.set_keys.assert_called_once_with(
            {'accel:device_profile': 'amd-v620-vf',
             'aggregate_instance_extra_specs:reservation': 'res-1',
             'resources:CUSTOM_RESERVATION_RES_1': '1'})

    def test_create_resources_without_constraints_unchanged(self):
        instance_reservation = {
            'reservation_id': 'res-1',
            'vcpus': 2,
            'memory_mb': 1024,
            'disk_gb': 20,
            'affinity': None,
            }
        plugin, fake_flavor = self._plugin_with_flavor_mocks()
        self.set_context(context.BlazarContext(project_id='fake-project',
                                               auth_token='fake-token'))
        self.patch(nova, 'NovaClientWrapper')
        fa = self.patch(nova, 'FlavorAccessor')
        self.patch(plugin.placement_client, 'create_reservation_class')
        mock_pool = self.patch(nova, 'ReservationPool')
        mock_pool.return_value = mock.MagicMock(id='pool-id1')

        plugin._create_resources(instance_reservation)

        self.assertFalse(fa.called)
        fake_flavor.set_keys.assert_called_once_with(
            {'aggregate_instance_extra_specs:reservation': 'res-1',
             'resources:CUSTOM_RESERVATION_RES_1': '1'})

    def test_update_resources_recreates_flavor_with_accel_specs(self):
        plugin, fake_flavor = self._plugin_with_flavor_mocks()
        mock_res_get = self.patch(db_api, 'reservation_get')
        mock_res_get.return_value = {
            'id': 'res-1',
            'status': 'pending',
            'vcpus': 4,
            'memory_mb': 8192,
            'disk_gb': 20,
            'server_group_id': None,
            'resource_id': 'inst-res-1',
            }
        mock_ir_get = self.patch(db_api, 'instance_reservation_get')
        mock_ir_get.return_value = {
            'accelerator_constraints': json.dumps(
                {'flavor_id': 'flavor-1'})}
        fa = self.patch(nova, 'FlavorAccessor')
        fa.return_value.get_flavor.return_value = (
            {'vcpus': 4, 'memory_mb': 8192, 'disk_gb': 20},
            {'accel:device_profile': 'amd-v620-vf'})

        plugin.update_resources('res-1')

        fake_flavor.set_keys.assert_called_once_with(
            {'accel:device_profile': 'amd-v620-vf',
             'aggregate_instance_extra_specs:reservation': 'res-1',
             'resources:CUSTOM_RESERVATION_RES_1': '1'})


class TestAccelSlotCap(tests.TestCase):
    """G2 fix: per-host candidate slots are capped by accelerator
    capacity so amount > capacity raises NotEnoughHostsAvailable."""

    def _build_plugin(self, hosts, placement_state):
        mock_host_get_query = self.patch(
            db_api, 'reservable_host_get_all_by_queries')
        mock_host_get_query.return_value = hosts
        self.patch(db_utils, 'get_reservations_by_host_id').return_value = []
        plugin = instance_plugin.VirtualInstancePlugin()
        plugin.placement_client = FakePlacementClient(placement_state)
        return plugin

    def _h(self, host_id, name='compute-01'):
        return {'id': host_id, 'hypervisor_hostname': name,
                'vcpus': 16, 'memory_mb': 65536, 'local_gb': 1000}

    def _query(self, plugin, **overrides):
        kwargs = {
            'cpus': 4,
            'memory': 8192,
            'disk': 20,
            'resource_properties': '',
            'start_date': datetime.datetime(2030, 1, 1, 8, 0),
            'end_date': datetime.datetime(2030, 1, 1, 12, 0),
        }
        kwargs.update(overrides)
        return plugin.query_available_hosts(**kwargs)

    def test_slots_capped_by_pgpu_capacity(self):
        # Host bin-packs to 4 slots by CPU (16/4) but has only 2 PGPU.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        self.assertEqual(2, len(ret))

    def test_slots_not_capped_without_accel_constraint(self):
        # Same host, no accelerator constraint: CPU bin-pack rules (4).
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        ret = self._query(plugin)
        self.assertEqual(4, len(ret))

    def test_used_reduces_slot_cap(self):
        # total=2 used=1 -> effective=1 -> exactly one slot.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 1}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        self.assertEqual(1, len(ret))

    def test_committed_reduces_slot_cap(self):
        # total=2, one PGPU committed to an overlapping reservation ->
        # only one candidate slot survives.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        self.patch(plugin, '_committed_accel_for_host').return_value = {
            'PGPU': 1}
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        self.assertEqual(1, len(ret))

    def test_committed_exhausts_capacity_rejects_host(self):
        # total=2, 2 committed -> effective 0 -> host excluded entirely.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        self.patch(plugin, '_committed_accel_for_host').return_value = {
            'PGPU': 2}
        ret = self._query(plugin, accelerator_resources={'PGPU': 1})
        self.assertEqual([], ret)

    def test_per_instance_requirement_divides_cap(self):
        # 4 PGPUs, each instance needs 2 -> cap = 2 slots.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 4, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        ret = self._query(plugin, accelerator_resources={'PGPU': 2})
        self.assertEqual(2, len(ret))

    def test_accel_admission_returns_cap(self):
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 2, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        constraints = {'accelerator_resources': {'PGPU': 1},
                       'required_traits': [],
                       'topology_locality': None}
        admit, cap = plugin._accel_admission(
            host, [], constraints,
            datetime.datetime(2030, 1, 1, 8, 0),
            datetime.datetime(2030, 1, 1, 12, 0), [])
        self.assertTrue(admit)
        self.assertEqual(2, cap)

    def test_accel_admission_traits_only_unbounded(self):
        # Traits-only constraint: admitted with no slot cap.
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {},
                            'anchors': set(),
                            'subtree_traits': {'CUSTOM_AMD_V620_VF'}}})
        constraints = {'accelerator_resources': {},
                       'required_traits': ['CUSTOM_AMD_V620_VF'],
                       'topology_locality': None}
        admit, cap = plugin._accel_admission(
            host, [], constraints,
            datetime.datetime(2030, 1, 1, 8, 0),
            datetime.datetime(2030, 1, 1, 12, 0), [])
        self.assertTrue(admit)
        self.assertIsNone(cap)

    def test_host_satisfies_accel_wrapper_bool(self):
        host = self._h('h1', 'compute-01')
        plugin = self._build_plugin(
            [host],
            {'compute-01': {'inventory': {'PGPU': {'total': 0, 'used': 0}},
                            'anchors': set(),
                            'subtree_traits': set()}})
        constraints = {'accelerator_resources': {'PGPU': 1},
                       'required_traits': [],
                       'topology_locality': None}
        self.assertFalse(plugin._host_satisfies_accel(
            host, [], constraints,
            datetime.datetime(2030, 1, 1, 8, 0),
            datetime.datetime(2030, 1, 1, 12, 0), []))
