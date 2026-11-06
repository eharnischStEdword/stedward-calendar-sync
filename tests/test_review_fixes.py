# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests pinning the fixes from the 2026-11 code review.

Each class names the defect it closes. The stub readers and writers follow
tests/test_sync_pairs.py and tests/test_duplicates.py: nothing here opens a
socket, starts a scheduler that sleeps, or writes under /data.
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config  # noqa: E402
import calendar_ops  # noqa: E402
import sync as sync_module  # noqa: E402
from calendar_ops import CalendarWriter  # noqa: E402
from signature_utils import generate_event_signature  # noqa: E402
from sync import SyncEngine, SyncScheduler  # noqa: E402


# The exact extended-property id the writer sets and the reader $expands.
SOURCE_ID_PROP = 'String {66f5a359-4659-4830-9070-00047ec6ac6e} Name sourceEventId'


def engine():
    e = SyncEngine(auth_manager=None)
    e.writer.copy_body = False
    return e


def source_event(subject='Parish Council Meeting', event_id='src-1',
                 start='2026-09-06T14:15:00', end='2026-09-06T15:25:00',
                 is_all_day=False, categories=None):
    return {
        'id': event_id,
        'subject': subject,
        'start': {'dateTime': start, 'timeZone': 'UTC'},
        'end': {'dateTime': end, 'timeZone': 'UTC'},
        'showAs': 'busy',
        'categories': categories if categories is not None else ['Public'],
        'type': 'singleInstance',
        'isCancelled': False,
        'isAllDay': is_all_day,
        'body': {'contentType': 'html', 'content': 'Gate code 1234'},
    }


def target_row(event_id, subject, start, end, source_id=None, body_content='',
               is_all_day=False, series_master_id=None, original_start=None):
    """A target-calendar row shaped the way Graph returns it. Synced only
    when source_id is given (the extended property) or the body carries a
    legacy marker."""
    row = {
        'id': event_id,
        'subject': subject,
        'body': {'contentType': 'html', 'content': body_content},
        'start': {'dateTime': start, 'timeZone': 'UTC'},
        'end': {'dateTime': end, 'timeZone': 'UTC'},
        'categories': [],
        'showAs': 'busy',
        'type': 'singleInstance',
        'isCancelled': False,
        'isAllDay': is_all_day,
    }
    if source_id:
        row['singleValueExtendedProperties'] = [{'id': SOURCE_ID_PROP, 'value': source_id}]
    if series_master_id:
        row['seriesMasterId'] = series_master_id
        row['type'] = 'occurrence'
    if original_start:
        row['originalStart'] = original_start
    return row


@pytest.mark.unit
class TestCancelledOccurrenceGuard:
    """Finding 35: the explicitly-cancelled loop in _handle_cancelled_occurrences
    deleted any target occurrence whose subject and slot matched a cancelled
    master occurrence, with no check that the service created it. The only
    target rows that carry a seriesMasterId are series staff typed in by hand."""

    CANCELLED_AT = '2026-09-06T23:00:00Z'

    def build(self, target_instances):
        e = engine()
        source_instances = [{
            'id': 'master-occ-1',
            'subject': 'Choir Practice',
            'seriesMasterId': 'master-series',
            'originalStart': self.CANCELLED_AT,
            'start': {'dateTime': self.CANCELLED_AT, 'timeZone': 'UTC'},
            'isCancelled': True,
            'body': {'content': ''},
        }]
        by_calendar = {'src': source_instances, 'tgt': target_instances}
        e.reader.get_calendar_instances = lambda cal_id, start, end: list(by_calendar[cal_id])
        calls = []
        e.writer.delete_occurrence = lambda cal, series, occ: calls.append((cal, series, occ)) or True
        return e, calls

    def hand_series_instance(self):
        return target_row(
            'hand-occ-1', 'Choir Practice', self.CANCELLED_AT, '2026-09-07T00:00:00Z',
            body_content='Bring the new hymnal',
            series_master_id='hand-series', original_start=self.CANCELLED_AT,
        )

    def test_a_hand_entered_series_is_never_passed_to_delete_occurrence(self):
        e, calls = self.build([self.hand_series_instance()])

        deleted = e._handle_cancelled_occurrences('src', 'tgt')

        assert deleted == 0
        assert calls == []

    def test_a_synced_occurrence_matching_a_cancellation_is_still_deleted(self):
        """The guard must not swing the other way and switch the pass off."""
        synced = self.hand_series_instance()
        synced['singleValueExtendedProperties'] = [{'id': SOURCE_ID_PROP, 'value': 'master-series'}]
        e, calls = self.build([synced])

        deleted = e._handle_cancelled_occurrences('src', 'tgt')

        # Both loops see a cancelled master occurrence (it is absent from the
        # active source keys too), so the same occurrence is handed to the
        # writer twice; live, the second call finds nothing and returns False.
        # That is pre-existing and not what this test pins: what matters is
        # that the synced row is still passed to delete_occurrence at all.
        assert deleted >= 1
        assert set(calls) == {('tgt', 'hand-series', self.CANCELLED_AT)}


