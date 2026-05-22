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

from blazar import tests
from blazar.tests import fake_requests
from blazar.utils.openstack import exceptions
from blazar.utils.openstack import placement

from oslo_config import cfg
from oslo_config import fixture as conf_fixture
from oslo_serialization import jsonutils
from oslo_utils import uuidutils

CONF = cfg.CONF
PLACEMENT_MICROVERSION = 1.29


class TestPlacementClient(tests.TestCase):
    def setUp(self):
        super(TestPlacementClient, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(os_auth_host='foofoo')
        self.cfg.config(os_auth_port='8080')
        self.cfg.config(os_auth_prefix='identity')
        self.cfg.config(os_auth_version='v3')
        self.cfg.config(os_region_name='region_foo')
        self.client = placement.BlazarPlacementClient()

    def test_client_auth_url(self):
        client = self.client._create_client()
        self.assertEqual("http://foofoo:8080/identity/v3",
                         client.session.auth.auth_url)

    def _add_default_kwargs(self, kwargs):
        kwargs['endpoint_filter'] = {'service_type': 'placement',
                                     'interface': 'internal',
                                     'region_name': 'region_foo'}
        kwargs['headers'] = {'accept': 'application/json'}
        kwargs['microversion'] = PLACEMENT_MICROVERSION
        kwargs['raise_exc'] = False
        kwargs['rate_semaphore'] = mock.ANY

    def _assert_keystone_called_once(self, kss_req, url, method, **kwargs):
        self._add_default_kwargs(kwargs)
        kss_req.assert_called_once_with(url, method, **kwargs)

    def _assert_keystone_called_any(self, kss_req, url, method, **kwargs):
        self._add_default_kwargs(kwargs)
        kss_req.assert_any_call(url, method, **kwargs)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(200)
        url = '/resource_providers'
        resp = self.client.get(url)
        self.assertEqual(200, resp.status_code)
        self._assert_keystone_called_once(kss_req, url, 'GET')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_post(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(200)
        url = '/resource_providers'
        data = {'name': 'unicorn'}
        resp = self.client.post(url, data)
        self.assertEqual(200, resp.status_code)
        self._assert_keystone_called_once(kss_req, url, 'POST', json=data)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_put(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(200)
        url = '/resource_providers'
        data = {'name': 'unicorn'}
        resp = self.client.put(url, data)
        self.assertEqual(200, resp.status_code)
        self._assert_keystone_called_once(kss_req, url, 'PUT', json=data)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(200)
        url = '/resource_providers'
        resp = self.client.delete(url)
        self.assertEqual(200, resp.status_code)
        self._assert_keystone_called_once(kss_req, url, 'DELETE')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_resource_provider(self, kss_req):
        rp_name = 'blazar'
        rp_uuid = uuidutils.generate_uuid()
        parent_uuid = uuidutils.generate_uuid()

        mock_json_data = {
            'resource_providers': [
                {
                    'uuid': rp_uuid,
                    'name': rp_name,
                    'generation': 0,
                    'parent_provider_uuid': parent_uuid
                }
            ]
        }

        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_json_data))

        result = self.client.get_resource_provider(rp_name)

        expected_url = '/resource_providers?name=blazar'
        self._assert_keystone_called_once(kss_req, expected_url, 'GET')
        expected = {'uuid': rp_uuid,
                    'name': rp_name,
                    'generation': 0,
                    'parent_provider_uuid': parent_uuid}
        self.assertEqual(expected, result)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_resource_provider_no_rp(self, kss_req):
        rp_name = 'blazar'

        mock_json_data = {
            'resource_providers': []
        }

        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_json_data))

        result = self.client.get_resource_provider(rp_name)

        expected_url = '/resource_providers?name=blazar'
        self._assert_keystone_called_once(kss_req, expected_url, 'GET')
        self.assertEqual(None, result)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_resource_provider_fail(self, kss_req):
        rp_name = 'blazar'
        kss_req.return_value = fake_requests.FakeResponse(404)

        self.assertRaises(
            exceptions.ResourceProviderRetrievalFailed,
            self.client.get_resource_provider, rp_name)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_traits(self, kss_req):
        rp_uuid = uuidutils.generate_uuid()

        mock_json_data = {
            "resource_provider_generation": 1,
            "traits": [
                "CUSTOM_HW_FPGA_CLASS1",
                "CUSTOM_HW_FPGA_CLASS3"
            ]
        }

        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_json_data))

        result = self.client.get_traits(rp_uuid)

        expected_url = '/resource_providers/%s/traits' % rp_uuid
        self._assert_keystone_called_once(kss_req, expected_url, 'GET')
        expected = [
            "CUSTOM_HW_FPGA_CLASS1",
            "CUSTOM_HW_FPGA_CLASS3"
        ]
        self.assertEqual(expected, result)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_resource_provider(self, kss_req):
        rp_name = 'Blazar'
        rp_uuid = uuidutils.generate_uuid()
        parent_uuid = uuidutils.generate_uuid()

        mock_json_data = {'uuid': rp_uuid,
                          'name': rp_name,
                          'generation': 0,
                          'parent_provider_uuid': parent_uuid}

        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_json_data))

        result = self.client.create_resource_provider(
            rp_name, rp_uuid=rp_uuid, parent_uuid=parent_uuid)

        expected_url = '/resource_providers'
        expected_data = {'uuid': rp_uuid,
                         'name': rp_name,
                         'parent_provider_uuid': parent_uuid}
        self._assert_keystone_called_once(kss_req, expected_url, 'POST',
                                          json=expected_data)
        self.assertEqual(mock_json_data, result)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_resource_provider_fail(self, kss_req):
        rp_name = 'Blazar'
        kss_req.return_value = fake_requests.FakeResponse(404)

        self.assertRaises(
            exceptions.ResourceProviderCreationFailed,
            self.client.create_resource_provider, rp_name)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_resource_provider(self, kss_req):
        rp_uuid = uuidutils.generate_uuid()
        kss_req.return_value = fake_requests.FakeResponse(200)

        self.client.delete_resource_provider(rp_uuid)

        expected_url = '/resource_providers/' + str(rp_uuid)
        self._assert_keystone_called_once(kss_req, expected_url, 'DELETE')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_resource_provider_fail(self, kss_req):
        rp_uuid = uuidutils.generate_uuid()
        kss_req.return_value = fake_requests.FakeResponse(404)

        self.assertRaises(
            exceptions.ResourceProviderDeletionFailed,
            self.client.delete_resource_provider, rp_uuid)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_reservation_provider(self, kss_req):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"
        get_json_mock = {
            'resource_providers': [
                {
                    'uuid': host_uuid,
                    'name': host_name,
                    'generation': 0,
                    'parent_provider_uuid': None
                }
            ]
        }
        post_json_mock = {'uuid': rp_uuid,
                          'name': rp_name,
                          'generation': 0,
                          'parent_provider_uuid': host_uuid}
        mock_call1 = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(get_json_mock))
        mock_call2 = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(post_json_mock))
        kss_req.side_effect = [mock_call1, mock_call2]

        self.client.create_reservation_provider(host_name)

        expected_url_get = "/resource_providers?name=%s" % host_name
        self._assert_keystone_called_any(kss_req, expected_url_get, 'GET')

        expected_url_post = "/resource_providers"
        expected_data = {'name': 'blazar_compute-1',
                         'parent_provider_uuid': host_uuid}
        self._assert_keystone_called_any(kss_req, expected_url_post, 'POST',
                                         json=expected_data)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_reservation_provider_fail(self, kss_req):
        host_name = "compute-1"
        get_json_mock = {'resource_providers': []}
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(get_json_mock))
        self.assertRaises(
            exceptions.ResourceProviderNotFound,
            self.client.create_reservation_provider, host_name)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_provider(self, kss_req):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"
        get_json_mock = {
            'resource_providers': [
                {
                    'uuid': rp_uuid,
                    'name': rp_name,
                    'generation': 0,
                    'parent_provider_uuid': host_uuid
                }
            ]
        }
        mock_call1 = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(get_json_mock))
        mock_call2 = fake_requests.FakeResponse(200)
        kss_req.side_effect = [mock_call1, mock_call2]

        self.client.delete_reservation_provider(host_name)

        expected_url_get = "/resource_providers?name=%s" % rp_name
        self._assert_keystone_called_any(kss_req, expected_url_get, 'GET')

        expected_url_post = "/resource_providers/%s" % rp_uuid
        self._assert_keystone_called_any(kss_req, expected_url_post, 'DELETE')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_provider_no_rp(self, kss_req):
        host_name = "compute-1"
        rp_name = "blazar_compute-1"
        get_json_mock = {
            'resource_providers': []
        }
        mock_call1 = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(get_json_mock))
        mock_call2 = fake_requests.FakeResponse(200)
        kss_req.side_effect = [mock_call1, mock_call2]

        self.client.delete_reservation_provider(host_name)

        expected_url_get = "/resource_providers?name=%s" % rp_name
        self._assert_keystone_called_any(kss_req, expected_url_get, 'GET')

        # Ensure that mock_call2 for delete is not called
        self.assertEqual(kss_req.call_count, 1)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_reservation_class(self, kss_req):
        rc_name = 'abc-def'
        kss_req.return_value = fake_requests.FakeResponse(200)

        self.client.create_reservation_class(rc_name)

        expected_url = '/resource_classes'
        expected_data = {'name': 'CUSTOM_RESERVATION_ABC_DEF'}
        self._assert_keystone_called_once(kss_req, expected_url, 'POST',
                                          json=expected_data)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_create_reservation_class_fail(self, kss_req):
        rc_name = 'abc-def'
        kss_req.return_value = fake_requests.FakeResponse(400)

        self.assertRaises(
            exceptions.ResourceClassCreationFailed,
            self.client.create_reservation_class, rc_name)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_class(self, kss_req):
        rc_name = 'abc-def'
        kss_req.return_value = fake_requests.FakeResponse(200)

        self.client.delete_reservation_class(rc_name)

        expected_url = '/resource_classes/CUSTOM_RESERVATION_ABC_DEF'
        self._assert_keystone_called_once(kss_req, expected_url, 'DELETE')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_class_fail(self, kss_req):
        rc_name = 'abc-def'
        # If no reservation class found, the placement API returns 404 error.
        kss_req.return_value = fake_requests.FakeResponse(404)
        # Ensure that no error is raised
        self.client.delete_reservation_class(rc_name)

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get')
    @mock.patch('keystoneauth1.session.Session.request')
    def test_update_reservation_inventory(self, kss_req, client_get, get_rp):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"

        # Build the mock of current resource provider
        mock_get_rp_json = {'uuid': rp_uuid,
                            'name': rp_name,
                            'generation': 0,
                            'parent_provider_uuid': host_uuid}
        get_rp.return_value = mock_get_rp_json

        # Build the mock of "current" inventory for get_inventory()
        curr_gen = 11
        mock_get_inv_json = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 1
                },
            },
            "resource_provider_generation": curr_gen
        }
        client_get.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_get_inv_json))

        # Build the mock of "updated" inventory for update_inventory()
        update_gen = 12
        mock_put_json = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 1
                },
                'CUSTOM_RESERVATION_ADD': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                },
            },
            "resource_provider_generation": update_gen
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_put_json))

        result = self.client.update_reservation_inventory(host_name, 'add', 3)

        expected_data = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 1
                },
                'CUSTOM_RESERVATION_ADD': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                },
            },
            "resource_provider_generation": curr_gen
        }
        expected_url = '/resource_providers/%s/inventories' % rp_uuid
        self._assert_keystone_called_once(kss_req, expected_url, 'PUT',
                                          json=expected_data)
        self.assertEqual(mock_put_json, result)

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get')
    @mock.patch('keystoneauth1.session.Session.request')
    def test_add_reservation_inventory(self, kss_req, client_get, get_rp):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"

        # Build the mock of current resource provider
        mock_get_rp_json = {'uuid': rp_uuid,
                            'name': rp_name,
                            'generation': 0,
                            'parent_provider_uuid': host_uuid}
        get_rp.return_value = mock_get_rp_json

        # Build the mock of "current" inventory for get_inventory()
        curr_gen = 11
        mock_get_inv_json = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 1
                },
            },
            "resource_provider_generation": curr_gen
        }
        client_get.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_get_inv_json))

        # Build the mock of "updated" inventory for update_inventory()
        update_gen = 12
        mock_put_json = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                },
            },
            "resource_provider_generation": update_gen
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_put_json))

        result = self.client.update_reservation_inventory(
            host_name, 'curr', 2, additional=True)

        expected_data = {
            'inventories': {
                'CUSTOM_RESERVATION_CURR': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                }
            },
            "resource_provider_generation": curr_gen
        }
        expected_url = '/resource_providers/%s/inventories' % rp_uuid
        self._assert_keystone_called_once(kss_req, expected_url, 'PUT',
                                          json=expected_data)
        self.assertEqual(mock_put_json, result)

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.create_reservation_provider')
    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get')
    @mock.patch('keystoneauth1.session.Session.request')
    def test_update_reservation_inventory_no_rp(
            self, kss_req, client_get, create_rp, get_rp):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"

        # Build the mock that there is no existing reservation provider
        get_rp.return_value = None

        # Build the mock of created resource provider
        mock_post_rp_json = {'uuid': rp_uuid,
                             'name': rp_name,
                             'generation': 0,
                             'parent_provider_uuid': host_uuid}
        create_rp.return_value = mock_post_rp_json

        # Build the mock of "current" inventory for get_inventory()
        curr_gen = 0
        mock_get_inv_json = {
            'inventories': {},
            "resource_provider_generation": curr_gen
        }
        client_get.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_get_inv_json))

        # Build the mock of "updated" inventory for update_inventory()
        update_gen = 1
        mock_put_json = {
            'inventories': {
                'CUSTOM_RESERVATION_ADD': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                },
            },
            "resource_provider_generation": update_gen
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(mock_put_json))

        result = self.client.update_reservation_inventory(host_name, 'add', 3)

        # Ensure that the create_reservation_provider() is called.
        create_rp.assert_called_once_with(host_name)

        expected_data = {
            'inventories': {
                'CUSTOM_RESERVATION_ADD': {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": 3
                },
            },
            "resource_provider_generation": curr_gen
        }
        expected_url = '/resource_providers/%s/inventories' % rp_uuid
        self._assert_keystone_called_once(kss_req, expected_url, 'PUT',
                                          json=expected_data)
        self.assertEqual(mock_put_json, result)

        kss_req.reset_mock()

        # Test retrying on 409 conflict
        mock_json_data = {
            "errors": [
                {"status": 409,
                 "code": "placement.concurrent_update",
                 "title": "Conflict"}
            ]
        }

        kss_req.return_value = fake_requests.FakeResponse(
            409, content=jsonutils.dump_as_bytes(mock_json_data))
        self.assertRaises(
            exceptions.InventoryConflict,
            self.client.update_reservation_inventory, host_name, 'add', 3)
        self.assertEqual(5, kss_req.call_count)
        kss_req.reset_mock()

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_inventory(self, kss_req, get_rp):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"

        # Build the mock of current resource provider
        mock_get_rp_json = {'uuid': rp_uuid,
                            'name': rp_name,
                            'generation': 0,
                            'parent_provider_uuid': host_uuid}
        get_rp.return_value = mock_get_rp_json

        kss_req.return_value = fake_requests.FakeResponse(200)

        self.client.delete_reservation_inventory(host_name, "curr1")

        expected_url = ('/resource_providers/%s/inventories'
                        '/CUSTOM_RESERVATION_CURR1' % rp_uuid)

        self._assert_keystone_called_once(kss_req, expected_url, 'DELETE')

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    def test_delete_reservation_inventory_no_rp(self, get_rp):
        host_name = "compute-1"
        # Build the mock that there is no existing reservation provider
        get_rp.return_value = None

        self.assertRaises(
            exceptions.ResourceProviderNotFound,
            self.client.delete_reservation_inventory, host_name, "curr1")

    @mock.patch('blazar.utils.openstack.placement.'
                'BlazarPlacementClient.get_resource_provider')
    @mock.patch('keystoneauth1.session.Session.request')
    def test_delete_reservation_inventory_no_rc(self, kss_req, get_rp):
        host_uuid = uuidutils.generate_uuid()
        host_name = "compute-1"
        rp_uuid = uuidutils.generate_uuid()
        rp_name = "blazar_compute-1"

        # Build the mock of current resource provider
        mock_get_rp_json = {'uuid': rp_uuid,
                            'name': rp_name,
                            'generation': 0,
                            'parent_provider_uuid': host_uuid}
        get_rp.return_value = mock_get_rp_json

        # If no reservation class found or if no inventory found,
        # then the placement API returns 404 error.
        kss_req.return_value = fake_requests.FakeResponse(404)
        # Ensure that no error is raised
        self.client.delete_reservation_inventory(host_name, "curr1")


