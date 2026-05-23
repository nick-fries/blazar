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

"""Config opts for the orphan-reservation reconciler.

These knobs are read by :mod:`blazar.manager.reservation_reconciler` and
by :mod:`blazar.manager.service` when wiring the periodic worker.
"""

from oslo_config import cfg


GROUP = 'reservation_reconciler'

opts = [
    cfg.BoolOpt(
        'enabled',
        default=True,
        help="Enable periodic reconciliation of orphan "
             "CUSTOM_RESERVATION_<uuid> resource classes in Placement. "
             "Set False to disable the background sweep entirely; the "
             "blazar-reconcile-reservations CLI command still works."),
    cfg.IntOpt(
        'cycle_interval_seconds',
        default=3600,
        min=60,
        help="Seconds between reconciliation cycles. Lower values "
             "catch orphans sooner at the cost of more Placement load."),
    cfg.IntOpt(
        'orphan_grace_period_seconds',
        default=600,
        min=0,
        help="Time a CUSTOM_RESERVATION_<uuid> resource class must "
             "remain unmatched by any known Blazar reservation before "
             "deletion is allowed. Protects against a transient gap "
             "during reservation creation where Placement state lands "
             "before the Blazar DB row commits."),
]


def register_opts(conf):
    conf.register_group(cfg.OptGroup(GROUP))
    conf.register_opts(opts, group=GROUP)


def list_opts():
    return {GROUP: opts}


# Register on import so that ``from blazar.conf import
# reservation_reconciler`` is enough to make CONF.reservation_reconciler
# resolve, matching the convention used elsewhere in the codebase
# (e.g. blazar.utils.openstack.placement).
register_opts(cfg.CONF)
