# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Dashboard multi-calendar tests.

Two things matter here operationally:

  1. The dashboard describes every CONFIGURED calendar, including one that the
     last run skipped, so a silently-missing calendar cannot look healthy.
  2. The delete routes act only on an explicitly named, configured target. They
     used to hardcode "St. Edward Public Calendar", so a staff member intending
     to reset a different calendar would have wiped the parish public one.
"""
import importlib
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_INIT', 'true')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config as config_module  # noqa: E402
import app as app_module  # noqa: E402
from app import app as flask_app  # noqa: E402


TWO_PAIRS = 'SAS=Sundays At St. Edward'


def configure(**env):
    for key in ('SOURCE_CALENDAR', 'TARGET_CALENDAR', 'SYNC_CATEGORY', 'EXTRA_SYNC_PAIRS'):
        if key in env:
            os.environ[key] = env[key]
        else:
            os.environ.pop(key, None)
    return importlib.reload(config_module)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    importlib.reload(config_module)


class _FakeAuth:
    def is_authenticated(self):
        return True


class TestBuildPairStatus:
    """/status must describe configured pairs, not just the ones that ran"""

    def test_reports_every_configured_pair(self, monkeypatch):
        configure(EXTRA_SYNC_PAIRS=TWO_PAIRS)
        monkeypatch.setattr(app_module, 'sync_engine', None, raising=False)

        rows = app_module.build_pair_status({
            'pairs': [
                {'category': 'Public', 'target_calendar': 'St. Edward Public Calendar',
                 'success': True, 'added': 3, 'updated': 1, 'deleted': 0},
                {'category': 'SAS', 'target_calendar': 'Sundays At St. Edward',
                 'success': True, 'added': 0, 'updated': 0, 'deleted': 0},
            ]
        })

        assert [r['target_calendar'] for r in rows] == [
            'St. Edward Public Calendar', 'Sundays At St. Edward'
        ]
        assert all(r['in_last_run'] for r in rows)
        assert rows[0]['added'] == 3

    def test_pair_absent_from_last_run_is_flagged(self, monkeypatch):
        """A configured calendar the run skipped must not look healthy."""
        configure(EXTRA_SYNC_PAIRS=TWO_PAIRS)
        monkeypatch.setattr(app_module, 'sync_engine', None, raising=False)

        rows = app_module.build_pair_status({
            'pairs': [
                {'category': 'Public', 'target_calendar': 'St. Edward Public Calendar',
                 'success': True, 'added': 0, 'updated': 0, 'deleted': 0},
            ]
        })

        sas = next(r for r in rows if r['category'] == 'SAS')
        assert sas['in_last_run'] is False
        assert sas['success'] is False

    def test_single_pair_flat_result_is_adopted(self, monkeypatch):
        """A one-pair run has no 'pairs' key; the flat result is that pair."""
        configure()
        monkeypatch.setattr(app_module, 'sync_engine', None, raising=False)

        rows = app_module.build_pair_status({
            'success': True, 'message': 'ok', 'added': 2, 'updated': 0, 'deleted': 0
        })

        assert len(rows) == 1
        assert rows[0]['in_last_run'] is True
        assert rows[0]['added'] == 2

    def test_no_sync_yet_still_lists_calendars(self, monkeypatch):
        configure(EXTRA_SYNC_PAIRS=TWO_PAIRS)
        monkeypatch.setattr(app_module, 'sync_engine', None, raising=False)

        rows = app_module.build_pair_status(None)

        assert len(rows) == 2
        assert not any(r['in_last_run'] for r in rows)

    def test_failure_detail_is_carried_through(self, monkeypatch):
        configure(EXTRA_SYNC_PAIRS=TWO_PAIRS)
        monkeypatch.setattr(app_module, 'sync_engine', None, raising=False)

        rows = app_module.build_pair_status({
            'pairs': [
                {'category': 'Public', 'target_calendar': 'St. Edward Public Calendar',
                 'success': True, 'added': 0, 'updated': 0, 'deleted': 0},
                {'category': 'SAS', 'target_calendar': 'Sundays At St. Edward',
                 'success': False, 'error': "Calendar 'Sundays At St. Edward' not found"},
            ]
        })

        sas = next(r for r in rows if r['category'] == 'SAS')
        assert sas['success'] is False
        assert 'not found' in sas['error']


class TestClearTargetGuard:
    """Destructive routes act only on an explicitly named, configured calendar"""

    @pytest.fixture
    def client(self, monkeypatch):
        configure(EXTRA_SYNC_PAIRS=TWO_PAIRS)
        flask_app.config['TESTING'] = True

        monkeypatch.setattr(app_module, 'auth_manager', _FakeAuth(), raising=False)
        monkeypatch.setattr(app_module, 'ensure_components_initialized', lambda: None, raising=False)

        deleted = []

        class _Reader:
            def find_calendar_id(self, name):
                return f'id-for-{name}'

            def get_calendar_events(self, calendar_id, **kwargs):
                return [{'id': 'evt-1'}, {'id': 'evt-2'}]

        class _Writer:
            def delete_event(self, calendar_id, event_id, **kwargs):
                deleted.append((calendar_id, event_id))
                return True

            def clear_synced_events_only(self, calendar_id):
                deleted.append((calendar_id, 'synced-sweep'))
                return 7

        class _Engine:
            reader = _Reader()
            writer = _Writer()
            history = None

        monkeypatch.setattr(app_module, 'sync_engine', _Engine(), raising=False)

        with flask_app.test_client() as client:
            client.deleted = deleted
            yield client

    def test_missing_target_is_rejected(self, client):
        """A stale tab must fail loudly, never fall back to a default calendar."""
        response = client.post('/clear-target', json={})
        assert response.status_code == 400
        assert not client.deleted

    def test_unknown_calendar_is_rejected(self, client):
        response = client.post('/clear-target', json={'target': 'Fr. Bulso Personal'})
        assert response.status_code == 400
        assert not client.deleted

    def test_source_calendar_can_never_be_cleared(self, client):
        """The master calendar is not a sync target, so it is unreachable here."""
        response = client.post('/clear-target', json={'target': 'Calendar'})
        assert response.status_code == 400
        assert not client.deleted

    def test_named_target_is_cleared(self, client):
        response = client.post('/clear-target', json={'target': 'Sundays At St. Edward'})
        assert response.status_code == 200
        body = response.get_json()
        assert body['success'] is True
        assert body['target'] == 'Sundays At St. Edward'
        assert all(cal == 'id-for-Sundays At St. Edward' for cal, _ in client.deleted)

    def test_target_name_is_matched_case_insensitively(self, client):
        response = client.post('/clear-target', json={'target': 'sundays at st. edward'})
        assert response.status_code == 200
        # Echoes the configured spelling, not what the caller typed
        assert response.get_json()['target'] == 'Sundays At St. Edward'

    def test_clear_synced_only_honours_the_named_target(self, client):
        response = client.post('/clear-synced-only', json={'target': 'Sundays At St. Edward'})
        assert response.status_code == 200
        body = response.get_json()
        assert body['deleted'] == 7
        assert body['target'] == 'Sundays At St. Edward'
        assert client.deleted == [('id-for-Sundays At St. Edward', 'synced-sweep')]

    def test_clear_synced_only_rejects_a_missing_target(self, client):
        response = client.post('/clear-synced-only', json={})
        assert response.status_code == 400
        assert not client.deleted
