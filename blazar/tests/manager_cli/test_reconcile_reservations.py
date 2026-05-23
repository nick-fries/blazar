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

"""Tests for the blazar-reconcile-reservations CLI."""

import io
from unittest import mock

from oslo_config import cfg
from oslo_config import fixture as conf_fixture

from blazar.cmd import reconcile_reservations as cmd
from blazar import tests


CONF = cfg.CONF


class DryRunPlacementClientTest(tests.TestCase):
    def setUp(self):
        super(DryRunPlacementClientTest, self).setUp()
        self.useFixture(conf_fixture.Config(CONF))

    def test_dry_run_does_not_delete_class(self):
        inner = mock.MagicMock()
        wrapper = cmd._DryRunPlacementClient(inner)
        wrapper.delete_resource_class('CUSTOM_RESERVATION_AAA')
        self.assertFalse(inner.delete_resource_class.called)

    def test_dry_run_does_not_clear_inventory(self):
        inner = mock.MagicMock()
        wrapper = cmd._DryRunPlacementClient(inner)
        wrapper.delete_reservation_inventory('host1', 'AAA')
        self.assertFalse(inner.delete_reservation_inventory.called)

    def test_dry_run_forwards_reads(self):
        inner = mock.MagicMock()
        inner.list_resource_classes.return_value = ['CUSTOM_RESERVATION_X']
        wrapper = cmd._DryRunPlacementClient(inner)
        out = wrapper.list_resource_classes(prefix='CUSTOM_RESERVATION_')
        self.assertEqual(['CUSTOM_RESERVATION_X'], out)
        inner.list_resource_classes.assert_called_once_with(
            prefix='CUSTOM_RESERVATION_')


def _patches_for_main(argv, recon_return):
    """Common context-manager stack for main() smoke tests."""
    p_svc = mock.patch.object(cmd.service_utils, 'prepare_service')
    p_db = mock.patch.object(cmd.db_api, 'setup_db')
    p_cli = mock.patch.object(cmd.placement, 'BlazarPlacementClient')
    p_rec = mock.patch.object(
        cmd.reservation_reconciler, 'ReservationReconciler')
    p_argv = mock.patch(
        'sys.argv', ['blazar-reconcile-reservations'] + argv)
    p_out = mock.patch('sys.stdout', new_callable=io.StringIO)
    return p_svc, p_db, p_cli, p_rec, p_argv, p_out


class CLIMainTest(tests.TestCase):
    def setUp(self):
        super(CLIMainTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))

    def _run_main(self, argv):
        p_svc, p_db, p_cli, p_rec, p_argv, p_out = _patches_for_main(argv, {})
        with p_svc, p_db, p_cli as fake_cls, p_rec as fake_rec_cls, p_argv, \
                p_out as fake_stdout:
            fake_rec = fake_rec_cls.return_value
            fake_rec.reconcile.return_value = {
                'detected': 2, 'deleted': 1,
                'skipped_allocations': 0, 'skipped_grace': 1,
                'errors': 0,
            }
            ret = cmd.main()
            return ret, fake_cls, fake_rec, fake_stdout.getvalue()

    def test_main_returns_zero_when_no_errors(self):
        ret, _, _, _ = self._run_main([])
        self.assertEqual(0, ret)

    def test_main_returns_nonzero_when_errors(self):
        p_svc, p_db, p_cli, p_rec, p_argv, p_out = _patches_for_main([], {})
        with p_svc, p_db, p_cli, p_rec as fake_rec_cls, p_argv, p_out:
            fake_rec_cls.return_value.reconcile.return_value = {
                'detected': 1, 'deleted': 0,
                'skipped_allocations': 0, 'skipped_grace': 0,
                'errors': 1,
            }
            ret = cmd.main()
        self.assertEqual(1, ret)

    def test_main_dry_run_wraps_placement_client(self):
        p_svc, p_db, p_cli, p_rec, p_argv, p_out = _patches_for_main(
            ['--dry-run'], {})
        with p_svc, p_db, p_cli, p_rec as fake_rec_cls, p_argv, p_out:
            fake_rec_cls.return_value.reconcile.return_value = {
                'detected': 0, 'deleted': 0,
                'skipped_allocations': 0, 'skipped_grace': 0,
                'errors': 0,
            }
            cmd.main()
            ctor_kwargs = fake_rec_cls.call_args.kwargs
            self.assertIsInstance(
                ctor_kwargs['placement_client'],
                cmd._DryRunPlacementClient)

    def test_main_passes_force_uuids(self):
        _, _, fake_rec, _ = self._run_main(
            ['--lease-uuid', 'aaa-111', '--lease-uuid', 'bbb-222'])
        call = fake_rec.reconcile.call_args
        self.assertEqual('cli', call.kwargs.get('triggered_by'))
        self.assertEqual(['aaa-111', 'bbb-222'],
                         call.kwargs.get('force_uuids'))

    def test_main_no_force_uuids_passes_none(self):
        _, _, fake_rec, _ = self._run_main([])
        call = fake_rec.reconcile.call_args
        self.assertIsNone(call.kwargs.get('force_uuids'))

    def test_main_prints_summary(self):
        _, _, _, output = self._run_main([])
        self.assertIn('reconcile-reservations summary:', output)
        self.assertIn('detected: 2', output)
        self.assertIn('deleted: 1', output)
        self.assertIn('skipped_grace: 1', output)
