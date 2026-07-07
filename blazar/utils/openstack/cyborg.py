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

"""Minimal Cyborg client.

This module exists purely to support reservation-time expansion of
Cyborg device profiles into resources:* / trait:* requirements that
Blazar can pre-flight against Placement. We do not write Cyborg state
from Blazar; we only read device profile definitions.

If Cyborg is not deployed (or auth/catalog lookup fails) callers must
treat this as a soft failure and proceed without device-profile
expansion. The spike's design contract is: if the user puts the
constraints directly on the flavor as resources:* / trait:* extra
specs, Blazar still pre-flights them correctly.
"""

import urllib.parse

from keystoneauth1 import adapter
from keystoneauth1.identity import v3
from keystoneauth1 import session

from oslo_config import cfg
from oslo_log import log as logging

from blazar import context
from blazar.utils.openstack import base


cyborg_opts = [
    cfg.StrOpt('endpoint_type',
               default='internal',
               choices=['public', 'admin', 'internal'],
               help='Type of the Cyborg endpoint to use. This endpoint '
                    'will be looked up in the keystone catalog and should '
                    'be one of public, internal or admin.'),
    cfg.StrOpt('service_type',
               default='accelerator',
               help='Cyborg service type as registered in the keystone '
                    'service catalog.'),
]

CONF = cfg.CONF
CONF.register_opts(cyborg_opts, group='cyborg')
LOG = logging.getLogger(__name__)

CYBORG_MICROVERSION = '2.0'


class CyborgClientError(Exception):
    """Raised on any client-side failure during a Cyborg lookup.

    Callers in Blazar should catch this and proceed without device
    profile expansion (logging at WARN).
    """


class BlazarCyborgClient(object):
    """Read-only Cyborg client used at reservation time.

    The only call we currently make is GET /v2/device_profiles. We do
    not bind ARQs from Blazar; ARQ creation/bind remains Nova's
    responsibility at instance boot.
    """

    def _create_client(self):
        ctx = None
        try:
            ctx = context.current()
        except RuntimeError:
            pass

        kwargs = {}
        if ctx is not None:
            kwargs['global_request_id'] = ctx.global_request_id

        auth_url = "%s://%s:%s" % (CONF.os_auth_protocol,
                                   base.get_os_auth_host(CONF),
                                   CONF.os_auth_port)
        if CONF.os_auth_prefix:
            auth_url += "/%s" % CONF.os_auth_prefix
        if CONF.os_auth_version:
            auth_url += "/%s" % CONF.os_auth_version

        auth = v3.Password(auth_url=auth_url,
                           username=CONF.os_admin_username,
                           password=CONF.os_admin_password,
                           project_name=CONF.os_admin_project_name,
                           user_domain_name=CONF.os_admin_user_domain_name,
                           project_domain_name=(
                               CONF.os_admin_project_domain_name))
        sess_kwargs = {'auth': auth}
        if CONF.cafile:
            sess_kwargs['verify'] = CONF.cafile
        sess = session.Session(**sess_kwargs)

        kwargs.setdefault('service_type', CONF.cyborg.service_type)
        kwargs.setdefault('interface', CONF.cyborg.endpoint_type)
        kwargs.setdefault('region_name', CONF.os_region_name)
        kwargs.setdefault('additional_headers',
                          {'accept': 'application/json',
                           'OpenStack-API-Version':
                               'accelerator ' + CYBORG_MICROVERSION})
        return adapter.Adapter(sess, **kwargs)

    def get_device_profile(self, name):
        """Fetch a device profile by name.

        :param name: device profile name (the value of
                     ``accel:device_profile=`` on a flavor)
        :return: the device profile dict (with a ``groups`` key whose
                 value is a list of dicts each holding resources:* and
                 trait:* keys).
        :raises CyborgClientError: on any failure.
        """
        try:
            client = self._create_client()
            resp = client.get(
                '/v2/device_profiles?name=%s'
                % urllib.parse.quote(str(name), safe=''),
                raise_exc=False)
        except Exception as exc:
            raise CyborgClientError(
                "Failed to call Cyborg device_profile API: %s" % exc)

        if resp is None or not getattr(resp, 'status_code', None):
            raise CyborgClientError(
                "No response from Cyborg device_profile API")
        if resp.status_code >= 400:
            raise CyborgClientError(
                "Cyborg device_profile API returned %s: %s" %
                (resp.status_code, resp.text))
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CyborgClientError(
                "Cyborg device_profile API returned non-JSON: %s" % exc)

        profiles = payload.get('device_profiles') or []
        if not profiles:
            raise CyborgClientError(
                "Cyborg has no device profile named %r" % name)
        # Cyborg's v2 API returns a list; pick the first match.
        return profiles[0]


def extract_groups_from_device_profile(profile):
    """Return (accelerator_resources, required_traits) from a profile.

    A device profile's ``groups`` is a list of dicts. Each dict has
    keys like ``resources:CUSTOM_FOO`` (str-valued counts) and
    ``trait:CUSTOM_BAR`` (value ``required``). We merge across all
    groups; per-group disambiguation is Nova's job at boot.

    Unknown keys are ignored.

    :param profile: dict as returned by Cyborg
    :return: tuple (dict resource_class -> int count,
                    list of trait strings)
    """
    accel = {}
    traits = []
    for group in profile.get('groups') or []:
        for key, value in group.items():
            if key.startswith('resources:'):
                rc = key[len('resources:'):]
                if rc in ('VCPU', 'MEMORY_MB', 'DISK_GB'):
                    # standard compute resources -- skip; the flavor
                    # carries those already
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    LOG.warning(
                        "Skipping non-integer resources:%s=%r in "
                        "Cyborg device profile", rc, value)
                    continue
                accel[rc] = accel.get(rc, 0) + count
            elif key.startswith('trait:'):
                if str(value).lower() == 'required':
                    traits.append(key[len('trait:'):])
    return accel, traits
