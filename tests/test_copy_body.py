# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for per-target event descriptions (COPY_BODY_TARGETS).

Source event bodies routinely contain gate codes and door-access times, so the
sync has always replaced the target body with a marker comment. Sundays At St.
Edward publishes its descriptions on sundayatstedward.org and needs the real
body. These tests pin the boundary:

  1. Off by default. A writer nobody configured still strips the body.
  2. The public calendar can never be opted in, even if it is named explicitly.
  3. When a target is opted in, the real description survives AND keeps the
     SYNC_ID marker, which is how the service recognizes its own events.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config as config_module  # noqa: E402
from calendar_ops import CalendarWriter  # noqa: E402


def reload_config(**env):
    """Reload config with a specific environment, then restore the old one."""
    saved = {}
    for key in ('TARGET_CALENDAR', 'COPY_BODY_TARGETS'):
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
    """Leave config as we found it, whatever a test did to the environment."""
    yield
    importlib.reload(config_module)


def source_event(content='Coffee &amp; Donuts. Topic: Faith &amp; Family', content_type='html'):
    return {
        'id': 'src-1',
        'subject': 'Family Faith Formation Kick-Off',
        'start': {'dateTime': '2026-09-06T14:15:00.0000000', 'timeZone': 'UTC'},
        'end': {'dateTime': '2026-09-06T15:25:00.0000000', 'timeZone': 'UTC'},
        'body': {'contentType': content_type, 'content': content},
    }


def prepare(copy_body, event=None):
    writer = CalendarWriter(auth_manager=None)
    writer.copy_body = copy_body
    return writer._prepare_event_for_api(event or source_event())


class TestDefaults:
    def test_writer_strips_body_by_default(self):
        """A writer nobody configured must not carry descriptions."""
        writer = CalendarWriter(auth_manager=None)
        assert writer.copy_body is False
        body = writer._prepare_event_for_api(source_event())['body']['content']
        assert body == '<!-- SYNC_ID:src-1 -->'
        assert 'Faith &amp; Family' not in body

    def test_no_env_means_no_targets(self):
        cfg = reload_config()
        assert cfg.get_copy_body_targets() == set()
        assert cfg.copies_body('Sundays At St. Edward') is False


class TestPublicCalendarRail:
    def test_public_calendar_cannot_be_opted_in(self):
        """The whole point of the strip. Naming it explicitly must not work."""
        cfg = reload_config(
            TARGET_CALENDAR='St. Edward Public Calendar',
            COPY_BODY_TARGETS='St. Edward Public Calendar',
        )
        assert cfg.copies_body('St. Edward Public Calendar') is False

    def test_public_rail_ignores_case_and_padding(self):
        cfg = reload_config(
            TARGET_CALENDAR='St. Edward Public Calendar',
            COPY_BODY_TARGETS='  st. edward   public calendar  ',
        )
        assert cfg.copies_body('St. Edward Public Calendar') is False

    def test_rail_does_not_block_other_targets_in_same_list(self):
        cfg = reload_config(
            TARGET_CALENDAR='St. Edward Public Calendar',
            COPY_BODY_TARGETS='St. Edward Public Calendar;Sundays At St. Edward',
        )
        assert cfg.copies_body('St. Edward Public Calendar') is False
        assert cfg.copies_body('Sundays At St. Edward') is True


class TestOptedInTarget:
    def test_named_target_is_opted_in(self):
        cfg = reload_config(
            TARGET_CALENDAR='St. Edward Public Calendar',
            COPY_BODY_TARGETS='Sundays At St. Edward',
        )
        assert cfg.copies_body('Sundays At St. Edward') is True

    def test_matching_ignores_case_and_whitespace(self):
        """Calendar names get pasted into hosting-panel textareas."""
        cfg = reload_config(
            TARGET_CALENDAR='St. Edward Public Calendar',
            COPY_BODY_TARGETS=' sundays  at st. edward ',
        )
        assert cfg.copies_body('Sundays At St. Edward') is True

    def test_description_survives(self):
        body = prepare(True)['body']['content']
        assert 'Faith &amp; Family' in body

    def test_marker_still_present(self):
        """clear_synced_events_only finds our events by this marker."""
        body = prepare(True)['body']['content']
        assert '<!-- SYNC_ID:src-1 -->' in body

    def test_marker_is_not_duplicated_on_repeat_preparation(self):
        """Re-preparing an already-synced body must not stack markers."""
        once = prepare(True)['body']['content']
        twice = prepare(True, {**source_event(), 'body': {'contentType': 'html', 'content': once}})['body']['content']
        assert twice.count('SYNC_ID:src-1') == 1

    def test_empty_body_is_marker_only(self):
        """Events with no description behave exactly as before, so the first
        sync after this change does not churn every event on the calendar."""
        event = {**source_event(), 'body': {'contentType': 'html', 'content': ''}}
        assert prepare(True, event)['body']['content'] == '<!-- SYNC_ID:src-1 -->'

    def test_missing_body_key_does_not_raise(self):
        event = {k: v for k, v in source_event().items() if k != 'body'}
        assert prepare(True, event)['body']['content'] == '<!-- SYNC_ID:src-1 -->'

    def test_text_body_is_escaped_and_sent_as_html(self):
        """A text body would otherwise render the marker as visible text."""
        prepared = prepare(True, source_event(content='Bring donuts & coffee', content_type='text'))
        assert prepared['body']['contentType'] == 'HTML'
        assert 'donuts &amp; coffee' in prepared['body']['content']
        assert '<!-- SYNC_ID:src-1 -->' in prepared['body']['content']
