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

import retrying

from keystoneauth1 import adapter
from keystoneauth1.identity import v3
from keystoneauth1 import session

from oslo_config import cfg
from oslo_log import log as logging

from blazar import context
from blazar.utils.openstack import base
from blazar.utils.openstack import exceptions

placement_opts = [
    cfg.StrOpt('endpoint_type',
               default='internal',
               choices=['public', 'admin', 'internal'],
               help='Type of the placement endpoint to use. This endpoint '
                    'will be looked up in the keystone catalog and should be '
                    'one of public, internal or admin.'),
]

CONF = cfg.CONF
CONF.register_opts(placement_opts, group='placement')
LOG = logging.getLogger(__name__)

PLACEMENT_MICROVERSION = 1.29


class BlazarPlacementClient(object):
    """Client class for updating placement."""

    def _create_client(self, **kwargs):
        """Create the HTTP session accessing the placement service."""
        ctx = kwargs.pop('ctx', None)
        username = kwargs.pop('username',
                              CONF.os_admin_username)
        user_domain_name = kwargs.pop('user_domain_name',
                                      CONF.os_admin_user_domain_name)
        project_name = kwargs.pop('project_name',
                                  CONF.os_admin_project_name)
        password = kwargs.pop('password',
                              CONF.os_admin_password)

        project_domain_name = kwargs.pop('project_domain_name',
                                         CONF.os_admin_project_domain_name)
        auth_url = kwargs.pop('auth_url', None)
        region_name = kwargs.pop('region_name', CONF.os_region_name)

        if ctx is None:
            try:
                ctx = context.current()
            except RuntimeError:
                pass
        if ctx is not None:
            kwargs.setdefault('global_request_id', ctx.global_request_id)

        if auth_url is None:
            auth_url = "%s://%s:%s" % (CONF.os_auth_protocol,
                                       base.get_os_auth_host(CONF),
                                       CONF.os_auth_port)
            if CONF.os_auth_prefix:
                auth_url += "/%s" % CONF.os_auth_prefix
            if CONF.os_auth_version:
                auth_url += "/%s" % CONF.os_auth_version

        auth = v3.Password(auth_url=auth_url,
                           username=username,
                           password=password,
                           project_name=project_name,
                           user_domain_name=user_domain_name,
                           project_domain_name=project_domain_name)
        sess_kwargs = dict(
            auth=auth
        )
        if CONF.cafile:
            sess_kwargs.update(verify=CONF.cafile)
        sess = session.Session(**sess_kwargs)
        # Set accept header on every request to ensure we notify placement
        # service of our response body media type preferences.
        headers = {'accept': 'application/json'}
        kwargs.setdefault('service_type', 'placement')
        kwargs.setdefault('interface', CONF.placement.endpoint_type)
        kwargs.setdefault('additional_headers', headers)
        kwargs.setdefault('region_name', region_name)
        client = adapter.Adapter(sess, **kwargs)
        return client

    def get(self, url, microversion=PLACEMENT_MICROVERSION):
        client = self._create_client()
        return client.get(url, raise_exc=False,
                          microversion=microversion)

    def post(self, url, data, microversion=PLACEMENT_MICROVERSION):
        client = self._create_client()
        return client.post(url, json=data, raise_exc=False,
                           microversion=microversion)

    def put(self, url, data, microversion=PLACEMENT_MICROVERSION):
        client = self._create_client()
        return client.put(url, json=data, raise_exc=False,
                          microversion=microversion)

    def delete(self, url, microversion=PLACEMENT_MICROVERSION):
        client = self._create_client()
        return client.delete(url, raise_exc=False,
                             microversion=microversion)

    def get_resource_provider(self, rp_name):
        """Calls the placement API for a resource provider record.

        :param rp_name: Name of the resource provider
        :return: A dict of resource provider information
                 or None if the resource provider doesn't exist.
        :raise: ResourceProviderRetrievalFailed on error.
        """
        url = "/resource_providers?name=%s" % rp_name
        resp = self.get(url)
        if resp:
            json_resp = resp.json()
            if json_resp['resource_providers']:
                return json_resp['resource_providers'][0]
            else:
                return None

        msg = ("Failed to get resource provider %(name)s. "
               "Got %(status_code)d: %(err_text)s.")
        args = {
            'name': rp_name,
            'status_code': resp.status_code,
            'err_text': resp.text,
        }
        LOG.error(msg, args)
        raise exceptions.ResourceProviderRetrievalFailed(name=rp_name)

    def list_resource_providers(
            self, query="", microversion=PLACEMENT_MICROVERSION):
        """Get all resource providers.

        :param query: A string of query parameters.
        :param microversion: The microversion to use for the request.
        :return: A list of resource provider information
        :raise: ResourceProviderListFailed on error.
        """
        resp = self.get(
            f'/resource_providers?{query}', microversion=microversion)
        if resp:
            json_resp = resp.json()
            if json_resp['resource_providers']:
                return json_resp['resource_providers']
            else:
                return []

        msg = ("Failed to get resource providers. "
               "Got %(status_code)d: %(err_text)s.")
        args = {
            'status_code': resp.status_code,
            'err_text': resp.text,
        }
        LOG.error(msg, args)
        raise exceptions.ResourceProviderListFailed()

    def create_resource_provider(self, rp_name, rp_uuid=None,
                                 parent_uuid=None):
        """Calls the placement API to create a new resource provider record.

        :param rp_name: Name of the resource provider
        :param rp_uuid: Optional UUID of the new resource provider
        :param parent_uuid: Optional UUID of the parent resource provider
        :return: A dict of resource provider information object representing
                 the newly-created resource provider.
        :raise: ResourceProviderCreationFailed error.
        """
        url = "/resource_providers"
        payload = {'name': rp_name}
        if rp_uuid is not None:
            payload['uuid'] = rp_uuid
        if parent_uuid is not None:
            payload['parent_provider_uuid'] = parent_uuid

        resp = self.post(url, payload)

        if resp:
            msg = ("Created resource provider record via placement API for "
                   "resource provider %(name)s.")
            args = {'name': rp_name}
            LOG.info(msg, args)
            return resp.json()

        if resp.status_code == 409:
            msg = ("Conflict on creating resource provider %(name)s in "
                   "placement API. Got %(status_code)d: %(err_text)s.")
            args = {
                'name': rp_name,
                'status_code': resp.status_code,
                'err_text': resp.text,
            }
            LOG.error(msg, args)
            raise exceptions.ResourceProviderCreationConflict(name=rp_name)

        msg = ("Failed to create resource provider record in placement API "
               "for resource provider %(name)s. "
               "Got %(status_code)d: %(err_text)s.")
        args = {
            'name': rp_name,
            'status_code': resp.status_code,
            'err_text': resp.text,
        }
        LOG.error(msg, args)
        raise exceptions.ResourceProviderCreationFailed(name=rp_name)

    def delete_resource_provider(self, rp_uuid):
        """Calls the placement API to delete a resource provider.

        :param rp_uuid: UUID of the resource provider to delete
        :raise: ResourceProviderDeletionFailed error
        """
        url = '/resource_providers/%s' % rp_uuid
        resp = self.delete(url)

        if resp:
            LOG.info("Deleted resource provider %s", rp_uuid)
            return

        msg = ("Failed to delete resource provider with UUID %(uuid)s from "
               "the placement API. Got %(status_code)d: %(err_text)s.")
        args = {
            'uuid': rp_uuid,
            'status_code': resp.status_code,
            'err_text': resp.text
        }
        LOG.error(msg, args)
        raise exceptions.ResourceProviderDeletionFailed(uuid=rp_uuid)

    def create_reservation_provider(self, host_name):
        """Create a reservation provider as a child of the given host"""
        host_rp = self.get_resource_provider(host_name)
        if host_rp is None:
            raise exceptions.ResourceProviderNotFound(
                resource_provider=host_name)
        host_uuid = host_rp['uuid']
        rp_name = "blazar_" + host_name

        reservation_rp = self.create_resource_provider(
            rp_name, parent_uuid=host_uuid)
        return reservation_rp

    def delete_reservation_provider(self, host_name):
        """Delete the reservation provider, the child of the given host"""
        rp_name = "blazar_" + host_name
        rp = self.get_resource_provider(rp_name)
        if rp is None:
            # If the reservation provider doesn't exist,
            # no operation will be performed.
            return
        rp_uuid = rp['uuid']
        self.delete_resource_provider(rp_uuid)

    def create_resource_class(self, rc_name):
        """Calls the placement API to create a resource class.

        :param rc_name: string name of the resource class to create. This
                        shall be something like "CUSTOM_RESERVATION_{uuid}".
        :raises: ResourceClassCreationFailed error.
        """
        url = '/resource_classes'
        payload = {'name': rc_name}
        resp = self.post(url, payload)
        if resp:
            LOG.info("Created resource class %s", rc_name)
            return
        msg = ("Failed to create resource class with placement API for "
               "%(rc_name)s. Got %(status_code)d: %(err_text)s.")
        args = {
            'rc_name': rc_name,
            'status_code': resp.status_code,
            'err_text': resp.text,
        }
        LOG.error(msg, args)
        raise exceptions.ResourceClassCreationFailed(resource_class=rc_name)

    def delete_resource_class(self, rc_name):
        """Calls the placement API to delete a resource class.

        :param rc_name: string name of the resource class to delete. This
                        shall be something like "CUSTOM_RESERVATION_{uuid}"
        :raises: ResourceClassDeletionFailed error.
        """
        url = '/resource_classes/%s' % rc_name
        resp = self.delete(url)
        if resp:
            LOG.info("Deleted resource class %s", rc_name)
            return
        msg = ("Failed to delete resource class with placement API for "
               "%(rc_name)s. Got %(status_code)d: %(err_text)s.")
        args = {
            'rc_name': rc_name,
            'status_code': resp.status_code,
            'err_text': resp.text,
        }
        LOG.error(msg, args)
        raise exceptions.ResourceClassDeletionFailed(resource_class=rc_name)

    def create_reservation_class(self, reservation_uuid):
        """Create the reservation class from the given reservation uuid"""
        # Placement API doesn't accept resource classes with lower characters
        # and "-"(hyphen) in its name. We should translate the uuid here.
        reservation_uuid = reservation_uuid.upper().replace("-", "_")
        rc_name = 'CUSTOM_RESERVATION_' + reservation_uuid
        self.create_resource_class(rc_name)

    def delete_reservation_class(self, reservation_uuid):
        """Delete the reservation class from the given reservation uuid"""
        # Placement API doesn't accept resource classes with lower characters
        # and "-"(hyphen) in its name. We should translate the uuid here.
        reservation_uuid = reservation_uuid.upper().replace("-", "_")
        rc_name = 'CUSTOM_RESERVATION_' + reservation_uuid
        try:
            self.delete_resource_class(rc_name)
        except exceptions.ResourceClassDeletionFailed:
            # We just log it and skip to keep the compatibility before Stein
            LOG.info("Resource class %s doesn't exist. Skipped the deletion "
                     "of the resource class", rc_name)

    def get_inventory(self, rp_uuid):
        """Calls the placement API to get resource inventory information.

        :param rp_uuid: UUID of the resource provider to get
        """
        url = '/resource_providers/%s/inventories' % rp_uuid
        resp = self.get(url)
        if resp:
            return resp.json()
        raise exceptions.ResourceProviderNotFound(resource_provider=rp_uuid)

    def get_traits(self, rp_uuid):
        """Calls the placement API to get resource trait information.

        :param rp_uuid: UUID of the resource provider to get
        """
        url = '/resource_providers/%s/traits' % rp_uuid
        resp = self.get(url)
        if resp:
            return resp.json().get("traits", [])
        raise exceptions.ResourceProviderNotFound(resource_provider=rp_uuid)

    @retrying.retry(stop_max_attempt_number=5,
                    retry_on_exception=lambda e: isinstance(
                        e, exceptions.InventoryConflict))
    def update_inventory(self, rp_uuid, rc_name, num, additional):
        """Update the inventory for the resource provider.

        :param rp_uuid: The resource provider UUID for the operation
        :param rc_name: The resource class name of the inventory to update
        :param num: The total inventory to add/update
        :param additional: Add the given number amounts to the existing if
                           True, else just overwrite the total value
        :raises: ResourceProviderNotFound or InventoryUpdateFailed error.
        """
        curr = self.get_inventory(rp_uuid)
        inventories = curr['inventories']
        generation = curr['resource_provider_generation']

        if additional and rc_name in inventories:
            inventories[rc_name]["total"] += num

        else:
            inv_data = {
                rc_name: {
                    "allocation_ratio": 1.0,
                    "max_unit": 1,
                    "min_unit": 1,
                    "reserved": 0,
                    "step_size": 1,
                    "total": num
                },
            }
            inventories.update(inv_data)

        payload = {
            'inventories': inventories,
            'resource_provider_generation': generation,
        }
        url = '/resource_providers/%s/inventories' % rp_uuid

        resp = self.put(url, payload)
        if resp:
            return resp.json()

        if resp.status_code == 409:
            err = resp.json()['errors'][0]
            if err['code'] == 'placement.concurrent_update':
                # NOTE(tetsuro): Another thread updated the inventory of the
                # same rp during the get_inventory() and the put(). We simply
                # retry it for this case.
                msg = ("Conflict on updating inventory in placement. "
                       "Got %(status_code)d: %(err_text)s. ")
                args = {
                    'status_code': resp.status_code,
                    'err_text': resp.text,
                }
                LOG.error(msg, args)
                raise exceptions.InventoryConflict(resource_provider=rp_uuid)

        raise exceptions.InventoryUpdateFailed(resource_provider=rp_uuid)

    def delete_inventory(self, rp_uuid, rc_name):
        """Delete the inventory for the resource provider.

        :param rp_uuid: The resource provider UUID for the operation
        :param rc_name: The resource class name to delete from inventory
        :raises: InventoryUpdateFailed error
        """
        url = '/resource_providers/%s/inventories/%s' % (rp_uuid, rc_name)

        resp = self.delete(url)
        if resp:
            return

        raise exceptions.InventoryUpdateFailed(resource_provider=rp_uuid)

    def update_reservation_inventory(self, host_name, reserv_uuid, num,
                                     additional=False):
        """Update the reservation inventory for the reservation provider.

        :param host_name: The name of the target host
        :param reserv_uuid: The reservation uuid
        :param num: The number of the instances to reserve on the host
        :return: The updated inventory record
        """
        # Get reservation provider uuid
        rp_name = "blazar_" + host_name
        rp = self.get_resource_provider(rp_name)
        if rp is None:
            # If the reservation provider is not created yet,
            # this function creates it.
            rp = self.create_reservation_provider(host_name)
        rp_uuid = rp['uuid']

        # Get resource class name
        reserv_uuid = reserv_uuid.upper().replace("-", "_")
        rc_name = 'CUSTOM_RESERVATION_' + reserv_uuid

        return self.update_inventory(rp_uuid, rc_name, num, additional)

    def delete_reservation_inventory(self, host_name, reserv_uuid):
        """Delete the reservation inventory for the reservation provider.

        :param host_name: The name of the target host
        :param reserv_uuid: The reservation uuid
        :raises: ResourceProviderNotFound if the reservation
                 provider is not found
        """
        # Get reservation provider uuid
        rp_name = "blazar_" + host_name
        rp = self.get_resource_provider(rp_name)
        if rp is None:
            raise exceptions.ResourceProviderNotFound(
                resource_provider=rp_name)
        rp_uuid = rp['uuid']

        # Convert reservation uuid to resource class name
        reserv_uuid = reserv_uuid.upper().replace("-", "_")
        rc_name = 'CUSTOM_RESERVATION_' + reserv_uuid
        try:
            self.delete_inventory(rp_uuid, rc_name)
        except exceptions.InventoryUpdateFailed:
            # We just log it and skip to keep the compatibility before Stein
            LOG.info("Resource class %s doesn't exist or there is no "
                     "inventory for that resource class on resource provider "
                     "%s. Skipped the deletion", rc_name, rp_name)
    # ------------------------------------------------------------------
    # Topology-aware reservation helpers
    #
    # These methods support pre-flight feasibility checks at reservation
    # creation time. They read the Placement resource provider tree for
    # a candidate compute host and summarise inventory, traits, and
    # topology anchors across all sub-RPs (PCI device RPs Nova writes,
    # accelerator RPs Cyborg writes, NIC RPs Neutron writes, plus the
    # socket/NUMA anchor sub-RPs the topology-aware Cyborg driver writes).
    #
    # Nothing here writes Placement state. Item 3 (Placement reservation
    # holds) is intentionally not implemented.
    # ------------------------------------------------------------------

    def get_host_rp_tree(self, host_name):
        """Read the full RP subtree rooted at the compute host.

        :param host_name: hypervisor_hostname (matches the Nova root RP name)
        :return: dict keyed by rp_uuid with entries
                 {'uuid', 'name', 'parent_uuid', 'traits' (list of str),
                  'inventory' (dict resource_class -> inventory record)}
                 If the host RP is not found, returns {}.
        """
        host_rp = self.get_resource_provider(host_name)
        if host_rp is None:
            return {}

        host_uuid = host_rp['uuid']
        # in_tree=<host_uuid> returns the root and all descendants.
        # Placement microversion 1.14+ supports in_tree; we pin 1.29
        # which is well above that.
        rps = self.list_resource_providers(
            query="in_tree=%s" % host_uuid)

        tree = {}
        for rp in rps:
            rp_uuid = rp['uuid']
            try:
                inv = self.get_inventory(rp_uuid).get('inventories', {})
            except exceptions.ResourceProviderNotFound:
                inv = {}
            try:
                traits = self.get_traits(rp_uuid)
            except exceptions.ResourceProviderNotFound:
                traits = []
            tree[rp_uuid] = {
                'uuid': rp_uuid,
                'name': rp.get('name'),
                'parent_uuid': rp.get('parent_provider_uuid'),
                'traits': list(traits),
                'inventory': inv,
            }
        return tree

    def get_allocations_for_rp(self, rp_uuid):
        """Return total used per resource class on a single RP.

        :param rp_uuid: RP UUID
        :return: dict resource_class -> used (int)
        """
        url = '/resource_providers/%s/usages' % rp_uuid
        resp = self.get(url)
        if not resp:
            return {}
        return resp.json().get('usages', {}) or {}

    @staticmethod
    def _rp_is_blazar_reservation(rp_entry):
        """True if the RP is a Blazar reservation sub-RP.

        Blazar's existing code creates 'blazar_<host_name>' sub-RPs to
        hold CUSTOM_RESERVATION_<uuid> inventory. We must exclude these
        from accelerator-availability accounting because they don't
        represent real hardware.
        """
        name = (rp_entry.get('name') or '')
        return name.startswith('blazar_')

    def get_accelerator_inventory_for_host(self, host_name,
                                           resource_classes,
                                           rp_tree=None):
        """Sum accelerator inventory and usage across the host RP subtree.

        :param host_name: hypervisor_hostname (used only if rp_tree is None)
        :param resource_classes: iterable of resource class names to sum
        :param rp_tree: optional pre-fetched RP tree from
                        :py:meth:`get_host_rp_tree`
        :return: dict resource_class -> {'total': int, 'used': int}.
                 Blazar's own blazar_<host> reservation RPs are excluded.
                 Resource classes absent from the tree report
                 {'total': 0, 'used': 0}.
        """
        if rp_tree is None:
            rp_tree = self.get_host_rp_tree(host_name)

        result = {rc: {'total': 0, 'used': 0} for rc in resource_classes}
        for rp_uuid, rp_entry in rp_tree.items():
            if self._rp_is_blazar_reservation(rp_entry):
                continue
            inv = rp_entry.get('inventory', {})
            usages = None
            for rc in resource_classes:
                if rc in inv:
                    total = inv[rc].get('total', 0)
                    reserved = inv[rc].get('reserved', 0)
                    result[rc]['total'] += max(0, int(total) - int(reserved))
                    if usages is None:
                        usages = self.get_allocations_for_rp(rp_uuid)
                    result[rc]['used'] += int(usages.get(rc, 0))
        return result

    def host_has_anchor(self, host_name, anchor_trait, rp_tree=None):
        """Return True iff the host RP subtree contains at least one
        sub-RP carrying ``anchor_trait``.

        Anchor traits are written by the topology-aware Cyborg driver
        onto sub-RPs that represent a physical socket
        (CUSTOM_SOCKET_ROOT) or NUMA node (HW_NUMA_ROOT) under the
        compute host. Their presence is a pre-flight signal that the
        host has the requested topology kind at all -- the actual
        same_subtree= co-location decision is Nova's at boot time.
        """
        if rp_tree is None:
            rp_tree = self.get_host_rp_tree(host_name)
        for rp_entry in rp_tree.values():
            if self._rp_is_blazar_reservation(rp_entry):
                continue
            if anchor_trait in (rp_entry.get('traits') or []):
                return True
        return False

    def host_subtree_has_traits(self, host_name, required_traits,
                                rp_tree=None):
        """Return True iff the union of traits across every non-Blazar
        sub-RP in the host's tree is a superset of ``required_traits``.

        Used for pre-flight trait matching at reservation time. This is
        a coarse check -- placement still enforces per-request-group
        traits at boot. We only need to know "is this trait present
        somewhere in the host's tree" to reject obviously-incompatible
        hosts.
        """
        if not required_traits:
            return True
        if rp_tree is None:
            rp_tree = self.get_host_rp_tree(host_name)
        union = set()
        for rp_entry in rp_tree.values():
            if self._rp_is_blazar_reservation(rp_entry):
                continue
            union.update(rp_entry.get('traits') or [])
        return set(required_traits).issubset(union)