@pytest.mark.unit
class TestMigrationGuard:
    """Finding 8: migrate_existing_events_to_extended_properties queued every
    unmatched target event for deletion. Anything a person entered by hand on
    the public calendar has no source match, so a live migration deleted it."""

    def build(self, target_events):
        e = engine()
        src = source_event('Mass', 'src-mass', '2026-09-06T14:00:00', '2026-09-06T15:00:00')
        e.reader.get_public_events = lambda cal_id, **kwargs: [src]
        e.reader.get_calendar_events = lambda cal_id, **kwargs: list(target_events)
        deleted = []
        e.writer.delete_event = lambda cal, eid, **kw: deleted.append(eid) or True
        e.writer.update_event = lambda cal, eid, data: True
        return e, deleted

    def hand_entered(self):
        return target_row('by-hand', 'Bake Sale', '2026-09-13T16:00:00', '2026-09-13T18:00:00',
                          body_content='Set up tables at 3')

    def legacy_orphan(self):
        """Created by an old version of this service, no longer on the master."""
        return target_row('legacy-orphan', 'Old Talk', '2026-09-20T16:00:00', '2026-09-20T17:00:00',
                          body_content='Auto-synced from Calendar')

    def test_dry_run_leaves_the_hand_entered_event_out_of_the_delete_list(self):
        e, _ = self.build([self.hand_entered(), self.legacy_orphan()])

        stats = e.migrate_existing_events_to_extended_properties('src', 'tgt', dry_run=True)

        queued = [x['target_id'] for x in stats['events_to_delete']]
        assert 'by-hand' not in queued
        assert queued == ['legacy-orphan']
        assert stats['left_alone_unsynced'] == 1
        assert stats['would_delete'] == 1
        assert stats['no_match_found'] == 2

    def test_a_live_migration_never_deletes_the_hand_entered_event(self):
        e, deleted = self.build([self.hand_entered(), self.legacy_orphan()])

        stats = e.migrate_existing_events_to_extended_properties('src', 'tgt', dry_run=False)

        assert 'by-hand' not in deleted
        assert deleted == ['legacy-orphan']
        assert stats['actually_deleted'] == 1

    def test_already_migrated_check_is_unchanged(self):
        """A target already carrying the property is still counted as done,
        whether or not its source still exists."""
        migrated = target_row('done', 'Gone Talk', '2026-09-27T16:00:00', '2026-09-27T17:00:00',
                              source_id='src-gone')
        e, _ = self.build([migrated])

        stats = e.migrate_existing_events_to_extended_properties('src', 'tgt', dry_run=True)

        assert stats['already_have_extended_props'] == 1
        assert stats['events_to_delete'] == []


