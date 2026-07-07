# Copyright (c) 2013 Mirantis Inc.
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
from unittest import mock
import uuid as uuidgen

from keystoneauth1 import session
from keystoneauth1 import token_endpoint
from novaclient import client as nova_client
from novaclient import exceptions as nova_exceptions
from novaclient.v2 import availability_zones
from novaclient.v2 import hypervisors
from oslo_config import cfg
from oslo_config import fixture

from blazar import context
from blazar.manager import exceptions as manager_exceptions
from blazar.plugins import oshosts as host_plugin
from blazar import tests
from blazar.utils.openstack import base
from blazar.utils.openstack import nova

CONF = cfg.CONF


class TestCNClient(tests.TestCase):
    def setUp(self):
        super(TestCNClient, self).setUp()

        self.nova = nova
        self.context = context
        self.n_client = nova_client
        self.base = base

        self.ctx = self.patch(self.context, 'current')
        self.client = self.patch(self.n_client, 'Client')
        self.auth = self.patch(token_endpoint, 'Token')
        self.session = self.patch(session, 'Session')
        self.url = 'http://fake.com/'
        self.patch(self.base, 'url_for').return_value = self.url

        self.version = '2'
        self.endpoint_type = 'internalURL'

    def test_client_from_kwargs(self):
        self.ctx.side_effect = RuntimeError
        endpoint = 'fake_endpoint'
        username = 'blazar_admin'
        password = 'blazar_password'
        user_domain = 'User_Domain'
        project_name = 'admin'
        project_domain = 'Project_Domain'
        auth_url = "%s://%s:%s" % (CONF.os_auth_protocol,
                                   CONF.os_auth_host,
                                   CONF.os_auth_port)
        if CONF.os_auth_prefix:
            auth_url += "/%s" % CONF.os_auth_prefix

        kwargs = {'version': self.version,
                  'endpoint_override': endpoint,
                  'username': username,
                  'password': password,
                  'user_domain_name': user_domain,
                  'project_name': project_name,
                  'project_domain_name': project_domain}

        self.nova.BlazarNovaClient(**kwargs)

        self.client.assert_called_once_with(version=self.version,
                                            username=username,
                                            password=password,
                                            user_domain_name=user_domain,
                                            project_name=project_name,
                                            project_domain_name=project_domain,
                                            auth_url=auth_url,
                                            endpoint_override=endpoint,
                                            endpoint_type=self.endpoint_type)

    def test_client_from_ctx(self):
        kwargs = {'version': self.version}

        self.nova.BlazarNovaClient(**kwargs)

        self.auth.assert_called_once_with(self.url,
                                          self.ctx().auth_token)
        self.session.assert_called_once_with(auth=self.auth.return_value)
        self.client.assert_called_once_with(version=self.version,
                                            endpoint_override=self.url,
                                            endpoint_type=self.endpoint_type,
                                            session=self.session.return_value,
                                            global_request_id=mock.ANY)

    def test_getattr(self):
        # TODO(n.s.): Will be done as soon as pypi package will be updated
        pass


class AggregateFake(object):

    def __init__(self, i, name, hosts):
        self.id = i
        self.name = name
        self.hosts = hosts


