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

"""``blazar-reconcile-reservations`` -- on-demand orphan cleanup.

Operators run this when they've just hand-edited the Blazar DB or
recovered from a partial cleanup failure and don't want to wait for
the next periodic cycle. ``--lease-uuid`` is the escape hatch that
bypasses the grace-period gate (the allocation gate still applies --
we never delete a class that's actively allocated against). ``--dry-run``
prints the orphan set and exits without making changes.
"""

import sys

from oslo_config import cfg
from oslo_log import log as logging

from blazar.conf import reservation_reconciler as _cfg  # noqa: F401
from blazar.db import api as db_api
from blazar.manager import reservation_reconciler
from blazar.utils.openstack import placement
from blazar.utils import service as service_utils


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


_cli_opts = [
    cfg.BoolOpt('dry-run', default=False,
                help="List the orphan resource classes that would be "
                     "deleted, but do not delete them."),
    cfg.MultiStrOpt('lease-uuid', default=[],
                    help="Reservation UUID to force-clean. Bypasses "
                         "the grace-period gate. May be passed "
                         "multiple times. The allocation gate still "
                         "applies."),
]


class _DryRunPlacementClient(object):
    """Wraps a real placement client and no-ops the destructive calls.

    Reads pass through; ``delete_reservation_inventory`` and
    ``delete_resource_class`` are intercepted. Used only by --dry-run.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def delete_reservation_inventory(self, host_name, reserv_uuid):
        LOG.warning(
            "[dry-run] would clear inventory of %s on host %s",
            reserv_uuid, host_name)

    def delete_resource_class(self, rc_name):
        LOG.warning("[dry-run] would delete resource class %s", rc_name)


def _register_cli_opts():
    """Register CLI opts in any cfg.CONF state.

    Tolerates both 'never parsed' (production) and 'tests already
    called CONF([])' (unit tests) situations. In tests the framework
    parses argv first; we then clear() the parse state, register, and
    re-parse with our CLI argv.
    """
    try:
        CONF.register_cli_opts(_cli_opts)
    except cfg.ArgsAlreadyParsedError:
        # Tests path: reset the parsed-args sentinel so CONF accepts
        # a fresh argv with our flags.
        CONF.clear()
        CONF.register_cli_opts(_cli_opts)


def main():
    _register_cli_opts()
    cfg.CONF(sys.argv[1:], project='blazar',
             prog='blazar-reconcile-reservations')
    service_utils.prepare_service(sys.argv)
    db_api.setup_db()

    client = placement.BlazarPlacementClient()
    if CONF.dry_run:
        client = _DryRunPlacementClient(client)

    force_uuids = list(CONF.lease_uuid) if CONF.lease_uuid else None
    reconciler = reservation_reconciler.ReservationReconciler(
        placement_client=client)
    summary = reconciler.reconcile(triggered_by='cli',
                                   force_uuids=force_uuids)

    # Print to stdout for operator-facing tooling. The audit-log row
    # in reservation_cleanup_logs is the durable record.
    print("reconcile-reservations summary:")
    for k in ('detected', 'deleted', 'skipped_allocations',
              'skipped_grace', 'errors'):
        print("  %s: %d" % (k, summary.get(k, 0)))

    return 1 if summary.get('errors', 0) else 0


if __name__ == '__main__':
    sys.exit(main())
