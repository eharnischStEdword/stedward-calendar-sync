# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for multi-category sync pairs.

The service syncs one source calendar into several target calendars, one per
Outlook category ("Public" -> public calendar, "SAS" -> Sundays At St. Edward).
These tests pin the two behaviours that matter operationally:

  1. With no EXTRA_SYNC_PAIRS set, the service behaves exactly as it did when
     it only knew about a single hardcoded pair.
  2. A typo in EXTRA_SYNC_PAIRS can never aim a write at the master calendar.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config as config_module  # noqa: E402
from calendar_ops import CalendarReader  # noqa: E402


def reload_config(**env):
    """Reload config with a specific environment, then restore the old one."""
    saved = {}
    for key in ('SOURCE_CALENDAR', 'TARGET_CALENDAR', 'SYNC_CATEGORY', 'EXTRA_SYNC_PAIRS'):
        saved[key] = os.environ.get(key)
        if key in env:
            os.environ[key] = env[key]
        else:
            os.environ.pop(key, None)
    try:
        return importlib.reload(config_module)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def restore_config():
    """Leave the config module as we found it for other test modules."""
    yield
    importlib.reload(config_module)


class TestSyncPairs:
    """config.get_sync_pairs() resolution and safety guards"""

    def test_default_is_single_public_pair(self):
        cfg = reload_config()
        assert cfg.get_sync_pairs() == [
            {'category': 'Public', 'target': 'St. Edward Public Calendar'}
        ]

    def test_extra_pair_is_appended_after_primary(self):
        cfg = reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        pairs = cfg.get_sync_pairs()
        assert pairs[0]['category'] == 'Public'
        assert pairs[1] == {'category': 'SAS', 'target': 'Sundays At St. Edward'}

    def test_target_matching_source_calendar_is_dropped(self):
        """A typo must never let the sync write to the master calendar."""
        cfg = reload_config(SOURCE_CALENDAR='Calendar', EXTRA_SYNC_PAIRS='SAS=Calendar')
        targets = [p['target'] for p in cfg.get_sync_pairs()]
        assert 'Calendar' not in targets

    def test_source_match_is_case_insensitive(self):
        cfg = reload_config(SOURCE_CALENDAR='Calendar', EXTRA_SYNC_PAIRS='SAS=calendar')
        assert len(cfg.get_sync_pairs()) == 1

    def test_duplicate_pairs_are_collapsed(self):
        cfg = reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward;SAS=Sundays At St. Edward')
        assert len(cfg.get_sync_pairs()) == 2

    def test_malformed_entries_are_ignored(self):
        cfg = reload_config(EXTRA_SYNC_PAIRS='garbage;=;X=;=Y;   ')
        assert cfg.get_sync_pairs() == [
            {'category': 'Public', 'target': 'St. Edward Public Calendar'}
        ]

    def test_multiple_valid_extra_pairs(self):
        cfg = reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward;Youth=Youth Calendar')
        assert [p['category'] for p in cfg.get_sync_pairs()] == ['Public', 'SAS', 'Youth']

    def test_whitespace_around_pairs_is_trimmed(self):
        cfg = reload_config(EXTRA_SYNC_PAIRS='  SAS = Sundays At St. Edward  ')
        assert cfg.get_sync_pairs()[1] == {'category': 'SAS', 'target': 'Sundays At St. Edward'}