class TestTopologyAwareHelpers(tests.TestCase):
    """Tests for the topology-aware reservation helpers on
    BlazarPlacementClient: get_host_rp_tree, host_has_anchor,
    host_subtree_has_traits, get_accelerator_inventory_for_host.

    These tests use a FakePlacementHTTP that records (method, url)
    pairs and returns canned JSON. Assertions are on the
    *return value* of each helper, not on which URLs were called.
    """

    def setUp(self):
        super(TestTopologyAwareHelpers, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(os_auth_host='dummy')
        self.cfg.config(os_auth_port='5000')
        self.cfg.config(os_region_name='r1')
        self.client = placement.BlazarPlacementClient()

    def _patch_http(self, responses):
        """Patch the four HTTP verbs on the client.

        :param responses: dict mapping URL substring to dict
            ``{'json': obj, 'status': int}``. Order doesn't matter;
            first matching substring wins.
        """
        def fake_get(url, microversion=mock.ANY):
            for key, resp in responses.items():
                if key in url:
                    return fake_requests.FakeResponse(
                        resp.get('status', 200),
                        content=jsonutils.dumps(
                            resp.get('json', {})).encode('utf-8'))
            return fake_requests.FakeResponse(404)
        return mock.patch.object(self.client, 'get', side_effect=fake_get)

    def _make_tree_responses(self, host_uuid, sub_rps,
                             host_traits=None,
                             host_inv=None):
        """Build the responses dict for one host + N sub-RPs.

        :param sub_rps: list of dicts with keys uuid, name, traits,
                        inventory, usages (each optional).
        :param host_traits: list of traits on the host RP
        :param host_inv: inventory dict on the host RP
        """
        host_traits = host_traits or []
        host_inv = host_inv or {}

        provider_list = [{
            'uuid': host_uuid,
            'name': 'host-1',
            'parent_provider_uuid': None,
        }]
        for rp in sub_rps:
            provider_list.append({
                'uuid': rp['uuid'],
                'name': rp.get('name', 'sub'),
                'parent_provider_uuid': rp.get('parent_uuid', host_uuid),
            })

        responses = {
            '/resource_providers?name=host-1': {
                'json': {'resource_providers': [provider_list[0]]}
            },
            '/resource_providers?in_tree=': {
                'json': {'resource_providers': provider_list}
            },
            '/resource_providers/%s/inventories' % host_uuid: {
                'json': {'inventories': host_inv,
                         'resource_provider_generation': 0}
            },
            '/resource_providers/%s/traits' % host_uuid: {
                'json': {'traits': host_traits,
                         'resource_provider_generation': 0}
            },
            '/resource_providers/%s/usages' % host_uuid: {
                'json': {'usages': {}}
            },
        }
        for rp in sub_rps:
            responses['/resource_providers/%s/inventories' % rp['uuid']] = {
                'json': {'inventories': rp.get('inventory', {}),
                         'resource_provider_generation': 0}}
            responses['/resource_providers/%s/traits' % rp['uuid']] = {
                'json': {'traits': rp.get('traits', []),
                         'resource_provider_generation': 0}}
            responses['/resource_providers/%s/usages' % rp['uuid']] = {
                'json': {'usages': rp.get('usages', {})}}
        return responses

    # --- get_host_rp_tree ------------------------------------------------

    def test_get_host_rp_tree_returns_empty_when_host_missing(self):
        # Arrange
        with mock.patch.object(self.client, 'get_resource_provider',
                               return_value=None):
            # Act
            tree = self.client.get_host_rp_tree('host-1')
        # Assert
        self.assertEqual({}, tree)

    def test_get_host_rp_tree_includes_host_and_subrps(self):
        # Arrange
        host_uuid = uuidutils.generate_uuid()
        sub_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': sub_uuid, 'name': 'gpu-sub',
              'traits': ['CUSTOM_AMD_V620'],
              'inventory': {'PGPU': {'total': 4, 'reserved': 0}}}])
        with self._patch_http(responses):
            # Act
            tree = self.client.get_host_rp_tree('host-1')
        # Assert
        self.assertIn(host_uuid, tree)
        self.assertIn(sub_uuid, tree)
        self.assertEqual('gpu-sub', tree[sub_uuid]['name'])
        self.assertEqual(['CUSTOM_AMD_V620'], tree[sub_uuid]['traits'])
        self.assertEqual(4, tree[sub_uuid]['inventory']['PGPU']['total'])

    # --- host_has_anchor -------------------------------------------------

    def test_host_has_anchor_true_when_socket_trait_present(self):
        # Arrange
        host_uuid = uuidutils.generate_uuid()
        sock_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': sock_uuid, 'name': 'socket-0',
              'traits': ['CUSTOM_SOCKET_ROOT']}])
        with self._patch_http(responses):
            # Act
            ok = self.client.host_has_anchor('host-1', 'CUSTOM_SOCKET_ROOT')
        # Assert
        self.assertTrue(ok)

    def test_host_has_anchor_false_when_no_anchor_trait(self):
        # Arrange
        host_uuid = uuidutils.generate_uuid()
        sub_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': sub_uuid, 'name': 'gpu',
              'traits': ['CUSTOM_AMD_V620']}])
        with self._patch_http(responses):
            # Act
            ok = self.client.host_has_anchor('host-1', 'CUSTOM_SOCKET_ROOT')
        # Assert
        self.assertFalse(ok)

    def test_host_has_anchor_ignores_blazar_reservation_rp(self):
        # Arrange: blazar_* RP carries the anchor trait by accident.
        # We must NOT consider it a real anchor.
        host_uuid = uuidutils.generate_uuid()
        blazar_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': blazar_uuid, 'name': 'blazar_host-1',
              'traits': ['CUSTOM_SOCKET_ROOT']}])
        with self._patch_http(responses):
            # Act
            ok = self.client.host_has_anchor('host-1', 'CUSTOM_SOCKET_ROOT')
        # Assert
        self.assertFalse(ok)

    # --- host_subtree_has_traits ----------------------------------------

    def test_host_subtree_has_traits_passes_when_all_present(self):
        # Arrange
        host_uuid = uuidutils.generate_uuid()
        gpu_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': gpu_uuid, 'name': 'gpu',
              'traits': ['CUSTOM_AMD_V620', 'CUSTOM_AMD_V620_VF']}])
        with self._patch_http(responses):
            # Act
            ok = self.client.host_subtree_has_traits(
                'host-1', ['CUSTOM_AMD_V620', 'CUSTOM_AMD_V620_VF'])
        # Assert
        self.assertTrue(ok)

    def test_host_subtree_has_traits_rejects_when_one_missing(self):
        # Arrange
        host_uuid = uuidutils.generate_uuid()
        gpu_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': gpu_uuid, 'name': 'gpu',
              'traits': ['CUSTOM_AMD_V620']}])
        with self._patch_http(responses):
            # Act
            ok = self.client.host_subtree_has_traits(
                'host-1', ['CUSTOM_AMD_V620', 'CUSTOM_AMD_V620_VF'])
        # Assert
        self.assertFalse(ok)

    def test_host_subtree_has_traits_empty_required_is_true(self):
        # Arrange / Act
        ok = self.client.host_subtree_has_traits('host-1', [])
        # Assert
        self.assertTrue(ok)

    # --- get_accelerator_inventory_for_host -----------------------------

    def test_accel_inventory_sums_across_subtree(self):
        # Arrange: two GPU sub-RPs, 2 PGPU each.
        host_uuid = uuidutils.generate_uuid()
        gpu0 = uuidutils.generate_uuid()
        gpu1 = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': gpu0, 'name': 'gpu0',
              'inventory': {'PGPU': {'total': 2, 'reserved': 0}},
              'usages': {'PGPU': 0}},
             {'uuid': gpu1, 'name': 'gpu1',
              'inventory': {'PGPU': {'total': 2, 'reserved': 0}},
              'usages': {'PGPU': 1}}])
        with self._patch_http(responses):
            # Act
            inv = self.client.get_accelerator_inventory_for_host(
                'host-1', ['PGPU'])
        # Assert
        self.assertEqual(4, inv['PGPU']['total'])
        self.assertEqual(1, inv['PGPU']['used'])

    def test_accel_inventory_excludes_blazar_reservation_rp(self):
        # Arrange: a blazar_* RP carries the same RC. It must be
        # excluded so the math doesn't double-count reservation
        # capacity as real hardware.
        host_uuid = uuidutils.generate_uuid()
        real_gpu = uuidutils.generate_uuid()
        blazar = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': real_gpu, 'name': 'gpu',
              'inventory': {'PGPU': {'total': 2, 'reserved': 0}},
              'usages': {'PGPU': 0}},
             {'uuid': blazar, 'name': 'blazar_host-1',
              'inventory': {'PGPU': {'total': 8, 'reserved': 0}},
              'usages': {'PGPU': 8}}])
        with self._patch_http(responses):
            # Act
            inv = self.client.get_accelerator_inventory_for_host(
                'host-1', ['PGPU'])
        # Assert
        self.assertEqual(2, inv['PGPU']['total'])
        self.assertEqual(0, inv['PGPU']['used'])

    def test_accel_inventory_missing_class_returns_zero(self):
        # Arrange: tree has PGPU but caller asks for FPGA.
        host_uuid = uuidutils.generate_uuid()
        gpu_uuid = uuidutils.generate_uuid()
        responses = self._make_tree_responses(
            host_uuid,
            [{'uuid': gpu_uuid, 'name': 'gpu',
              'inventory': {'PGPU': {'total': 2, 'reserved': 0}}}])
        with self._patch_http(responses):
            # Act
            inv = self.client.get_accelerator_inventory_for_host(
                'host-1', ['FPGA'])
        # Assert
        self.assertEqual({'total': 0, 'used': 0}, inv['FPGA'])
