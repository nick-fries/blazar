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

"""Manager-service wiring tests for the orphan-reservation reconciler."""

from unittest import mock

from oslo_config import cfg
from oslo_config import fixture as conf_fixture

from blazar.conf import reservation_reconciler as _opts  # noqa: F401
from blazar.manager import service as manager_service
from blazar import tests


CONF = cfg.CONF


class _BareManager(object):
    def __init__(self):
        self._reservation_reconciler = None


class PeriodicWorkerWiringTest(tests.TestCase):
    def setUp(self):
        super(PeriodicWorkerWiringTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))

    def test_disabled_does_nothing(self):
        self.cfg.config(group='reservation_reconciler', enabled=False)
        bare = _BareManager()
        manager_service.ManagerService._reservation_reconciler_thread(
            bare)
        self.assertIsNone(bare._reservation_reconciler)

    def test_enabled_instantiates_once_and_reuses(self):
        self.cfg.config(group='reservation_reconciler', enabled=True)
        bare = _BareManager()
        with mock.patch.object(
                manager_service._rsv_reconciler,
                'ReservationReconciler') as fake_cls:
            with mock.patch(
                    'blazar.utils.openstack.placement'
                    '.BlazarPlacementClient'):
                fake_cls.return_value.reconcile.return_value = {
                    'detected': 0, 'deleted': 0,
                    'skipped_allocations': 0, 'skipped_grace': 0,
                    'errors': 0,
                }
                manager_service.ManagerService._reservation_reconciler_thread(
                    bare)
                first = bare._reservation_reconciler
                manager_service.ManagerService._reservation_reconciler_thread(
                    bare)
                self.assertIs(first, bare._reservation_reconciler)
                self.assertEqual(1, fake_cls.call_count)
                self.assertEqual(
                    2, fake_cls.return_value.reconcile.call_count)

    def test_swallows_exceptions(self):
        self.cfg.config(group='reservation_reconciler', enabled=True)
        bare = _BareManager()
        with mock.patch.object(
                manager_service._rsv_reconciler,
                'ReservationReconciler') as fake_cls:
            with mock.patch(
                    'blazar.utils.openstack.placement'
                    '.BlazarPlacementClient'):
                fake_cls.return_value.reconcile.side_effect = (
                    RuntimeError("placement gone"))
                manager_service.ManagerService._reservation_reconciler_thread(
                    bare)
                self.assertIsNotNone(bare._reservation_reconciler)
