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

"""Reconcile orphaned CUSTOM_RESERVATION_<uuid> resource classes.

Blazar registers a ``CUSTOM_RESERVATION_<uuid>`` resource class in
Placement for every instance reservation. Three paths can leave the
class behind without a matching Blazar reservation row:

1. *Partial cleanup*. The plugin tears Placement state down in
   :py:meth:`VirtualInstancePlugin.on_end`; a Placement 5xx between
   ``delete_reservation_inventory`` and ``delete_reservation_class``
   leaves either per-host inventory or the class itself behind. Today
   the surrounding ``except`` in :mod:`blazar.manager.service` only
   catches ``BlazarDBException`` and ``RuntimeError``; HTTP errors
   slip through and the next cycle never retries.

2. *State-machine bypass*. An operator runs ``DELETE FROM leases ...``
   directly in MySQL. Blazar never runs its cleanup; Placement keeps
   the class forever.

3. *Process crash* between ``plugin.on_end()`` and
   ``db_api.lease_destroy()``. Rare, but Blazar has no recovery path.

This module's :class:`ReservationReconciler` reclaims them. It is
single-process safe (Blazar runs one manager) and reentrant within
that process: per-class grace-period state lives on the instance and
the wall-clock guard prevents two concurrent cycles from racing on the
same class.
"""

import json
import time

from oslo_config import cfg
from oslo_log import log as logging

from blazar.conf import reservation_reconciler as _opts  # noqa: F401
from blazar.db import api as db_api
from blazar import exceptions as blazar_exc
from blazar.utils.openstack import exceptions as placement_exc


CONF = cfg.CONF
LOG = logging.getLogger(__name__)

RESERVATION_RC_PREFIX = 'CUSTOM_RESERVATION_'


def _rc_name_for_uuid(uuid_str):
    """Translate a reservation UUID to its Placement resource class.

    Mirrors :py:meth:`BlazarPlacementClient.create_reservation_class`
    (uppercase, hyphens to underscores).
    """
    norm = uuid_str.upper().replace('-', '_')
    return RESERVATION_RC_PREFIX + norm


def _uuid_from_rc_name(rc_name):
    """Best-effort inverse of :func:`_rc_name_for_uuid`.

    Returns ``None`` if ``rc_name`` doesn't look like a reservation
    class. The reverse is lossy because Placement uppercases the UUID
    -- we lowercase and replace ``_`` with ``-`` to get a canonical
    UUID string back. This is only used for forensic linkage in the
    audit log; the reconciler itself never matches on it.
    """
    if not rc_name or not rc_name.startswith(RESERVATION_RC_PREFIX):
        return None
    tail = rc_name[len(RESERVATION_RC_PREFIX):]
    if len(tail) != 36 and tail.count('_') != 4:
        # Not a uuid-shaped tail. Could happen if an operator made
        # other CUSTOM_RESERVATION_* classes by hand; we still record
        # the row but with a NULL reservation_id.
        if tail.count('_') != 4:
            return None
    return tail.lower().replace('_', '-')


