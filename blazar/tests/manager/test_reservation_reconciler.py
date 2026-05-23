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

"""Tests for the orphan-reservation reconciler.

Assertions go after observable state: which classes the fake
placement actually deleted, which audit-log rows the fake DB
accumulated, and the returned summary dict. Mock call-count is
deliberately avoided -- behaviour-driven assertions catch regressions
that "mock was called with X" assertions paper over.
"""

import json

from oslo_config import cfg
from oslo_config import fixture as conf_fixture

from blazar.conf import reservation_reconciler as _opts  # noqa: F401
from blazar.manager import reservation_reconciler as rr
from blazar import tests
from blazar.utils.openstack import exceptions as placement_exc


CONF = cfg.CONF


class _FakePlacement(object):
    def __init__(self):
        self.classes = set()
        self.allocations = {}
        self.inventory_by_host = {}
        self.deleted_classes = []
        self.cleared_inventory = []
        self.list_classes_raises = None
        self.delete_class_raises = {}
        self.delete_inventory_raises = {}
        self.allocations_raises = None

    def list_resource_classes(self, prefix=None):
        if self.list_classes_raises:
            raise self.list_classes_raises
        out = list(self.classes)
        if prefix is not None:
            out = [c for c in out if c.startswith(prefix)]
        return sorted(out)

    def get_allocations_for_class(self, rc_name):
        if self.allocations_raises:
            raise self.allocations_raises
        return list(self.allocations.get(rc_name, []))

    def list_resource_providers_with_inventory(self, rc_name):
        return list(self.inventory_by_host.get(rc_name, []))

    def delete_reservation_inventory(self, host_name, reserv_uuid):
        exc = self.delete_inventory_raises.get(host_name)
        if exc:
            raise exc
        self.cleared_inventory.append((host_name, reserv_uuid))

    def delete_resource_class(self, rc_name):
        exc = self.delete_class_raises.get(rc_name)
        if exc:
            raise exc
        self.classes.discard(rc_name)
        self.deleted_classes.append(rc_name)


class _FakeDB(object):
    def __init__(self, live_uuids=None):
        self._live = list(live_uuids or [])
        self.cleanup_rows = []
        self.live_uuids_raises = None
        self.create_raises = None

    def reservation_get_all_active_uuids(self):
        if self.live_uuids_raises:
            raise self.live_uuids_raises
        return list(self._live)

    def reservation_cleanup_log_create(self, values):
        if self.create_raises:
            raise self.create_raises
        self.cleanup_rows.append(values)
        return values


class _Clock(object):
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _make_reconciler(placement, db, clock=None):
    return rr.ReservationReconciler(
        placement_client=placement,
        db_api_module=db,
        time_func=clock or _Clock())


def _rc(uuid_str):
    return rr._rc_name_for_uuid(uuid_str)


class ReconcilerDiscoveryTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerDiscoveryTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)

    def test_no_classes_in_placement_is_noop(self):
        placement = _FakePlacement()
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(
            {'detected': 0, 'deleted': 0, 'skipped_allocations': 0,
             'skipped_grace': 0, 'errors': 0},
            summary)
        self.assertEqual([], placement.deleted_classes)
        self.assertEqual([], db.cleanup_rows)

    def test_all_classes_have_live_reservations(self):
        uuid_a = '11111111-1111-1111-1111-111111111111'
        uuid_b = '22222222-2222-2222-2222-222222222222'
        placement = _FakePlacement()
        placement.classes = {_rc(uuid_a), _rc(uuid_b)}
        db = _FakeDB(live_uuids=[uuid_a, uuid_b])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(0, summary['detected'])
        self.assertEqual(0, summary['deleted'])
        self.assertEqual([], placement.deleted_classes)

    def test_non_reservation_classes_are_ignored(self):
        placement = _FakePlacement()
        placement.classes = {'CUSTOM_RESERVATION_LIVE'}
        placement.classes.add('VCPU')
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(1, summary['detected'])
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_grace'])


class ReconcilerGracePeriodTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerGracePeriodTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        self.rc_name = _rc(self.uuid)

    def _orphan_setup(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        return placement

    def test_first_sighting_records_detection_only(self):
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        summary = rec.reconcile()
        self.assertEqual(1, summary['detected'])
        self.assertEqual(1, summary['skipped_grace'])
        self.assertEqual(0, summary['deleted'])
        self.assertEqual([], placement.deleted_classes)
        self.assertEqual(1, len(db.cleanup_rows))
        row = db.cleanup_rows[0]
        self.assertEqual('detected', row['action'])
        self.assertEqual('periodic', row['triggered_by'])
        self.assertEqual(self.rc_name, row['resource_class_name'])

    def test_inside_grace_window_does_not_re_log(self):
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(120)
        summary = rec.reconcile()
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_grace'])
        actions = [r['action'] for r in db.cleanup_rows]
        self.assertEqual(['detected'], actions)

    def test_past_grace_window_deletes(self):
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        self.assertEqual([self.rc_name], placement.deleted_classes)
        actions = [r['action'] for r in db.cleanup_rows]
        self.assertEqual(['detected', 'class_deleted'], actions)

    def test_exactly_at_grace_window_deletes(self):
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(600)
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])

    def test_orphan_reappears_in_db_clears_seen_state(self):
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        self.assertIn(self.rc_name, rec._seen_orphans)
        db._live = [self.uuid]
        rec.reconcile()
        self.assertNotIn(self.rc_name, rec._seen_orphans)
        self.assertEqual(0, len(placement.deleted_classes))

    def test_zero_grace_period_deletes_second_cycle(self):
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=0)
        placement = self._orphan_setup()
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        s0 = rec.reconcile()
        s1 = rec.reconcile()
        self.assertEqual(0, s0['deleted'])
        self.assertEqual(1, s0['skipped_grace'])
        self.assertEqual(1, s1['deleted'])
        actions = [r['action'] for r in db.cleanup_rows]
        self.assertEqual(['detected', 'class_deleted'], actions)


class ReconcilerAllocationGateTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerAllocationGateTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        self.rc_name = _rc(self.uuid)

    def _aged_orphan(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)
        return placement, db, rec

    def test_one_allocation_blocks_delete(self):
        placement, db, rec = self._aged_orphan()
        placement.allocations[self.rc_name] = [
            {'consumer_uuid': 'consumer-1'}]
        summary = rec.reconcile()
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_allocations'])
        self.assertEqual([], placement.deleted_classes)
        skipped = [r for r in db.cleanup_rows
                   if r['action'] == 'skipped_allocations']
        self.assertEqual(1, len(skipped))
        details = json.loads(skipped[0]['details'])
        self.assertEqual(1, details['allocations'])
        self.assertEqual(['consumer-1'], details['consumers'])

    def test_many_allocations_blocks_delete(self):
        placement, db, rec = self._aged_orphan()
        placement.allocations[self.rc_name] = [
            {'consumer_uuid': 'c%d' % i} for i in range(5)]
        summary = rec.reconcile()
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_allocations'])
        skipped = [r for r in db.cleanup_rows
                   if r['action'] == 'skipped_allocations']
        details = json.loads(skipped[0]['details'])
        self.assertEqual(5, details['allocations'])
        self.assertEqual(5, len(details['consumers']))

    def test_zero_allocations_passes_through(self):
        placement, db, rec = self._aged_orphan()
        placement.allocations[self.rc_name] = []
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        self.assertEqual(0, summary['skipped_allocations'])
        self.assertEqual([self.rc_name], placement.deleted_classes)

    def test_allocations_query_failure_logs_error(self):
        placement, db, rec = self._aged_orphan()
        placement.allocations_raises = (
            placement_exc.ResourceClassListFailed())
        summary = rec.reconcile()
        self.assertEqual(1, summary['errors'])
        self.assertEqual(0, summary['deleted'])
        errs = [r for r in db.cleanup_rows if r['action'] == 'error']
        self.assertEqual(1, len(errs))
        details = json.loads(errs[0]['details'])
        self.assertEqual('allocations', details['stage'])


class ReconcilerHostClearTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerHostClearTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
        self.rc_name = _rc(self.uuid)

    def _aged_with_hosts(self, host_names):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        placement.inventory_by_host[self.rc_name] = [
            {'uuid': 'rp-%d' % i, 'name': 'blazar_%s' % h}
            for i, h in enumerate(host_names)]
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)
        return placement, db, rec

    def test_no_hosts_still_deletes_class(self):
        placement, db, rec = self._aged_with_hosts([])
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        self.assertEqual([], placement.cleared_inventory)

    def test_one_host_inventory_cleared_then_class_deleted(self):
        placement, db, rec = self._aged_with_hosts(['host1'])
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        uuid_part = self.rc_name[len(rr.RESERVATION_RC_PREFIX):]
        self.assertEqual([('host1', uuid_part)],
                         placement.cleared_inventory)
        self.assertEqual([self.rc_name], placement.deleted_classes)
        deleted_rows = [r for r in db.cleanup_rows
                        if r['action'] == 'class_deleted']
        details = json.loads(deleted_rows[0]['details'])
        self.assertEqual(['host1'], details['hosts_cleared'])
        self.assertEqual([], details['hosts_failed'])

    def test_many_hosts_all_cleared(self):
        placement, db, rec = self._aged_with_hosts(
            ['host1', 'host2', 'host3'])
        rec.reconcile()
        self.assertEqual(3, len(placement.cleared_inventory))
        cleared_names = sorted(h for h, _ in placement.cleared_inventory)
        self.assertEqual(['host1', 'host2', 'host3'], cleared_names)
        self.assertEqual([self.rc_name], placement.deleted_classes)

    def test_one_of_many_hosts_fails_inventory_clear(self):
        placement, db, rec = self._aged_with_hosts(
            ['host1', 'host2', 'host3'])
        placement.delete_inventory_raises['host2'] = (
            placement_exc.InventoryUpdateFailed(
                resource_provider='blazar_host2'))
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        cleared = sorted(h for h, _ in placement.cleared_inventory)
        self.assertEqual(['host1', 'host3'], cleared)
        deleted_rows = [r for r in db.cleanup_rows
                        if r['action'] == 'class_deleted']
        details = json.loads(deleted_rows[0]['details'])
        self.assertEqual(['host1', 'host3'], details['hosts_cleared'])
        self.assertEqual(1, len(details['hosts_failed']))
        self.assertEqual('host2', details['hosts_failed'][0]['host'])

    def test_all_hosts_fail_inventory_clear_still_attempts_class(self):
        placement, db, rec = self._aged_with_hosts(['host1', 'host2'])
        placement.delete_inventory_raises['host1'] = RuntimeError("boom1")
        placement.delete_inventory_raises['host2'] = RuntimeError("boom2")
        placement.delete_class_raises[self.rc_name] = (
            placement_exc.ResourceClassDeletionFailed(
                resource_class=self.rc_name))
        summary = rec.reconcile()
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['errors'])
        self.assertEqual([], placement.cleared_inventory)
        err_rows = [r for r in db.cleanup_rows if r['action'] == 'error']
        self.assertEqual(1, len(err_rows))

    def test_host_name_strip_blazar_prefix(self):
        placement, db, rec = self._aged_with_hosts(['some-host'])
        rec.reconcile()
        self.assertEqual(
            [('some-host', self.rc_name[len(rr.RESERVATION_RC_PREFIX):])],
            placement.cleared_inventory)


class ReconcilerClassDelete409Test(tests.TestCase):
    def setUp(self):
        super(ReconcilerClassDelete409Test, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
        self.rc_name = _rc(self.uuid)

    def test_409_with_allocations_appearing_is_skipped(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)

        placement.delete_class_raises[self.rc_name] = (
            placement_exc.ResourceClassDeletionFailed(
                resource_class=self.rc_name))

        call_count = {'n': 0}

        def _alloc(rc_name):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return []
            return [{'consumer_uuid': 'late-consumer'}]

        placement.get_allocations_for_class = _alloc

        summary = rec.reconcile()

        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_allocations'])
        races = [r for r in db.cleanup_rows
                 if r['action'] == 'skipped_allocations']
        self.assertEqual(1, len(races))
        details = json.loads(races[0]['details'])
        self.assertTrue(details.get('race'))

    def test_409_with_no_allocations_is_error(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)

        placement.delete_class_raises[self.rc_name] = (
            placement_exc.ResourceClassDeletionFailed(
                resource_class=self.rc_name))
        summary = rec.reconcile()
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['errors'])
        self.assertEqual(0, summary['skipped_allocations'])


class ReconcilerListFailureTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerListFailureTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)

    def test_list_classes_failure_is_noop_cycle(self):
        placement = _FakePlacement()
        placement.list_classes_raises = (
            placement_exc.ResourceClassListFailed())
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(1, summary['errors'])
        self.assertEqual(0, summary['detected'])
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(0, len(db.cleanup_rows))

    def test_db_failure_is_noop_cycle(self):
        placement = _FakePlacement()
        placement.classes = {'CUSTOM_RESERVATION_X'}
        db = _FakeDB(live_uuids=[])
        db.live_uuids_raises = RuntimeError("db down")
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(1, summary['errors'])
        self.assertEqual(0, summary['deleted'])

    def test_list_hosts_failure_logs_error_continues(self):
        uuid_a = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
        uuid_b = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
        placement = _FakePlacement()
        placement.classes = {_rc(uuid_a), _rc(uuid_b)}

        original = placement.list_resource_providers_with_inventory

        def _hosts(rc_name):
            if rc_name == _rc(uuid_a):
                raise RuntimeError("placement transient")
            return original(rc_name)

        placement.list_resource_providers_with_inventory = _hosts
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)
        summary = rec.reconcile()
        self.assertEqual(2, summary['detected'])
        self.assertEqual(1, summary['errors'])
        self.assertEqual(1, summary['deleted'])
        self.assertEqual([_rc(uuid_b)], placement.deleted_classes)


class ReconcilerForceUuidsTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerForceUuidsTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.rc_name = _rc(self.uuid)

    def test_force_uuid_bypasses_grace_period(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        summary = rec.reconcile(
            triggered_by='cli', force_uuids=[self.uuid])
        self.assertEqual(1, summary['deleted'])
        self.assertEqual([self.rc_name], placement.deleted_classes)
        deleted_rows = [r for r in db.cleanup_rows
                        if r['action'] == 'class_deleted']
        self.assertEqual('cli', deleted_rows[0]['triggered_by'])

    def test_force_uuid_still_blocked_by_allocations(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        placement.allocations[self.rc_name] = [
            {'consumer_uuid': 'still-running'}]
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile(
            triggered_by='cli', force_uuids=[self.uuid])
        self.assertEqual(0, summary['deleted'])
        self.assertEqual(1, summary['skipped_allocations'])

    def test_force_uuid_not_in_orphans_is_ignored(self):
        placement = _FakePlacement()
        placement.classes = set()
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile(
            triggered_by='cli', force_uuids=[self.uuid])
        self.assertEqual(0, summary['detected'])
        self.assertEqual(0, summary['deleted'])

    def test_force_uuid_filters_to_only_listed(self):
        other = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
        placement = _FakePlacement()
        placement.classes = {self.rc_name, _rc(other)}
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile(
            triggered_by='cli', force_uuids=[self.uuid])
        self.assertEqual([self.rc_name], placement.deleted_classes)
        self.assertEqual(1, summary['detected'])
        self.assertEqual(1, summary['deleted'])


class ReconcilerMultiCycleTest(tests.TestCase):
    def setUp(self):
        super(ReconcilerMultiCycleTest, self).setUp()
        self.cfg = self.useFixture(conf_fixture.Config(CONF))
        self.cfg.config(group='reservation_reconciler',
                        orphan_grace_period_seconds=600)
        self.uuid = '11112222-3333-4444-5555-666677778888'
        self.rc_name = _rc(self.uuid)

    def test_t0_t599_t601_walkthrough(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        clock = _Clock(start=0.0)
        rec = _make_reconciler(placement, db, clock)
        s0 = rec.reconcile()
        clock.advance(599)
        s1 = rec.reconcile()
        clock.advance(2)
        s2 = rec.reconcile()
        self.assertEqual(1, s0['skipped_grace'])
        self.assertEqual(1, s1['skipped_grace'])
        self.assertEqual(1, s2['deleted'])
        self.assertEqual([self.rc_name], placement.deleted_classes)
        actions = [r['action'] for r in db.cleanup_rows]
        self.assertEqual(['detected', 'class_deleted'], actions)

    def test_audit_log_survives_failed_writes(self):
        placement = _FakePlacement()
        placement.classes = {self.rc_name}
        db = _FakeDB(live_uuids=[])
        db.create_raises = RuntimeError("audit table is full")
        clock = _Clock()
        rec = _make_reconciler(placement, db, clock)
        rec.reconcile()
        clock.advance(601)
        summary = rec.reconcile()
        self.assertEqual(1, summary['deleted'])
        self.assertEqual([self.rc_name], placement.deleted_classes)
        self.assertEqual([], db.cleanup_rows)

    def test_summary_shape_is_stable(self):
        placement = _FakePlacement()
        db = _FakeDB(live_uuids=[])
        rec = _make_reconciler(placement, db)
        summary = rec.reconcile()
        self.assertEqual(
            {'detected', 'deleted', 'skipped_allocations',
             'skipped_grace', 'errors'},
            set(summary.keys()))
        for v in summary.values():
            self.assertIsInstance(v, int)


class RCNameHelpersTest(tests.TestCase):
    def test_uuid_round_trip_is_lossy_but_canonical(self):
        u = 'abcdef12-3456-7890-abcd-ef1234567890'
        rc = rr._rc_name_for_uuid(u)
        recovered = rr._uuid_from_rc_name(rc)
        self.assertEqual(
            'CUSTOM_RESERVATION_ABCDEF12_3456_7890_ABCD_EF1234567890',
            rc)
        self.assertEqual(u, recovered)

    def test_uuid_from_non_reservation_class_is_none(self):
        self.assertIsNone(rr._uuid_from_rc_name('VCPU'))
        self.assertIsNone(rr._uuid_from_rc_name(''))
        self.assertIsNone(rr._uuid_from_rc_name(None))

    def test_uuid_from_malformed_tail_is_none(self):
        out = rr._uuid_from_rc_name('CUSTOM_RESERVATION_HELLO')
        self.assertIsNone(out)