class TestCategoryFiltering:
    """CalendarReader.get_public_events() honours the requested category"""

    @staticmethod
    def make_reader(events):
        reader = CalendarReader(auth_manager=None)
        reader.get_calendar_events = lambda *args, **kwargs: events
        return reader

    @staticmethod
    def event(subject, categories):
        return {
            'id': subject,
            'subject': subject,
            'categories': categories,
            'showAs': 'busy',
            'type': 'singleInstance',
            'isCancelled': False,
            'start': {'dateTime': '2026-08-09T10:30:00.0000000', 'timeZone': 'UTC'},
            'end': {'dateTime': '2026-08-09T11:30:00.0000000', 'timeZone': 'UTC'}
        }

    def test_defaults_to_public(self):
        reader = self.make_reader([
            self.event('Mass', ['Public']),
            self.event('Staff meeting', ['Private'])
        ])
        subjects = [e['subject'] for e in reader.get_public_events('cal-id')]
        assert subjects == ['Mass']

    def test_selects_only_the_requested_category(self):
        reader = self.make_reader([
            self.event('Mass', ['Public']),
            self.event('Kickoff', ['SAS']),
            self.event('Staff meeting', ['Private'])
        ])
        subjects = [e['subject'] for e in reader.get_public_events('cal-id', category='SAS')]
        assert subjects == ['Kickoff']

    def test_category_match_is_case_insensitive(self):
        reader = self.make_reader([self.event('Kickoff', ['sas'])])
        assert len(reader.get_public_events('cal-id', category='SAS')) == 1

    def test_event_in_two_categories_syncs_to_both(self):
        """An event tagged Public and SAS belongs on both calendars."""
        events = [self.event('Campaign launch', ['Public', 'SAS'])]
        reader = self.make_reader(events)
        assert len(reader.get_public_events('cal-id', category='Public')) == 1
        assert len(reader.get_public_events('cal-id', category='SAS')) == 1

    def test_untagged_events_are_excluded(self):
        reader = self.make_reader([self.event('Random', [])])
        assert reader.get_public_events('cal-id', category='SAS') == []

    def test_non_busy_events_are_excluded(self):
        free_event = self.event('Tentative hold', ['SAS'])
        free_event['showAs'] = 'free'
        reader = self.make_reader([free_event])
        assert reader.get_public_events('cal-id', category='SAS') == []

    def test_cancelled_events_are_excluded(self):
        cancelled = self.event('Cancelled talk', ['SAS'])
        cancelled['isCancelled'] = True
        reader = self.make_reader([cancelled])
        assert reader.get_public_events('cal-id', category='SAS') == []


