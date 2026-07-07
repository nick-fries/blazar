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
from blazar.utils.openstack import cyborg


class FakeResponse(object):
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError('no json')
        return self._json


class TestGetDeviceProfile(tests.TestCase):

    def _client_with_response(self, response):
        client = cyborg.BlazarCyborgClient()
        fake_adapter = mock.Mock()
        fake_adapter.get.return_value = response
        self.patch(client, '_create_client').return_value = fake_adapter
        return client, fake_adapter

    def test_profile_returned(self):
        profile = {'name': 'v620-singlevf', 'groups': []}
        client, adapter = self._client_with_response(
            FakeResponse(json_data={'device_profiles': [profile]}))
        ret = client.get_device_profile('v620-singlevf')
        self.assertEqual(profile, ret)
        adapter.get.assert_called_once_with(
            '/v2/device_profiles?name=v620-singlevf', raise_exc=False)

    def test_profile_name_is_url_quoted(self):
        # A name containing reserved characters must not be spliced
        # verbatim into the query string.
        profile = {'name': 'a&b', 'groups': []}
        client, adapter = self._client_with_response(
            FakeResponse(json_data={'device_profiles': [profile]}))
        client.get_device_profile('a&b=c/d?e')
        path = adapter.get.call_args[0][0]
        self.assertEqual(
            '/v2/device_profiles?name=a%26b%3Dc%2Fd%3Fe', path)

    def test_http_error_raises_client_error(self):
        client, _ = self._client_with_response(
            FakeResponse(status_code=500, text='boom'))
        self.assertRaises(cyborg.CyborgClientError,
                          client.get_device_profile, 'p')

    def test_non_json_raises_client_error(self):
        client, _ = self._client_with_response(FakeResponse(json_data=None))
        self.assertRaises(cyborg.CyborgClientError,
                          client.get_device_profile, 'p')

    def test_no_matching_profile_raises_client_error(self):
        client, _ = self._client_with_response(
            FakeResponse(json_data={'device_profiles': []}))
        self.assertRaises(cyborg.CyborgClientError,
                          client.get_device_profile, 'missing')

    def test_transport_failure_raises_client_error(self):
        client = cyborg.BlazarCyborgClient()
        self.patch(client, '_create_client').side_effect = (
            RuntimeError('no catalog'))
        self.assertRaises(cyborg.CyborgClientError,
                          client.get_device_profile, 'p')


class TestExtractGroups(tests.TestCase):

    def test_resources_and_traits_merged_across_groups(self):
        profile = {'groups': [
            {'resources:CUSTOM_AMD_V620_VF': '1',
             'trait:CUSTOM_AMD_V620': 'required'},
            {'resources:CUSTOM_AMD_V620_VF': '1',
             'trait:CUSTOM_GPU_SRIOV': 'required'},
        ]}
        accel, traits = cyborg.extract_groups_from_device_profile(profile)
        self.assertEqual({'CUSTOM_AMD_V620_VF': 2}, accel)
        self.assertEqual(['CUSTOM_AMD_V620', 'CUSTOM_GPU_SRIOV'],
                         sorted(traits))

    def test_standard_resource_classes_skipped(self):
        profile = {'groups': [
            {'resources:VCPU': '2', 'resources:PGPU': '1'}]}
        accel, _ = cyborg.extract_groups_from_device_profile(profile)
        self.assertEqual({'PGPU': 1}, accel)

    def test_non_integer_count_skipped(self):
        profile = {'groups': [{'resources:PGPU': 'many'}]}
        accel, _ = cyborg.extract_groups_from_device_profile(profile)
        self.assertEqual({}, accel)

    def test_non_required_trait_ignored(self):
        profile = {'groups': [{'trait:CUSTOM_X': 'forbidden'}]}
        _, traits = cyborg.extract_groups_from_device_profile(profile)
        self.assertEqual([], traits)

    def test_empty_profile(self):
        accel, traits = cyborg.extract_groups_from_device_profile({})
        self.assertEqual({}, accel)
        self.assertEqual([], traits)