class ReservationPoolTestCase(tests.TestCase):

    def setUp(self):
        super(ReservationPoolTestCase, self).setUp()
        self.pool_name = 'pool-name-xxx'
        self.project_id = 'project-uuid'
        self.fake_aggregate = AggregateFake(i=123,
                                            name='fooname',
                                            hosts=['host1', 'host2'])
        physical_host_conf = cfg.CONF[host_plugin.RESOURCE_TYPE]
        nova_conf = cfg.CONF.nova
        self.freepool_name = nova_conf.aggregate_freepool_name
        self.project_id_key = nova_conf.project_id_key
        self.blazar_owner = nova_conf.blazar_owner
        self.blazar_az_prefix = physical_host_conf.blazar_az_prefix

        self.cfg = self.useFixture(fixture.Config(CONF))
        self.cfg.config(os_admin_username='fake-user')
        self.cfg.config(os_admin_password='fake-passwd')
        self.cfg.config(os_admin_user_domain_name='fake-user-domain')
        self.cfg.config(os_admin_project_name='fake-pj-name')
        self.cfg.config(os_admin_project_domain_name='fake-pj-domain')

        self.fake_freepool = AggregateFake(i=456,
                                           name=self.freepool_name,
                                           hosts=['host3'])

        self.set_context(context.BlazarContext(project_id=self.project_id))

        self.nova_client = nova_client
        self.nova = self.patch(self.nova_client, 'Client').return_value

        self.patch(self.nova.aggregates, 'set_metadata')
        self.patch(self.nova.aggregates, 'remove_host')

        self.patch(base, 'url_for').return_value = 'http://foo.bar'
        self.pool = nova.ReservationPool()

        self.p_name = self.patch(self.pool, '_generate_aggregate_name')
        self.p_name.return_value = self.pool_name

    def test_configuration(self):
        self.assertEqual("fake-user", self.pool.username)
        self.assertEqual("fake-passwd", self.pool.password)
        self.assertEqual("fake-user-domain", self.pool.user_domain_name)
        self.assertEqual("fake-pj-name", self.pool.project_name)
        self.assertEqual("fake-pj-domain", self.pool.project_domain_name)

    def _patch_get_aggregate_from_name_or_id(self):
        def get_fake_aggregate(*args):
            if self.freepool_name in args or self.fake_freepool.id in args:
                return self.fake_freepool
            else:
                return self.fake_aggregate

        patched_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        patched_pool.side_effect = get_fake_aggregate

    def test_get_aggregate_from_name_or_id(self):
        def fake_aggregate_get(id):
            if id == self.fake_aggregate.id:
                return self.fake_aggregate
            else:
                raise nova_exceptions.NotFound(id)

        self.nova.aggregates.list.return_value = [self.fake_aggregate]
        self.nova.aggregates.get.side_effect = fake_aggregate_get

        self.assertRaises(manager_exceptions.AggregateNotFound,
                          self.pool.get_aggregate_from_name_or_id, 'none')
        self.assertRaises(manager_exceptions.AggregateNotFound,
                          self.pool.get_aggregate_from_name_or_id, '3000')
        self.assertEqual(self.pool.get_aggregate_from_name_or_id('fooname'),
                         self.fake_aggregate)
        self.assertEqual(
            self.pool.get_aggregate_from_name_or_id(self.fake_aggregate),
            self.fake_aggregate)

    def test_generate_aggregate_name(self):
        self.uuidgen = uuidgen
        self.patch(uuidgen, 'uuid4').return_value = 'foo'
        self.assertEqual('foo',
                         nova.ReservationPool._generate_aggregate_name())

    def test_create(self):
        self.patch(self.nova.aggregates, 'create').return_value = (
            self.fake_aggregate)

        az_name = self.blazar_az_prefix + self.pool_name

        agg = self.pool.create(az=az_name)

        self.assertEqual(agg, self.fake_aggregate)

        check0 = self.nova.aggregates.create
        check0.assert_called_once_with(self.pool_name, az_name)

        meta = {self.blazar_owner: self.project_id}
        check1 = self.nova.aggregates.set_metadata
        check1.assert_called_once_with(self.fake_aggregate, meta)

    def test_create_no_az(self):
        self.patch(self.nova.aggregates, 'create').return_value = (
            self.fake_aggregate)

        self.pool.create()

        self.nova.aggregates.create.assert_called_once_with(self.pool_name,
                                                            None)

    def test_create_no_project_id(self):
        self.patch(self.nova.aggregates, 'create').return_value = (
            self.fake_aggregate)

        self.nova_wrapper = self.patch(nova.NovaClientWrapper, 'nova')

        def raiseRuntimeError():
            raise RuntimeError()

        self.context_mock.side_effect = raiseRuntimeError

        self.assertRaises(manager_exceptions.ProjectIdNotFound,
                          self.pool.create)

    def test_delete_with_host(self):
        self._patch_get_aggregate_from_name_or_id()
        agg = self.pool.get('foo')

        self.pool.delete(agg)
        self.nova.aggregates.delete.assert_called_once_with(agg.id)
        for host in agg.hosts:
            self.nova.aggregates.remove_host.assert_any_call(agg.id, host)
            self.nova.aggregates.add_host.assert_any_call(
                self.fake_freepool.id, host
            )

        # can't delete aggregate with hosts
        self.assertRaises(manager_exceptions.AggregateHaveHost,
                          self.pool.delete, 'bar',
                          force=False)

    def test_delete_with_no_host(self):
        self._patch_get_aggregate_from_name_or_id()
        agg = self.pool.get('foo')
        agg.hosts = []
        self.pool.delete('foo', force=False)
        self.nova.aggregates.delete.assert_called_once_with(agg.id)

    def test_delete_with_no_freepool(self):
        def get_fake_aggregate_but_no_freepool(*args):
            if self.freepool_name in args:
                raise manager_exceptions.AggregateNotFound
            else:
                return self.fake_aggregate
        fake_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        fake_pool.side_effect = get_fake_aggregate_but_no_freepool
        agg = self.pool.get('foo')
        agg.hosts = []
        self.assertRaises(manager_exceptions.NoFreePool,
                          self.pool.delete, 'bar',
                          force=False)

    def test_get_all(self):
        self.pool.get_all()
        self.nova.aggregates.list.assert_called_once_with()

    def test_get(self):
        self._patch_get_aggregate_from_name_or_id()
        agg = self.pool.get('foo')
        self.assertEqual(self.fake_aggregate, agg)

    def test_add_computehost(self):
        self._patch_get_aggregate_from_name_or_id()
        terminate_preemptibles = self.patch(
            self.pool, 'terminate_preemptibles')
        self.pool.add_computehost('pool', 'host3')

        check0 = self.nova.aggregates.add_host
        check0.assert_any_call(self.fake_aggregate.id, 'host3')
        check1 = self.nova.aggregates.remove_host
        check1.assert_any_call(self.fake_freepool.id, 'host3')
        terminate_preemptibles.assert_called_with('host3')

    def test_add_computehost_with_host_id(self):
        # NOTE(sbauza): Freepool.hosts only contains names of hosts, not UUIDs
        self._patch_get_aggregate_from_name_or_id()
        self.assertRaises(manager_exceptions.HostNotInFreePool,
                          self.pool.add_computehost, 'pool', '3')

    def test_add_computehost_not_in_freepool(self):
        self._patch_get_aggregate_from_name_or_id()
        self.assertRaises(manager_exceptions.HostNotInFreePool,
                          self.pool.add_computehost,
                          'foopool',
                          'ghost-host')

    def test_add_computehost_with_no_freepool(self):
        def get_fake_aggregate_but_no_freepool(*args):
            if self.freepool_name in args:
                raise manager_exceptions.AggregateNotFound
            else:
                return self.fake_aggregate

        fake_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        fake_pool.side_effect = get_fake_aggregate_but_no_freepool

        self.assertRaises(manager_exceptions.NoFreePool,
                          self.pool.add_computehost,
                          'pool',
                          'host3')

    def test_add_computehost_with_incorrect_pool(self):
        def get_no_aggregate_but_freepool(*args):
            if self.freepool_name in args:
                return self.freepool_name
            else:
                raise manager_exceptions.AggregateNotFound
        fake_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        fake_pool.side_effect = get_no_aggregate_but_freepool
        self.assertRaises(manager_exceptions.AggregateNotFound,
                          self.pool.add_computehost,
                          'wrong_pool',
                          'host3')

    def test_add_computehost_to_freepool(self):
        self._patch_get_aggregate_from_name_or_id()
        self.pool.add_computehost(self.freepool_name, 'host2')
        check = self.nova.aggregates.add_host
        check.assert_called_once_with(self.fake_freepool.id, 'host2')

    def test_add_computehost_revert(self):
        self._patch_get_aggregate_from_name_or_id()
        self.fake_freepool.hosts = ['host1', 'host2']
        terminate_preemptibles = self.patch(
            self.pool, 'terminate_preemptibles')
        self.assertRaises(manager_exceptions.HostNotInFreePool,
                          self.pool.add_computehost,
                          'pool', ['host1', 'host2', 'host3'])

        check0 = self.nova.aggregates.add_host
        check0.assert_has_calls([mock.call(self.fake_aggregate.id, 'host1'),
                                 mock.call(self.fake_aggregate.id, 'host2'),
                                 mock.call(self.fake_freepool.id, 'host1'),
                                 mock.call(self.fake_freepool.id, 'host2')])
        check1 = self.nova.aggregates.remove_host
        check1.assert_has_calls([mock.call(self.fake_freepool.id, 'host1'),
                                 mock.call(self.fake_freepool.id, 'host2'),
                                 mock.call(self.fake_aggregate.id, 'host1'),
                                 mock.call(self.fake_aggregate.id, 'host2')])
        terminate_preemptibles.assert_has_calls([mock.call('host1'),
                                                 mock.call('host2')])

    def test_remove_computehost_from_freepool(self):
        self._patch_get_aggregate_from_name_or_id()
        self.pool.remove_computehost(self.freepool_name, 'host3')

        check = self.nova.aggregates.remove_host
        check.assert_called_once_with(self.fake_freepool.id, 'host3')

    def test_remove_computehost_not_existing_from_freepool(self):
        self._patch_get_aggregate_from_name_or_id()
        self.assertRaises(manager_exceptions.HostNotInFreePool,
                          self.pool.remove_computehost,
                          self.freepool_name,
                          'hostXX')

    def test_remove_all_computehosts(self):
        self._patch_get_aggregate_from_name_or_id()
        self.pool.remove_all_computehosts('pool')
        for host in self.fake_aggregate.hosts:
            check = self.nova.aggregates.remove_host
            check.assert_any_call(self.fake_aggregate.id, host)

    def test_remove_computehost_with_no_freepool(self):
        def get_fake_aggregate_but_no_freepool(*args):
            if self.freepool_name in args:
                raise manager_exceptions.AggregateNotFound
            else:
                return self.fake_aggregate

        fake_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        fake_pool.side_effect = get_fake_aggregate_but_no_freepool

        self.assertRaises(manager_exceptions.NoFreePool,
                          self.pool.remove_computehost,
                          'pool',
                          'host3')

    def test_remove_computehost_with_incorrect_pool(self):
        def get_no_aggregate_but_freepool(*args):
            if self.freepool_name in args:
                return self.freepool_name
            else:
                raise manager_exceptions.AggregateNotFound
        fake_pool = self.patch(self.pool, 'get_aggregate_from_name_or_id')
        fake_pool.side_effect = get_no_aggregate_but_freepool
        self.assertRaises(manager_exceptions.AggregateNotFound,
                          self.pool.remove_computehost,
                          'wrong_pool',
                          'host3')

    def test_remove_computehost_with_wrong_hosts(self):
        self._patch_get_aggregate_from_name_or_id()
        self.nova.aggregates.remove_host.side_effect = (
            nova_exceptions.NotFound(404))
        self.assertRaises(manager_exceptions.CantRemoveHost,
                          self.pool.remove_computehost,
                          'pool',
                          'host3')

    def test_remove_computehosts_with_duplicate_host(self):
        self._patch_get_aggregate_from_name_or_id()
        add_host = self.nova.aggregates.add_host

        self.pool.remove_computehost('pool', 'host3')
        add_host.assert_not_called()

    def test_get_computehosts_with_correct_pool(self):
        self._patch_get_aggregate_from_name_or_id()
        hosts = self.pool.get_computehosts('foo')
        self.assertEqual(hosts, self.fake_aggregate.hosts)

    def test_get_computehosts_with_incorrect_pool(self):
        self.assertEqual([], self.pool.get_computehosts('wrong_pool'))

    def test_add_project(self):
        self._patch_get_aggregate_from_name_or_id()
        self.pool.add_project('pool', 'projectX')
        check = self.nova.aggregates.set_metadata
        check.assert_called_once_with(self.fake_aggregate.id,
                                      {'projectX': self.project_id_key})

    def test_remove_project(self):
        self._patch_get_aggregate_from_name_or_id()
        self.pool.remove_project('pool', 'projectY')
        check = self.nova.aggregates.set_metadata
        check.assert_called_once_with(self.fake_aggregate.id,
                                      {'projectY': None})