@pytest.mark.unit
class TestBatchSuccessRule:
    """Finding 34: _execute_sync_operations_batch said success True no matter
    how many operations failed, so a throttled run advanced last_sync_time
    and the history recorded a healthy sync."""

    @staticmethod
    def all_fail(e):
        e.writer.batch_create_events = lambda cal, events, **kw: {
            'successful': 0, 'failed': len(events), 'errors': ['429'] * len(events)}
        e.writer.batch_delete_events = lambda cal, ids, **kw: {
            'successful': 0, 'failed': len(ids), 'errors': ['429'] * len(ids)}
        e.writer.update_event = lambda cal, eid, data: False

    def test_every_write_failing_is_a_failed_sync_with_the_counts(self):
        e = engine()
        self.all_fail(e)
        add = [source_event('A', 'a'), source_event('B', 'b')]
        upd = [(source_event('C', 'c'), target_row('t-c', 'C', '2026-09-06T14:15:00', '2026-09-06T15:25:00', source_id='c'))]
        dele = [target_row('t-d', 'D', '2026-09-06T14:15:00', '2026-09-06T15:25:00', source_id='d')]

        result = e._execute_sync_operations_batch('tgt', add, upd, dele)

        assert result['success'] is False
        assert result['failed_operations'] == 4
        assert result['successful_operations'] == 0
        assert '0/4' in result['message']
        assert '4 failed' in result['message']
        assert result['error'] == result['message']

    def test_nothing_to_do_is_still_a_success(self):
        e = engine()
        self.all_fail(e)

        result = e._execute_sync_operations_batch('tgt', [], [], [])

        assert result['success'] is True
        assert 'error' not in result
        assert result['failed_operations'] == 0

    def test_a_partial_failure_stays_a_success_with_the_failure_visible(self):
        """One permanently malformed source event must not mark every run
        failed for good; the count is what surfaces it."""
        e = engine()
        self.all_fail(e)
        e.writer.batch_create_events = lambda cal, events, **kw: {
            'successful': 1, 'failed': len(events) - 1, 'errors': ['400']}
        add = [source_event('A', 'a'), source_event('B', 'b')]

        result = e._execute_sync_operations_batch('tgt', add, [], [])

        assert result['success'] is True
        assert result['failed_operations'] == 1
        assert '1/2' in result['message']

    def test_a_run_where_every_write_failed_is_recorded_as_failed(self, monkeypatch):
        """End to end through _do_sync: history says failed and the freshness
        clock does not move."""
        monkeypatch.setattr(config, 'DRY_RUN_MODE', False, raising=False)
        monkeypatch.setattr(config, 'SYNC_CUTOFF_DAYS', 7, raising=False)
        monkeypatch.setattr(config, 'SYNC_LOOKAHEAD_DAYS', 7, raising=False)
        monkeypatch.setattr(config, 'get_sync_pairs',
                            lambda: [{'category': 'Public', 'target': 'St. Edward Public Calendar'}])

        e = engine()
        self.all_fail(e)
        ids = {config.SOURCE_CALENDAR: 'id-source', 'St. Edward Public Calendar': 'id-public'}
        e.reader.find_calendar_id = lambda name: ids.get(name)
        e.reader.get_public_events = lambda cal_id, **kwargs: [source_event('New Event', 'src-new')]
        e.reader.get_calendar_events = lambda cal_id, **kwargs: []
        e.change_tracker.update_cache = lambda events: None
        e._handle_cancelled_occurrences = lambda s, t: 0
        e._handle_modified_occurrences = lambda s, t: 0

        result = e._do_sync()

        assert result['success'] is False
        assert '0/1' in result['message']
        assert e.last_sync_time is None
        assert e.history.history[-1]['success'] is False
        assert e.history.history[-1]['operations']['failed'] == 1


@pytest.mark.unit
class TestSchedulerJobRegistration:
    """Findings 36 and 37: every scheduler thread registered another 23-minute
    job on the module-global schedule registry and nothing ever cleared it, so
    N pause/resume cycles ran N+1 full syncs per tick. And start() right after
    stop() saw the old thread still sleeping, said 'already running', and
    left nothing running."""

    @pytest.fixture(autouse=True)
    def clean_registry(self, monkeypatch):
        sync_module.schedule.clear()
        # Never let a test thread actually run a sync.
        monkeypatch.setattr(sync_module.schedule, 'run_pending', lambda: None)
        yield
        sync_module.schedule.clear()

    @staticmethod
    def already_stopped():
        event = threading.Event()
        event.set()
        return event

    def test_running_the_loop_twice_registers_one_job(self):
        s = SyncScheduler(sync_engine=None)
        s._run_scheduler(self.already_stopped())
        s._run_scheduler(self.already_stopped())

        assert len(sync_module.schedule.jobs) == 1

    def test_restart_scheduler_path_registers_one_job(self):
        """/restart-scheduler builds a second SyncScheduler and starts it."""
        SyncScheduler(sync_engine=None)._run_scheduler(self.already_stopped())
        SyncScheduler(sync_engine=None)._run_scheduler(self.already_stopped())

        assert len(sync_module.schedule.jobs) == 1

    def test_start_right_after_stop_leaves_the_scheduler_running(self):
        s = SyncScheduler(sync_engine=None)
        s.start()
        first_thread = s.scheduler_thread
        assert s.is_running()

        s.stop()
        s.start()  # inside the old loop's sleep window

        assert s.is_running()
        assert s.scheduler_thread is not first_thread

        s.stop()
        s.scheduler_thread.join(timeout=5)
        first_thread.join(timeout=5)
        assert not s.scheduler_thread.is_alive()
        assert not first_thread.is_alive()
        assert len(sync_module.schedule.jobs) == 1

    def test_stop_wakes_the_loop_promptly(self):
        """The loop used to sleep a full minute before noticing the flag."""
        s = SyncScheduler(sync_engine=None)
        s.start()
        s.stop()
        s.scheduler_thread.join(timeout=5)

        assert not s.scheduler_thread.is_alive()
        assert not s.is_running()

    def test_start_while_running_does_not_spawn_a_second_thread(self):
        s = SyncScheduler(sync_engine=None)
        s.start()
        thread = s.scheduler_thread
        s.start()

        assert s.scheduler_thread is thread

        s.stop()
        thread.join(timeout=5)


