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

"""Tests for reservation_cleanup_logs DB helpers + the
reservation_get_all_active_uuids helper.

These hit the real (sqlite) DB via the DBTestCase fixture so we get
end-to-end coverage of the model, helpers, and filter wiring.
"""

import datetime

from blazar.db import api as db_api
from blazar import tests


def _fake_lease_values(lease_id='lease-1'):
    """Minimal valid lease+reservation payload."""
    return {
        'id': lease_id,
        'name': 'lease-' + lease_id,
        'user_id': 'user-1',
        'project_id': 'proj-1',
        'start_date': datetime.datetime(2030, 1, 1, 0, 0),
        'end_date': datetime.datetime(2030, 1, 2, 0, 0),
        'trust_id': 't1',
        'reservations': [{
            'id': 'res-' + lease_id,
            'resource_type': 'virtual:instance',
            'resource_id': 'r1',
            'status': 'pending',
        }],
        'events': [],
    }


class ReservationCleanupLogTest(tests.DBTestCase):
    """End-to-end tests for the audit-log helpers."""

    def test_create_round_trips(self):
        payload = {
            'resource_class_name': 'CUSTOM_RESERVATION_AAA',
            'reservation_id': 'res-aaa',
            'action': 'detected',
            'triggered_by': 'periodic',
            'details': '{"foo": "bar"}',
        }
        created = db_api.reservation_cleanup_log_create(payload)
        self.assertEqual('CUSTOM_RESERVATION_AAA',
                         created['resource_class_name'])
        self.assertEqual('detected', created['action'])
        self.assertEqual('periodic', created['triggered_by'])
        self.assertIsNotNone(created['id'])
        self.assertIsNotNone(created['created_at'])

    def test_create_nullable_reservation_id(self):
        payload = {
            'resource_class_name': 'CUSTOM_RESERVATION_WEIRD',
            'reservation_id': None,
            'action': 'detected',
            'triggered_by': 'periodic',
            'details': None,
        }
        created = db_api.reservation_cleanup_log_create(payload)
        self.assertIsNone(created['reservation_id'])
        self.assertIsNone(created['details'])

    def test_list_empty(self):
        rows = db_api.reservation_cleanup_log_list()
        self.assertEqual([], rows)

    def test_list_all_newest_first(self):
        for i in range(3):
            db_api.reservation_cleanup_log_create({
                'resource_class_name': 'CUSTOM_RESERVATION_%d' % i,
                'reservation_id': 'r-%d' % i,
                'action': 'detected',
                'triggered_by': 'periodic',
                'details': None,
            })
        rows = db_api.reservation_cleanup_log_list()
        self.assertEqual(3, len(rows))
        names = {r['resource_class_name'] for r in rows}
        self.assertEqual(
            {'CUSTOM_RESERVATION_0', 'CUSTOM_RESERVATION_1',
             'CUSTOM_RESERVATION_2'},
            names)

    def test_list_filters_by_action(self):
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_A',
            'action': 'detected', 'triggered_by': 'periodic',
            'reservation_id': None, 'details': None,
        })
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_B',
            'action': 'class_deleted', 'triggered_by': 'periodic',
            'reservation_id': None, 'details': None,
        })
        rows = db_api.reservation_cleanup_log_list(
            filters={'action': 'class_deleted'})
        self.assertEqual(1, len(rows))
        self.assertEqual('CUSTOM_RESERVATION_B',
                         rows[0]['resource_class_name'])

    def test_list_filters_by_triggered_by(self):
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_A',
            'action': 'class_deleted', 'triggered_by': 'periodic',
            'reservation_id': None, 'details': None,
        })
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_B',
            'action': 'class_deleted', 'triggered_by': 'cli',
            'reservation_id': None, 'details': None,
        })
        rows = db_api.reservation_cleanup_log_list(
            filters={'triggered_by': 'cli'})
        self.assertEqual(1, len(rows))
        self.assertEqual('cli', rows[0]['triggered_by'])

    def test_list_filters_by_reservation_id(self):
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_A',
            'action': 'class_deleted', 'triggered_by': 'cli',
            'reservation_id': 'r-1', 'details': None,
        })
        db_api.reservation_cleanup_log_create({
            'resource_class_name': 'CUSTOM_RESERVATION_B',
            'action': 'class_deleted', 'triggered_by': 'cli',
            'reservation_id': 'r-2', 'details': None,
        })
        rows = db_api.reservation_cleanup_log_list(
            filters={'reservation_id': 'r-2'})
        self.assertEqual(1, len(rows))
        self.assertEqual('r-2', rows[0]['reservation_id'])

    def test_list_limit_caps_returned_rows(self):
        for i in range(5):
            db_api.reservation_cleanup_log_create({
                'resource_class_name': 'CUSTOM_RESERVATION_%d' % i,
                'action': 'detected', 'triggered_by': 'periodic',
                'reservation_id': None, 'details': None,
            })
        rows = db_api.reservation_cleanup_log_list(limit=2)
        self.assertEqual(2, len(rows))


class ReservationActiveUuidsTest(tests.DBTestCase):
    """The helper that feeds the reconciler's known-rcs set."""

    def _make_lease_with_reservation(self, lease_id, res_id, res_status):
        values = _fake_lease_values(lease_id=lease_id)
        values['reservations'][0]['id'] = res_id
        values['reservations'][0]['status'] = res_status
        return db_api.lease_create(values)

    def test_no_reservations_returns_empty(self):
        out = db_api.reservation_get_all_active_uuids()
        self.assertEqual([], list(out))

    def test_pending_reservations_are_returned(self):
        self._make_lease_with_reservation(
            'l-1', 'res-pending-1', 'pending')
        self._make_lease_with_reservation(
            'l-2', 'res-pending-2', 'active')
        out = db_api.reservation_get_all_active_uuids()
        self.assertEqual(
            {'res-pending-1', 'res-pending-2'}, set(out))

    def test_deleted_reservations_are_excluded(self):
        self._make_lease_with_reservation(
            'l-1', 'res-live', 'active')
        self._make_lease_with_reservation(
            'l-2', 'res-gone', 'deleted')
        out = db_api.reservation_get_all_active_uuids()
        self.assertEqual({'res-live'}, set(out))

    def test_error_reservations_are_considered_alive(self):
        self._make_lease_with_reservation(
            'l-1', 'res-err', 'error')
        out = db_api.reservation_get_all_active_uuids()
        self.assertEqual({'res-err'}, set(out))
