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

"""Tests for the reconciler-support helpers on BlazarPlacementClient."""

from unittest import mock

from oslo_config import cfg
from oslo_config import fixture as conf_fixture
from oslo_serialization import jsonutils

from blazar import tests
from blazar.tests import fake_requests
from blazar.utils.openstack import exceptions
from blazar.utils.openstack import placement


CONF = cfg.CONF


class _FakeRoundTrip(object):
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    def __call__(self, url, method, **kwargs):
        self.urls.append((url, method))
        return self._responses.pop(0)


class PlacementReconcilerHelpersTest(tests.TestCase):
    def setUp(self):
        super(PlacementReconcilerHelpersTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(os_auth_host='foofoo')
        self.cfg.config(os_auth_port='8080')
        self.cfg.config(os_auth_prefix='identity')
        self.cfg.config(os_auth_version='v3')
        self.cfg.config(os_region_name='region_foo')
        self.client = placement.BlazarPlacementClient()

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_empty(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(
            200,
            content=jsonutils.dump_as_bytes({'resource_classes': []}))
        out = self.client.list_resource_classes()
        self.assertEqual([], out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_single_page(self, kss_req):
        body = {
            'resource_classes': [
                {'name': 'VCPU'},
                {'name': 'CUSTOM_RESERVATION_AAA'},
                {'name': 'CUSTOM_RESERVATION_BBB'},
            ]
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.list_resource_classes()
        self.assertEqual(
            ['VCPU', 'CUSTOM_RESERVATION_AAA', 'CUSTOM_RESERVATION_BBB'],
            out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_prefix_filter(self, kss_req):
        body = {
            'resource_classes': [
                {'name': 'VCPU'},
                {'name': 'MEMORY_MB'},
                {'name': 'CUSTOM_RESERVATION_AAA'},
                {'name': 'CUSTOM_RESERVATION_BBB'},
                {'name': 'CUSTOM_OTHER'},
            ]
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.list_resource_classes(
            prefix='CUSTOM_RESERVATION_')
        self.assertEqual(
            ['CUSTOM_RESERVATION_AAA', 'CUSTOM_RESERVATION_BBB'], out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_paginates(self, kss_req):
        page1 = {
            'resource_classes': [{'name': 'CUSTOM_RESERVATION_AAA'}],
            'links': [{'rel': 'next',
                       'href': '/resource_classes?marker=AAA'}]
        }
        page2 = {
            'resource_classes': [{'name': 'CUSTOM_RESERVATION_BBB'}],
            'links': [{'rel': 'self', 'href': '/resource_classes'}]
        }
        rt = _FakeRoundTrip([
            fake_requests.FakeResponse(
                200, content=jsonutils.dump_as_bytes(page1)),
            fake_requests.FakeResponse(
                200, content=jsonutils.dump_as_bytes(page2)),
        ])
        kss_req.side_effect = rt
        out = self.client.list_resource_classes(
            prefix='CUSTOM_RESERVATION_')
        self.assertEqual(
            ['CUSTOM_RESERVATION_AAA', 'CUSTOM_RESERVATION_BBB'], out)
        self.assertEqual(2, len(rt.urls))
        self.assertEqual('/resource_classes', rt.urls[0][0])
        self.assertEqual('/resource_classes?marker=AAA', rt.urls[1][0])

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_paginates_three_pages(self, kss_req):
        pages = []
        for i, name in enumerate(['ALPHA', 'BETA', 'GAMMA']):
            body = {'resource_classes': [{'name': name}]}
            if i < 2:
                body['links'] = [{'rel': 'next',
                                  'href': f'/resource_classes?p={i+1}'}]
            pages.append(fake_requests.FakeResponse(
                200, content=jsonutils.dump_as_bytes(body)))
        rt = _FakeRoundTrip(pages)
        kss_req.side_effect = rt
        out = self.client.list_resource_classes()
        self.assertEqual(['ALPHA', 'BETA', 'GAMMA'], out)
        self.assertEqual(3, len(rt.urls))

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_raises_on_5xx(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(503)
        self.assertRaises(
            exceptions.ResourceClassListFailed,
            self.client.list_resource_classes)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_resource_classes_ignores_unnamed_entries(self, kss_req):
        body = {
            'resource_classes': [
                {'name': 'CUSTOM_RESERVATION_AAA'},
                {'no_name_key': True},
            ]
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.list_resource_classes()
        self.assertEqual(['CUSTOM_RESERVATION_AAA'], out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_allocations_for_class_empty(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes({'allocations': []}))
        out = self.client.get_allocations_for_class(
            'CUSTOM_RESERVATION_AAA')
        self.assertEqual([], out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_allocations_for_class_list_shape(self, kss_req):
        body = {
            'allocations': [
                {'consumer_uuid': 'c1', 'allocations': {}},
                {'consumer_uuid': 'c2', 'allocations': {}},
            ]
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.get_allocations_for_class(
            'CUSTOM_RESERVATION_AAA')
        self.assertEqual(2, len(out))
        self.assertEqual('c1', out[0]['consumer_uuid'])

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_allocations_for_class_dict_shape(self, kss_req):
        body = {
            'allocations': {
                'c1': {'resources': {'CUSTOM_RESERVATION_AAA': 1}},
                'c2': {'resources': {'CUSTOM_RESERVATION_AAA': 1}},
            }
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.get_allocations_for_class(
            'CUSTOM_RESERVATION_AAA')
        self.assertEqual(2, len(out))
        consumers = sorted(a['consumer_uuid'] for a in out)
        self.assertEqual(['c1', 'c2'], consumers)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_allocations_for_class_raises_on_5xx(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(500)
        self.assertRaises(
            exceptions.ResourceClassListFailed,
            self.client.get_allocations_for_class,
            'CUSTOM_RESERVATION_AAA')

    @mock.patch('keystoneauth1.session.Session.request')
    def test_get_allocations_for_class_url_includes_filter(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes({'allocations': []}))
        self.client.get_allocations_for_class('CUSTOM_RESERVATION_AAA')
        args, _ = kss_req.call_args
        self.assertIn('resource_class=CUSTOM_RESERVATION_AAA', args[0])

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_rps_with_inventory_empty(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(
            200,
            content=jsonutils.dump_as_bytes({'resource_providers': []}))
        out = self.client.list_resource_providers_with_inventory(
            'CUSTOM_RESERVATION_AAA')
        self.assertEqual([], out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_rps_with_inventory_returns_uuid_and_name(self, kss_req):
        body = {
            'resource_providers': [
                {'uuid': 'u1', 'name': 'blazar_host1', 'generation': 1},
                {'uuid': 'u2', 'name': 'blazar_host2', 'generation': 2},
            ]
        }
        kss_req.return_value = fake_requests.FakeResponse(
            200, content=jsonutils.dump_as_bytes(body))
        out = self.client.list_resource_providers_with_inventory(
            'CUSTOM_RESERVATION_AAA')
        self.assertEqual(
            [{'uuid': 'u1', 'name': 'blazar_host1'},
             {'uuid': 'u2', 'name': 'blazar_host2'}],
            out)

    @mock.patch('keystoneauth1.session.Session.request')
    def test_list_rps_with_inventory_uses_resource_filter(self, kss_req):
        kss_req.return_value = fake_requests.FakeResponse(
            200,
            content=jsonutils.dump_as_bytes({'resource_providers': []}))
        self.client.list_resource_providers_with_inventory(
            'CUSTOM_RESERVATION_AAA')
        args, _ = kss_req.call_args
        self.assertIn('resource=CUSTOM_RESERVATION_AAA:1', args[0])