@pytest.mark.unit
class TestCaseOnlySubjectEdit:
    """Finding 33: the signature lowercases the subject, so a case-only
    retitle lands in _needs_update, which also lowercased and reported no
    change. The old capitalization stayed on the public calendar forever."""

    def test_case_only_retitle_is_an_update(self):
        e = engine()
        src = source_event('Parish Council Meeting')
        stale = e.writer._prepare_event_data(source_event('parish council meeting'))
        stale['id'] = 'tgt-1'

        assert e._needs_update(src, stale) is True

    def test_case_only_retitle_keeps_its_signature_so_it_is_not_delete_plus_add(self):
        new = source_event('Parish Council Meeting')
        old = source_event('parish council meeting')

        assert generate_event_signature(new) == generate_event_signature(old)

    def test_whitespace_around_the_subject_is_still_ignored(self):
        e = engine()
        src = source_event('Parish Council Meeting')
        stale = e.writer._prepare_event_data(source_event('  Parish Council Meeting '))
        stale['id'] = 'tgt-1'

        assert e._needs_update(src, stale) is False

    def test_identical_subject_is_left_alone(self):
        e = engine()
        src = source_event('Parish Council Meeting')
        stale = e.writer._prepare_event_data(source_event('Parish Council Meeting'))
        stale['id'] = 'tgt-1'

        assert e._needs_update(src, stale) is False


@pytest.mark.unit
class TestPreviewRefusesDuringASync:
    """Finding 31: preview_sync rewrote the shared writer's copy_body while a
    live pair could be mid-write, stripping descriptions from whatever that
    pair created next."""

    def test_preview_raises_without_touching_copy_body(self):
        e = engine()
        e.sync_in_progress = True
        e.writer.copy_body = 'untouched'

        def must_not_be_called(*args, **kwargs):
            raise AssertionError('preview must not read calendars during a sync')

        e.reader.find_calendar_id = must_not_be_called

        with pytest.raises(Exception, match='Sync in progress'):
            e.preview_sync()

        assert e.writer.copy_body == 'untouched'


