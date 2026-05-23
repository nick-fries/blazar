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

"""Aggregate point for new-style oslo_config opt groups.

Historically Blazar registered all opts in :mod:`blazar.config` and the
per-module utility files. This subpackage is the new location for
groups introduced in stable/2026.1 and later. Each submodule must
expose ``register_opts(conf)`` and ``list_opts()``; the package's own
:func:`register_opts` walks the submodules so callers (config bootstrap,
opts.py for the sample config) only need one import.
"""

from blazar.conf import reservation_reconciler


_GROUPS = (
    reservation_reconciler,
)


def register_opts(conf):
    for mod in _GROUPS:
        mod.register_opts(conf)


def list_opts():
    out = {}
    for mod in _GROUPS:
        out.update(mod.list_opts())
    return out


__all__ = ['register_opts', 'list_opts']