class FakeNovaHypervisors(object):

    class FakeHost(object):
        id = 1
        availability_zone = 'fake_az1'
        hypervisor_hostname = 'fake_name.openstack.org'
        vcpus = 1
        cpu_info = 'fake_cpu'
        hypervisor_type = 'fake_type'
        hypervisor_version = 1000000
        memory_mb = 8192
        local_gb = 10

        servers = ['server1', 'server2']
        service = {'host': 'fake_name'}

    @classmethod
    def get(cls, host):
        try:
            host = int(host)
        except ValueError:
            raise nova_exceptions.NotFound(404)
        if host == cls.FakeHost.id:
            return cls.FakeHost
        else:
            raise nova_exceptions.NotFound(404)

    @classmethod
    def search(cls, host, servers=False):
        if host == 'multiple':
            return [cls.FakeHost, cls.FakeHost]
        if host == cls.FakeHost.service['host']:
            return [cls.FakeHost]
        else:
            raise nova_exceptions.NotFound(404)

    @classmethod
    def expected(cls):
        return {'id': cls.FakeHost.id,
                'availability_zone': cls.FakeHost.availability_zone,
                'hypervisor_hostname': cls.FakeHost.hypervisor_hostname,
                'service_name': cls.FakeHost.service['host'],
                'vcpus': cls.FakeHost.vcpus,
                'cpu_info': cls.FakeHost.cpu_info,
                'hypervisor_type': cls.FakeHost.hypervisor_type,
                'hypervisor_version': cls.FakeHost.hypervisor_version,
                'memory_mb': cls.FakeHost.memory_mb,
                'local_gb': cls.FakeHost.local_gb}