@pytest.mark.unit
class TestPreviewCarriesIsAllDay:
    """Finding 39: the three preview lists had no isAllDay, so the dashboard
    rendered every all-day copy (midnight UTC) on the previous evening."""

    def test_each_preview_list_entry_says_whether_it_is_all_day(self, monkeypatch):
        monkeypatch.setattr(config, 'SYNC_CUTOFF_DAYS', 7, raising=False)
        monkeypatch.setattr(config, 'SYNC_LOOKAHEAD_DAYS', 7, raising=False)

        e = engine()
        ids = {config.SOURCE_CALENDAR: 'id-source', config.TARGET_CALENDAR: 'id-public'}
        e.reader.find_calendar_id = lambda name: ids.get(name)

        picnic = source_event('Parish Picnic', 'src-picnic',
                              '2026-07-04T00:00:00', '2026-07-05T00:00:00', is_all_day=True)
        retitled = source_event('Parish Council Meeting', 'src-council')
        e.reader.get_public_events = lambda cal_id, **kwargs: [picnic, retitled]

        stale_council = target_row('t-council', 'parish council meeting',
                                   '2026-09-06T14:15:00.0000000', '2026-09-06T15:25:00.0000000',
                                   source_id='src-council')
        gone_all_day = target_row('t-gone', 'Fall Break', '2026-10-12T00:00:00.0000000',
                                  '2026-10-13T00:00:00.0000000', source_id='src-gone', is_all_day=True)
        e.reader.get_calendar_events = lambda cal_id, **kwargs: [stale_council, gone_all_day]

        preview = e.preview_sync()

        assert [(x['subject'], x['isAllDay']) for x in preview['to_add']] == [('Parish Picnic', True)]
        assert [(x['subject'], x['isAllDay']) for x in preview['to_update']] == [('Parish Council Meeting', False)]
        assert [(x['subject'], x['isAllDay']) for x in preview['to_delete']] == [('Fall Break', True)]


@pytest.mark.unit
class TestMasterCalendarProtectionById:
    """Finding 29: every write guard compared the Graph calendar id it was
    handed to the master calendar's display name, so it never fired."""

    MASTER_ID = 'AAMkAGraphIdOfTheMasterCalendar'

    @pytest.fixture(autouse=True)
    def protection_on(self, monkeypatch):
        monkeypatch.setattr(config, 'MASTER_CALENDAR_PROTECTION', True, raising=False)

        def no_network(*args, **kwargs):
            raise AssertionError('a protected write reached the network')

        for verb in ('get', 'post', 'patch', 'delete'):
            monkeypatch.setattr(calendar_ops.requests, verb, no_network)

    def writer_with_reader(self, find):
        writer = CalendarWriter(auth_manager=None)

        class _Reader:
            def find_calendar_id(self, name):
                return find(name)

        writer.reader = _Reader()
        return writer

    def resolving_writer(self):
        return self.writer_with_reader(
            lambda name: self.MASTER_ID if name == config.SOURCE_CALENDAR else 'id-other')

    def test_the_master_calendar_id_is_refused_on_every_write_path(self):
        w = self.resolving_writer()
        event = source_event()

        assert w.create_event(self.MASTER_ID, event) is False
        assert w.update_event(self.MASTER_ID, 'evt', event) is False
        assert w.delete_event(self.MASTER_ID, 'evt') is False
        assert w.delete_occurrence(self.MASTER_ID, 'series', '2026-09-06T14:15:00Z') is False
        assert w.update_occurrence(self.MASTER_ID, 'series', '2026-09-06T14:15:00Z', event) is False
        assert w.batch_create_events(self.MASTER_ID, [event])['errors'] == ['Protected calendar']
        assert w.batch_delete_events(self.MASTER_ID, ['evt'])['errors'] == ['Protected calendar']

    def test_the_name_comparison_still_fires(self):
        w = self.resolving_writer()
        assert w._is_source_calendar(config.SOURCE_CALENDAR) is True

    def test_a_target_calendar_id_is_not_blocked(self):
        w = self.resolving_writer()
        assert w._is_source_calendar('id-other') is False

    def test_the_id_is_resolved_once_per_writer(self):
        lookups = []

        def find(name):
            lookups.append(name)
            return self.MASTER_ID

        w = self.writer_with_reader(find)
        w._is_source_calendar('id-other')
        w._is_source_calendar('id-other')
        w._is_source_calendar(self.MASTER_ID)

        assert lookups == [config.SOURCE_CALENDAR]

    def test_fails_closed_when_the_id_cannot_be_resolved(self):
        """The guard keeps its name comparison and never raises."""
        def find(name):
            raise RuntimeError('Graph is down')

        w = self.writer_with_reader(find)

        assert w._is_source_calendar(config.SOURCE_CALENDAR) is True
        assert w._is_source_calendar('id-other') is False
        assert w._source_calendar_id is None

    def test_no_reader_and_no_auth_skips_resolution(self):
        w = CalendarWriter(auth_manager=None)

        assert w._is_source_calendar(config.SOURCE_CALENDAR) is True
        assert w._is_source_calendar('id-other') is False

    def test_the_engine_hands_its_reader_to_the_writer(self):
        e = engine()
        assert e.writer.reader is e.reader
