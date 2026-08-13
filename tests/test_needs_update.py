# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for _needs_update, the check that decides whether an already-synced event
gets rewritten.

It used to compare source and target lastModifiedDateTime and return False when
they matched. get_calendar_events never requests that field, so both sides were
None, None == None was True, and no matching event was ever updated. Every other
kind of edit changes the event signature and is handled as a delete plus an add,
so the only visible symptom was that description edits never reached the target.

These tests pin the behaviour that fix depends on.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sync import SyncEngine  # noqa: E402


def engine(copy_body=True):
    e = SyncEngine(auth_manager=None)
    e.writer.copy_body = copy_body
    return e


def source(body='Coffee &amp; Donuts. Topic: Faith &amp; Family', **extra):
    event = {
        'id': 'src-1',
        'subject': 'Family Faith Formation Kick-Off',
        'start': {'dateTime': '2026-09-06T14:15:00', 'timeZone': 'UTC'},
        'end': {'dateTime': '2026-09-06T15:25:00', 'timeZone': 'UTC'},
        'showAs': 'busy',
        'categories': ['SAS'],
        'body': {'contentType': 'html', 'content': body},
    }
    event.update(extra)
    return event


def target_from(engine_, source_event, **overrides):
    """The target as the writer would have created it, before any overrides."""
    prepared = engine_.writer._prepare_event_data(source_event)
    prepared['id'] = 'tgt-1'
    prepared.update(overrides)
    return prepared


class TestBodyChanges:
    def test_description_change_is_detected(self):
        """The whole point. Neither side has lastModifiedDateTime."""
        e = engine()
        stale = target_from(e, source(body='Old text'))
        assert e._needs_update(source(), stale) is True

    def test_marker_only_target_gets_the_description(self):
        """The state every SAS event was in before this feature existed."""
        e = engine()
        stale = target_from(e, source(), body={'contentType': 'HTML', 'content': '<!-- SYNC_ID:src-1 -->'})
        assert e._needs_update(source(), stale) is True

    def test_identical_event_is_left_alone(self):
        """No churn: a target already matching its source must not be rewritten."""
        e = engine()
        assert e._needs_update(source(), target_from(e, source())) is False

    def test_body_ignored_when_target_does_not_copy_it(self):
        """A description edit must not churn the public calendar, which only
        ever holds the marker."""
        e = engine(copy_body=False)
        assert e._needs_update(source(body='Rewritten entirely'), target_from(e, source())) is False


class TestModificationTimes:
    def test_absent_timestamps_do_not_short_circuit(self):
        """The regression itself: None on both sides once meant 'no change'."""
        e = engine()
        stale = target_from(e, source(body='Old text'))
        assert 'lastModifiedDateTime' not in stale
        assert e._needs_update(source(), stale) is True

    def test_equal_timestamps_still_short_circuit(self):
        """Preserved: two events genuinely stamped the same are the same event."""
        e = engine()
        stamp = '2026-08-13T20:00:00Z'
        stale = target_from(e, source(body='Old text'), lastModifiedDateTime=stamp)
        assert e._needs_update(source(lastModifiedDateTime=stamp), stale) is False

    def test_differing_timestamps_fall_through_to_content(self):
        e = engine()
        stale = target_from(e, source(), lastModifiedDateTime='2026-08-13T19:00:00Z')
        assert e._needs_update(source(lastModifiedDateTime='2026-08-13T20:00:00Z'), stale) is False


class TestOtherFields:
    def test_time_change_is_detected(self):
        e = engine()
        stale = target_from(e, source())
        moved = source()
        moved['start'] = {'dateTime': '2026-09-06T15:15:00', 'timeZone': 'UTC'}
        assert e._needs_update(moved, stale) is True

    def test_category_change_is_detected(self):
        e = engine()
        stale = target_from(e, source())
        assert e._needs_update(source(categories=['SAS', 'Public']), stale) is True


class TestGraphFractionalSeconds:
    """Graph returns seven fractional digits; the writer strips them.

    Comparing the two raw made every event look time-changed, which turned the
    first working sync into a 1,890-event rewrite of the public calendar,
    repeating every 23 minutes.
    """

    def graph_target(self, engine_, source_event):
        """A target as Graph hands it back, fractional seconds and all."""
        t = target_from(engine_, source_event)
        t['start'] = {'dateTime': '2026-09-06T14:15:00.0000000', 'timeZone': 'UTC'}
        t['end'] = {'dateTime': '2026-09-06T15:25:00.0000000', 'timeZone': 'UTC'}
        return t

    def test_fractional_seconds_alone_are_not_a_change(self):
        e = engine()
        assert e._needs_update(source(), self.graph_target(e, source())) is False

    def test_real_time_change_still_detected_against_graph_format(self):
        e = engine()
        target = self.graph_target(e, source())
        moved = source()
        moved['start'] = {'dateTime': '2026-09-06T15:15:00', 'timeZone': 'UTC'}
        assert e._needs_update(moved, target) is True

    def test_body_change_detected_against_graph_format(self):
        """The case that matters: same time, new description."""
        e = engine()
        target = self.graph_target(e, source(body='Old text'))
        assert e._needs_update(source(), target) is True

    def test_trailing_z_is_not_a_change(self):
        e = engine()
        target = self.graph_target(e, source())
        target['start'] = {'dateTime': '2026-09-06T14:15:00Z', 'timeZone': 'UTC'}
        assert e._needs_update(source(), target) is False
