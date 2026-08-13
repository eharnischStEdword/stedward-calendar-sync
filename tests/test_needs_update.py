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

from sync import SyncEngine, body_comparison_key  # noqa: E402


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


class TestGraphMaterializedDefaults:
    """Graph returns isAllDay explicitly; _prepare_event_for_api only sets it when true.

    So a timed event compared None (prepared, key absent) against False (target,
    materialized by Graph) and reported an all-day flag change on every event
    that is not all-day, which is nearly all of them. Same defect class as the
    lastModifiedDateTime check: a field whose absence means 'default' compared
    against a side that spells the default out.
    """

    def graph_target(self, engine_, source_event, is_all_day=False):
        """A target as Graph hands it back, with isAllDay spelled out."""
        t = target_from(engine_, source_event)
        t['isAllDay'] = is_all_day
        return t

    def test_absent_flag_matches_explicit_false(self):
        e = engine()
        assert e._needs_update(source(), self.graph_target(e, source())) is False

    def test_real_all_day_change_still_detected(self):
        """A source that became all-day against a target that is not."""
        e = engine()
        target = self.graph_target(e, source(), is_all_day=False)
        assert e._needs_update(source(isAllDay=True), target) is True

    def test_all_day_source_matches_all_day_target(self):
        e = engine()
        target = self.graph_target(e, source(isAllDay=True), is_all_day=True)
        assert e._needs_update(source(isAllDay=True), target) is False

    def test_body_change_still_detected_against_materialized_target(self):
        e = engine()
        target = self.graph_target(e, source(body='Old text'))
        assert e._needs_update(source(), target) is True


class TestMarkerRoundTrip:
    """The marker never survives the round trip to Outlook.

    A public-calendar body is written as nothing but '<!-- SYNC_ID:... -->'
    and Graph reads it back as ''. Comparing those raw reported a description
    change on all 1,893 events on that calendar, every 23 minutes, forever.
    These pin the exact pair of values taken from the live logs.
    """

    MARKER = '<!-- SYNC_ID:AAMkADFhZjE2NzRhLWExMmUtNGFhOC05MjMx -->'

    def test_marker_only_against_empty_target_is_not_a_change(self):
        """The 1,893 case, verbatim from the production logs."""
        e = engine(copy_body=False)
        target = target_from(e, source())
        target['body'] = {'contentType': 'HTML', 'content': ''}
        assert e._needs_update(source(), target) is False

    def test_missing_body_key_on_target_is_not_a_change(self):
        e = engine(copy_body=False)
        target = target_from(e, source())
        target.pop('body', None)
        assert e._needs_update(source(), target) is False

    def test_real_description_still_detected_through_the_marker(self):
        """The feature this whole chain exists to deliver must still work."""
        e = engine(copy_body=True)
        target = target_from(e, source(body='Old text'))
        target['body'] = {'contentType': 'HTML', 'content': 'Old text'}
        assert e._needs_update(source(body='New text'), target) is True

    def test_outlook_html_rewrite_is_not_a_change(self):
        """Outlook stores a wrapped document; that rewrite is not an edit."""
        e = engine(copy_body=True)
        wrapped = (
            '<html><head><meta http-equiv="Content-Type" content="text/html"></head>'
            '<body><div>Coffee &amp; Donuts. Topic: Faith &amp; Family</div>'
            f'{self.MARKER}</body></html>'
        )
        target = target_from(e, source())
        target['body'] = {'contentType': 'HTML', 'content': wrapped}
        assert e._needs_update(source(), target) is False


class TestBodyComparisonKey:
    def test_marker_only_collapses_to_empty(self):
        assert body_comparison_key(TestMarkerRoundTrip.MARKER) == ''

    def test_empty_and_none_collapse_to_empty(self):
        assert body_comparison_key('') == ''
        assert body_comparison_key(None) == ''

    def test_entities_and_tags_are_normalized(self):
        assert body_comparison_key('<p>Coffee &amp;  Donuts</p>') == 'coffee & donuts'

    def test_distinct_text_stays_distinct(self):
        assert body_comparison_key('<p>Old</p>') != body_comparison_key('<p>New</p>')