class ReservationReconciler(object):
    """Find and reclaim orphan CUSTOM_RESERVATION_<uuid> classes.

    :param placement_client: a
        :class:`blazar.utils.openstack.placement.BlazarPlacementClient`
        (or any object with the same surface). Injected for testability.
    :param db_api_module: the Blazar DB API module. Defaults to
        :mod:`blazar.db.api`; tests inject a fake.
    """

    def __init__(self, placement_client, db_api_module=None,
                 time_func=time.time):
        self.placement_client = placement_client
        # The spec calls the kwarg ``db_api`` for clarity at the call
        # site. Internally we keep both names so existing callers in
        # tests don't break if they pass either.
        self._db_api = db_api_module or db_api
        self._time = time_func
        # class_name -> first-seen wall-clock seconds. Reset across
        # cycles for entries that are no longer orphaned.
        self._seen_orphans = {}

    # Public alias matching the spec's ``db_api`` kwarg.
    @property
    def db_api(self):
        return self._db_api

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(self,  # noqa: C901
                  triggered_by='periodic', force_uuids=None):
        """Run one reconciliation cycle.

        :param triggered_by: 'periodic' or 'cli'. Recorded in the audit
                             log; also controls whether the
                             grace-period gate applies.
        :param force_uuids: optional iterable of reservation UUIDs to
                            force-clean (bypass grace period; the
                            allocation gate still applies). Class
                            names not in the discovered orphan set are
                            ignored.
        :return: summary dict ``{"detected", "deleted",
                 "skipped_allocations", "skipped_grace", "errors"}``.
        """
        summary = {
            'detected': 0,
            'deleted': 0,
            'skipped_allocations': 0,
            'skipped_grace': 0,
            'errors': 0,
        }

        # Step 1: pull all CUSTOM_RESERVATION_* classes from Placement.
        try:
            placement_rcs = set(
                self.placement_client.list_resource_classes(
                    prefix=RESERVATION_RC_PREFIX))
        except blazar_exc.BlazarException as e:
            # A failed listing is a no-op cycle -- we cannot safely
            # decide what is orphan when we can't see Placement state.
            LOG.warning(
                "ReservationReconciler: aborting cycle, failed to list "
                "resource classes from placement: %s", e)
            summary['errors'] += 1
            return summary
        except Exception as e:
            LOG.warning(
                "ReservationReconciler: aborting cycle, unexpected "
                "error listing resource classes: %s", e)
            summary['errors'] += 1
            return summary

        # Step 2: pull live reservation UUIDs from the Blazar DB.
        try:
            live_uuids = self._db_api.reservation_get_all_active_uuids()
        except Exception as e:
            LOG.warning(
                "ReservationReconciler: aborting cycle, failed to read "
                "active reservations from DB: %s", e)
            summary['errors'] += 1
            return summary
        known_rcs = {_rc_name_for_uuid(u) for u in live_uuids}

        # Step 3: set difference -- everything in Placement that we
        # have no DB row for.
        orphans = placement_rcs - known_rcs

        # Step 4: optional intersection with force_uuids (CLI path).
        force_set = None
        if force_uuids:
            force_set = {_rc_name_for_uuid(u) for u in force_uuids}
            orphans = orphans & force_set

        now = self._time()
        grace = CONF.reservation_reconciler.orphan_grace_period_seconds

        # Step 5/6/7: per-orphan loop.
        for rc_name in sorted(orphans):
            summary['detected'] += 1
            is_forced = bool(force_set and rc_name in force_set)
            first_seen = self._seen_orphans.get(rc_name)

            # Grace-period gate (skipped entirely for CLI-forced runs).
            if not is_forced:
                if first_seen is None:
                    # First sighting of this orphan. Record it; do not
                    # delete until at least grace-period seconds have
                    # elapsed since we first saw it.
                    self._seen_orphans[rc_name] = now
                    summary['skipped_grace'] += 1
                    self._log_action(
                        rc_name=rc_name,
                        action='detected',
                        triggered_by=triggered_by,
                        details={'first_seen_at': now,
                                 'grace_period_seconds': grace})
                    continue
                elif (now - first_seen) < grace:
                    # Still inside the grace window. Don't re-log
                    # detection -- we already wrote that row on the
                    # first sighting.
                    summary['skipped_grace'] += 1
                    LOG.warning(
                        "ReservationReconciler: class %s still inside "
                        "grace window (%.1fs of %ds remaining)",
                        rc_name, grace - (now - first_seen), grace)
                    continue

            # Step 6: allocation gate. If anything still holds the
            # class, never delete.
            try:
                allocations = (
                    self.placement_client.get_allocations_for_class(rc_name))
            except Exception as e:
                LOG.warning(
                    "ReservationReconciler: skipping class %s, failed "
                    "to read allocations: %s", rc_name, e)
                summary['errors'] += 1
                self._log_action(
                    rc_name=rc_name, action='error',
                    triggered_by=triggered_by,
                    details={'error': str(e), 'stage': 'allocations'})
                continue

            if allocations:
                consumer_uuids = sorted(
                    {a.get('consumer_uuid') for a in allocations
                     if a.get('consumer_uuid')})
                LOG.warning(
                    "ReservationReconciler: class %s has %d live "
                    "allocation(s); refusing to delete. consumers=%s",
                    rc_name, len(allocations), consumer_uuids)
                summary['skipped_allocations'] += 1
                self._log_action(
                    rc_name=rc_name, action='skipped_allocations',
                    triggered_by=triggered_by,
                    details={'allocations': len(allocations),
                             'consumers': consumer_uuids})
                continue

            # Find host RPs that still carry inventory of this class
            # and clear them one at a time. Per-host failures are
            # logged but do not abort the cycle; a class with at least
            # one host successfully cleared can usually still be
            # deleted on the next pass.
            try:
                hosts = (
                    self.placement_client
                    .list_resource_providers_with_inventory(rc_name))
            except Exception as e:
                LOG.warning(
                    "ReservationReconciler: skipping class %s, failed "
                    "to list hosts with inventory: %s", rc_name, e)
                summary['errors'] += 1
                self._log_action(
                    rc_name=rc_name, action='error',
                    triggered_by=triggered_by,
                    details={'error': str(e), 'stage': 'list_hosts'})
                continue

            uuid_part = rc_name[len(RESERVATION_RC_PREFIX):]
            hosts_cleared = []
            hosts_failed = []
            for host in hosts:
                # Blazar's reservation provider name is "blazar_<host>";
                # delete_reservation_inventory takes the bare host name
                # and looks the child RP up internally. The RP entries
                # we see are the blazar_<host> children, so strip the
                # prefix if present so the placement client's lookup
                # finds the same RP we just saw.
                host_name = host.get('name') or ''
                if host_name.startswith('blazar_'):
                    host_name = host_name[len('blazar_'):]
                try:
                    self.placement_client.delete_reservation_inventory(
                        host_name, uuid_part)
                    hosts_cleared.append(host_name)
                except Exception as e:
                    LOG.warning(
                        "ReservationReconciler: failed to clear "
                        "inventory of %s on host %s: %s",
                        rc_name, host_name, e)
                    hosts_failed.append(
                        {'host': host_name, 'error': str(e)})

            # Step 6 final: drop the class itself. 409 means a
            # consumer reappeared between the allocation check and now
            # -- treat as skipped_allocations.
            try:
                self.placement_client.delete_resource_class(rc_name)
            except placement_exc.ResourceClassDeletionFailed as e:
                # The placement client raises this generically on any
                # non-2xx delete. Re-fetch allocations to disambiguate
                # 409-because-allocations from a real error.
                try:
                    retry_allocs = (
                        self.placement_client.get_allocations_for_class(
                            rc_name))
                except Exception:
                    retry_allocs = []
                if retry_allocs:
                    LOG.warning(
                        "ReservationReconciler: class %s gained "
                        "allocations during cleanup (race); skipping. "
                        "consumers=%s",
                        rc_name,
                        sorted({a.get('consumer_uuid')
                                for a in retry_allocs
                                if a.get('consumer_uuid')}))
                    summary['skipped_allocations'] += 1
                    self._log_action(
                        rc_name=rc_name, action='skipped_allocations',
                        triggered_by=triggered_by,
                        details={'allocations': len(retry_allocs),
                                 'hosts_cleared': hosts_cleared,
                                 'race': True})
                else:
                    LOG.warning(
                        "ReservationReconciler: failed to delete "
                        "resource class %s: %s", rc_name, e)
                    summary['errors'] += 1
                    self._log_action(
                        rc_name=rc_name, action='error',
                        triggered_by=triggered_by,
                        details={'error': str(e),
                                 'stage': 'delete_class',
                                 'hosts_cleared': hosts_cleared,
                                 'hosts_failed': hosts_failed})
                continue
            except Exception as e:
                LOG.warning(
                    "ReservationReconciler: unexpected error deleting "
                    "resource class %s: %s", rc_name, e)
                summary['errors'] += 1
                self._log_action(
                    rc_name=rc_name, action='error',
                    triggered_by=triggered_by,
                    details={'error': str(e), 'stage': 'delete_class',
                             'hosts_cleared': hosts_cleared,
                             'hosts_failed': hosts_failed})
                continue

            LOG.warning(
                "Reservation reconciler: deleted orphan resource class "
                "%s (triggered_by=%s, hosts_cleared=%d)",
                rc_name, triggered_by, len(hosts_cleared))
            summary['deleted'] += 1
            self._log_action(
                rc_name=rc_name, action='class_deleted',
                triggered_by=triggered_by,
                details={'hosts_cleared': hosts_cleared,
                         'hosts_failed': hosts_failed})

        # Step 7: forget entries that are no longer orphaned.
        for stale in [k for k in self._seen_orphans if k not in orphans]:
            del self._seen_orphans[stale]

        LOG.warning(
            "ReservationReconciler: cycle complete triggered_by=%s "
            "summary=%s", triggered_by, summary)
        return summary

    # ------------------------------------------------------------------
    # Audit log helper
    # ------------------------------------------------------------------

    def _log_action(self, rc_name, action, triggered_by, details=None):
        """Write one row to reservation_cleanup_logs.

        We deliberately swallow DB errors here -- losing an audit row
        must not prevent the reconciler from continuing to clean up
        Placement state. The WARN-level log line above each call is
        the durable backup.
        """
        try:
            payload = {
                'resource_class_name': rc_name,
                'reservation_id': _uuid_from_rc_name(rc_name),
                'action': action,
                'triggered_by': triggered_by,
                'details': json.dumps(details, sort_keys=True,
                                      default=str) if details else None,
            }
            self._db_api.reservation_cleanup_log_create(payload)
        except Exception as e:
            LOG.warning(
                "ReservationReconciler: failed to persist audit-log row "
                "for %s action=%s: %s", rc_name, action, e)