class FakeAvailabilityZones(object):

    class FakeAZ1(object):
        zoneName = 'fake_az1'
        hosts = {
            "fake_name": {
                "nova-compute": {}
            },
        }

    class FakeAZ2(object):
        zoneName = 'fake_az2'
        hosts = {
            "fake_name": {
                "nova-conductor": {},
                "nova-scheduler": {}
            },
        }

    @classmethod
    def list(cls, detailed=False):
        return [cls.FakeAZ1, cls.FakeAZ2]


class NovaInventoryTestCase(tests.TestCase):
    def setUp(self):
        super(NovaInventoryTestCase, self).setUp()
        self.context = context
        self.patch(self.context, 'BlazarContext')
        self.nova = nova
        self.patch(base, 'url_for').return_value = 'http://foo.bar'
        self.inventory = self.nova.NovaInventory()

        self.hypervisors_get = self.patch(hypervisors.HypervisorManager, 'get')
        self.hypervisors_get.side_effect = FakeNovaHypervisors.get
        self.hypervisors_search = self.patch(hypervisors.HypervisorManager,
                                             'search')
        self.hypervisors_search.side_effect = FakeNovaHypervisors.search
        self.availability_zones = self.patch(
            availability_zones.AvailabilityZoneManager, 'list')
        self.availability_zones.side_effect = FakeAvailabilityZones.list

    def test_get_host_details_with_host_id(self):
        host = self.inventory.get_host_details('1')
        expected = FakeNovaHypervisors.expected()
        self.assertEqual(expected, host)

    def test_get_host_details_with_host_name(self):
        host = self.inventory.get_host_details('fake_name')
        expected = FakeNovaHypervisors.expected()
        self.assertEqual(expected, host)

    def test_get_host_details_with_host_name_having_multiple_results(self):
        self.assertRaises(manager_exceptions.MultipleHostsFound,
                          self.inventory.get_host_details, 'multiple')

    def test_get_host_details_with_host_id_not_found(self):
        self.assertRaises(manager_exceptions.HostNotFound,
                          self.inventory.get_host_details, '2')

    def test_get_host_details_with_host_name_not_found(self):
        self.assertRaises(manager_exceptions.HostNotFound,
                          self.inventory.get_host_details, 'wrong_name')

    def test_get_host_details_with_invalid_host(self):
        # Create a new class from FakeHost called `invalid_host`,
        # which lacks the vcpus attribute.
        invalid_host = type('invalid_host',
                            FakeNovaHypervisors.FakeHost.__bases__,
                            dict(FakeNovaHypervisors.FakeHost.__dict__))
        del invalid_host.vcpus
        self.hypervisors_get.side_effect = [invalid_host]
        self.assertRaises(manager_exceptions.InvalidHost,
                          self.inventory.get_host_details, '1')

    def test_get_host_details_without_az(self):
        conf = fixture.Config(CONF)
        conf.config(group='nova', az_aware=False)
        host = self.inventory.get_host_details('fake_name')
        expected = FakeNovaHypervisors.expected()
        expected['availability_zone'] = ''
        self.assertEqual(expected, host)

    def test_get_servers_per_host(self):
        servers = self.inventory.get_servers_per_host('fake_name')
        self.assertEqual(FakeNovaHypervisors.FakeHost.servers, servers)

    def test_get_servers_per_host_with_host_id(self):
        self.assertRaises(manager_exceptions.HostNotFound,
                          self.inventory.get_servers_per_host, '1')

    def test_get_servers_per_host_with_host_not_found(self):
        self.assertRaises(manager_exceptions.HostNotFound,
                          self.inventory.get_servers_per_host, 'wrong_name')

    def test_get_servers_per_host_having_multiple_results(self):
        self.assertRaises(manager_exceptions.MultipleHostsFound,
                          self.inventory.get_servers_per_host, 'multiple')

    def test_get_servers_per_host_with_host_having_no_servers(self):
        host_with_zero_servers = FakeNovaHypervisors.FakeHost
        # NOTE(sbauza): We need to simulate a host having zero servers
        del host_with_zero_servers.servers
        servers = self.inventory.get_servers_per_host('fake_name')
        self.assertEqual(None, servers)