class TestTwoPairSync:
    """A full _do_sync() cycle routes each category to its own target calendar"""

    CAL_IDS = {
        'Calendar': 'id-source',
        'St. Edward Public Calendar': 'id-public',
        'Sundays At St. Edward': 'id-sas',
    }

    @staticmethod
    def source_events():
        def event(event_id, subject, categories, day):
            return {
                'id': event_id, 'subject': subject, 'categories': categories,
                'showAs': 'busy', 'type': 'singleInstance', 'isCancelled': False,
                'start': {'dateTime': f'2026-08-{day}T10:30:00.0000000', 'timeZone': 'UTC'},
                'end': {'dateTime': f'2026-08-{day}T11:30:00.0000000', 'timeZone': 'UTC'}
            }

        return [
            event('e1', 'Daily Mass', ['Public'], '09'),
            event('e2', 'SAS Kickoff', ['SAS'], '16'),
            event('e3', 'Campaign Launch', ['Public', 'SAS'], '23'),
            event('e4', 'Staff Meeting', [], '24'),
        ]

    def build_engine(self, monkeypatch):
        """SyncEngine wired to in-memory calendars; records every write."""
        import sync as sync_module

        engine = sync_module.SyncEngine(auth_manager=None)
        writes = []
        cache_updates = []

        engine.reader.find_calendar_id = lambda name: self.CAL_IDS.get(name)

        served = set()

        def get_calendar_events(calendar_id, select_fields=None, start=None, end=None):
            # Serve the source events once; both target calendars start empty.
            if calendar_id == 'id-source' and (calendar_id, str(start)) not in served:
                served.add((calendar_id, str(start)))
                return self.source_events()
            return []

        engine.reader.get_calendar_events = get_calendar_events
        engine.change_tracker.update_cache = lambda events: cache_updates.append(len(events))

        def execute(target_id, to_add, to_update, to_delete):
            writes.append({'target': target_id, 'add': len(to_add), 'delete': len(to_delete)})
            return {'success': True, 'message': 'ok', 'added': len(to_add),
                    'updated': len(to_update), 'deleted': len(to_delete),
                    'successful_operations': len(to_add), 'failed_operations': 0}

        engine._execute_sync_operations_batch = execute
        engine._handle_cancelled_occurrences = lambda source, target: 0
        engine._handle_modified_occurrences = lambda source, target: 0

        return engine, writes, cache_updates

    def test_each_category_writes_only_to_its_own_calendar(self, monkeypatch):
        reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, writes, _ = self.build_engine(monkeypatch)

        result = engine._do_sync()

        assert [w['target'] for w in writes] == ['id-public', 'id-sas']
        # Public: Daily Mass + Campaign Launch. SAS: SAS Kickoff + Campaign Launch.
        assert writes[0]['add'] == 2
        assert writes[1]['add'] == 2
        # The untagged staff meeting reaches neither calendar
        assert result['added'] == 4

    def test_dual_tagged_event_reaches_both_calendars(self, monkeypatch):
        reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, _, _ = self.build_engine(monkeypatch)

        result = engine._do_sync()

        by_category = {p['category']: p for p in result['pairs']}
        assert by_category['Public']['target_calendar'] == 'St. Edward Public Calendar'
        assert by_category['SAS']['target_calendar'] == 'Sundays At St. Edward'
        assert by_category['Public']['added'] == 2
        assert by_category['SAS']['added'] == 2

    def test_nothing_is_deleted_when_targets_start_empty(self, monkeypatch):
        reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, writes, _ = self.build_engine(monkeypatch)

        engine._do_sync()

        assert all(w['delete'] == 0 for w in writes)

    def test_only_the_primary_pair_owns_the_event_cache(self, monkeypatch):
        """update_cache() replaces the cache wholesale, so a second writer would wipe it."""
        reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, _, cache_updates = self.build_engine(monkeypatch)

        engine._do_sync()

        assert len(cache_updates) == 1

    def test_single_pair_result_keeps_its_original_shape(self, monkeypatch):
        """With no extra pairs the dashboard must see the result it always saw."""
        reload_config()
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, writes, _ = self.build_engine(monkeypatch)

        result = engine._do_sync()

        assert 'pairs' not in result
        assert result['message'] == 'ok'
        assert [w['target'] for w in writes] == ['id-public']

    def test_one_failing_pair_does_not_stop_the_other(self, monkeypatch):
        reload_config(EXTRA_SYNC_PAIRS='SAS=Sundays At St. Edward')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, writes, _ = self.build_engine(monkeypatch)

        original = engine._execute_sync_operations_batch

        def explode_on_public(target_id, to_add, to_update, to_delete):
            if target_id == 'id-public':
                raise RuntimeError("Graph is having a day")
            return original(target_id, to_add, to_update, to_delete)

        engine._execute_sync_operations_batch = explode_on_public

        result = engine._do_sync()

        by_category = {p['category']: p for p in result['pairs']}
        assert by_category['Public']['success'] is False
        assert by_category['SAS']['success'] is True
        assert by_category['SAS']['added'] == 2
        assert result['success'] is False

    def test_missing_target_calendar_is_reported_not_raised(self, monkeypatch):
        reload_config(EXTRA_SYNC_PAIRS='SAS=Calendar That Does Not Exist')
        monkeypatch.setattr('config.DRY_RUN_MODE', False, raising=False)
        engine, writes, _ = self.build_engine(monkeypatch)

        result = engine._do_sync()

        by_category = {p['category']: p for p in result['pairs']}
        assert by_category['Public']['success'] is True
        assert by_category['SAS']['success'] is False
        assert [w['target'] for w in writes] == ['id-public']