class TestParseFlavorConstraints(tests.TestCase):
    """Tests for nova.parse_flavor_constraints, the helper that turns
    Nova flavor extra-specs into Blazar's accelerator constraint
    dict. Pure function, no I/O."""

    def test_empty_extra_specs_returns_empty_constraints(self):
        # Arrange / Act
        result = nova.parse_flavor_constraints({})
        # Assert
        self.assertEqual({}, result['accelerator_resources'])
        self.assertEqual([], result['required_traits'])
        self.assertIsNone(result['topology_locality'])
        self.assertIsNone(result['device_profile'])

    def test_resources_prefix_extracted_to_accelerator_resources(self):
        # Arrange
        extra_specs = {'resources:CUSTOM_AMD_V620_VF': '1',
                       'resources:PGPU': '2'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual({'CUSTOM_AMD_V620_VF': 1, 'PGPU': 2},
                         result['accelerator_resources'])

    def test_resources_vcpu_memory_disk_are_skipped(self):
        # Standard fields are not accelerators -- they come from
        # flavor.vcpus/ram/disk instead.
        # Arrange
        extra_specs = {'resources:VCPU': '4',
                       'resources:MEMORY_MB': '8192',
                       'resources:DISK_GB': '20',
                       'resources:PGPU': '1'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual({'PGPU': 1}, result['accelerator_resources'])

    def test_trait_required_extracted(self):
        # Arrange
        extra_specs = {'trait:CUSTOM_AMD_V620': 'required',
                       'trait:CUSTOM_AMD_V620_NUMA0': 'required',
                       'trait:HW_NIC_OFFLOAD_TSO': 'forbidden'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert: only required, not forbidden
        self.assertIn('CUSTOM_AMD_V620', result['required_traits'])
        self.assertIn('CUSTOM_AMD_V620_NUMA0', result['required_traits'])
        self.assertNotIn('HW_NIC_OFFLOAD_TSO', result['required_traits'])

    def test_topology_locality_socket(self):
        # Arrange
        # Act
        result = nova.parse_flavor_constraints(
            {'hw:cyborg_locality': 'socket'})
        # Assert
        self.assertEqual('socket', result['topology_locality'])

    def test_topology_locality_numa(self):
        # Arrange / Act
        result = nova.parse_flavor_constraints(
            {'hw:cyborg_locality': 'numa'})
        # Assert
        self.assertEqual('numa', result['topology_locality'])

    def test_topology_locality_none_is_no_constraint(self):
        # Arrange / Act
        result = nova.parse_flavor_constraints(
            {'hw:cyborg_locality': 'none'})
        # Assert
        self.assertIsNone(result['topology_locality'])

    def test_device_profile_extracted(self):
        # Arrange / Act
        result = nova.parse_flavor_constraints(
            {'accel:device_profile': 'v620-singlevf'})
        # Assert
        self.assertEqual('v620-singlevf', result['device_profile'])

    def test_non_integer_resources_value_logged_and_skipped(self):
        # Arrange
        extra_specs = {'resources:PGPU': 'not-a-number',
                       'resources:CUSTOM_FPGA': '3'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert: bad value skipped, good value kept
        self.assertNotIn('PGPU', result['accelerator_resources'])
        self.assertEqual(3, result['accelerator_resources']['CUSTOM_FPGA'])

    # ---- Nova granular request groups (numbered suffixes) --------------

    def test_numbered_resources_group_extracted(self):
        # Nova granular groups use numbered prefixes: resources1:,
        # resources2:, ... They must count like the unnumbered form.
        # Arrange / Act
        result = nova.parse_flavor_constraints(
            {'resources1:CUSTOM_AMD_V620_VF': '1'})
        # Assert
        self.assertEqual({'CUSTOM_AMD_V620_VF': 1},
                         result['accelerator_resources'])

    def test_numbered_and_unnumbered_resources_summed(self):
        # The pre-flight is host-level, so counts for the same resource
        # class merge across groups.
        # Arrange
        extra_specs = {'resources:PGPU': '1',
                       'resources1:PGPU': '1',
                       'resources2:PGPU': '2'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual({'PGPU': 4}, result['accelerator_resources'])

    def test_numbered_resources_standard_classes_skipped(self):
        # VCPU/MEMORY_MB/DISK_GB are skipped in numbered groups too.
        # Arrange
        extra_specs = {'resources1:VCPU': '4',
                       'resources1:PGPU': '1'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual({'PGPU': 1}, result['accelerator_resources'])

    def test_numbered_trait_group_extracted(self):
        # Arrange
        extra_specs = {'trait1:CUSTOM_AMD_V620': 'required',
                       'trait2:HW_NIC_OFFLOAD_TSO': 'forbidden'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual(['CUSTOM_AMD_V620'], result['required_traits'])

    def test_same_trait_across_groups_deduplicated(self):
        # Arrange
        extra_specs = {'trait:CUSTOM_AMD_V620': 'required',
                       'trait1:CUSTOM_AMD_V620': 'required'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual(['CUSTOM_AMD_V620'], result['required_traits'])

    def test_traits_suffix_not_confused_with_resources(self):
        # Keys like 'resourcesX:FOO' (non-digit suffix) are not request
        # groups and must be ignored.
        # Arrange
        extra_specs = {'resourcesX:PGPU': '1',
                       'traitX:CUSTOM_FOO': 'required'}
        # Act
        result = nova.parse_flavor_constraints(extra_specs)
        # Assert
        self.assertEqual({}, result['accelerator_resources'])
        self.assertEqual([], result['required_traits'])


class TestFlavorAccessor(tests.TestCase):
    """Tests for nova.FlavorAccessor.get_flavor."""

    def setUp(self):
        super(TestFlavorAccessor, self).setUp()
        self.cfg = self.useFixture(fixture.Config(CONF))

    def _accessor_with_fake_nova(self, fake_flavor=None,
                                  raises=None):
        accessor = nova.FlavorAccessor()
        fake = mock.Mock()
        if raises is not None:
            fake.flavors.get.side_effect = raises
        else:
            fake.flavors.get.return_value = fake_flavor
        # nova.NovaClientWrapper.nova is a @property; patching the
        # instance dict doesn't shadow it. Patch the descriptor at the
        # class level for the duration of the test.
        p = mock.patch.object(type(accessor), 'nova',
                              new_callable=mock.PropertyMock,
                              return_value=fake)
        p.start()
        self.addCleanup(p.stop)
        return accessor

    def test_get_flavor_returns_standard_and_extra_specs(self):
        # Arrange
        fake_flavor = mock.Mock()
        fake_flavor.vcpus = 4
        fake_flavor.ram = 8192
        fake_flavor.disk = 20
        fake_flavor.get_keys.return_value = {
            'resources:PGPU': '1',
            'trait:CUSTOM_AMD_V620': 'required',
        }
        accessor = self._accessor_with_fake_nova(fake_flavor=fake_flavor)
        # Act
        standard, extra = accessor.get_flavor('flavor-1')
        # Assert
        self.assertEqual({'vcpus': 4, 'memory_mb': 8192,
                          'disk_gb': 20}, standard)
        self.assertEqual('1', extra['resources:PGPU'])
        self.assertEqual('required', extra['trait:CUSTOM_AMD_V620'])

    def test_get_flavor_not_found_raises(self):
        # Arrange
        accessor = self._accessor_with_fake_nova(
            raises=nova_exceptions.NotFound(404))
        # Act / Assert
        self.assertRaises(manager_exceptions.FlavorNotFound,
                          accessor.get_flavor, 'nope')

    def test_get_flavor_extra_specs_error_returns_empty(self):
        # If get_keys() blows up (e.g. no permission to read extras),
        # we degrade gracefully to standard fields only -- a reserve
        # without any accelerator extras still works.
        # Arrange
        fake_flavor = mock.Mock()
        fake_flavor.vcpus = 2
        fake_flavor.ram = 1024
        fake_flavor.disk = 5
        fake_flavor.get_keys.side_effect = (
            nova_exceptions.ClientException(500))
        accessor = self._accessor_with_fake_nova(fake_flavor=fake_flavor)
        # Act
        standard, extra = accessor.get_flavor('flavor-2')
        # Assert
        self.assertEqual(2, standard['vcpus'])
        self.assertEqual({}, extra)
