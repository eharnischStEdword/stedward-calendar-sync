# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests that a failed calendar read can never be mistaken for an empty calendar.

get_calendar_events() pages through calendarView. Before this suite existed it
handled a mid-pagination failure by breaking out of the loop and returning the
pages it already had, and its outer handler returned [] for any exception. Both
answers are the same shape as a real, short calendar, so a transient Graph error
on one weekly window made the sync believe those events were gone and plan
deletions for all of them. MAX_RUN_DELETIONS was the only thing standing in the
way of that reaching the parish calendar.

The rule these pin: a read either returns everything it was asked for, or it
raises. It never returns part of an answer as though it were the whole one.
"""
import os
import sys

import pytest
import pytz
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import calendar_ops  # noqa: E402
import utils  # noqa: E402
from calendar_ops import CalendarReader  # noqa: E402


class FakeResponse:
    """Stands in for a requests.Response over the fields the reader touches."""

    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class FakeAuth:
    def get_headers(self):
        return {'Authorization': 'Bearer test-token'}

    def refresh_access_token(self):
        return False


def event(subject):
    return {
        'id': subject,
        'subject': subject,
        'categories': ['Public'],
        'showAs': 'busy',
        'type': 'singleInstance',
        'isCancelled': False,
        'start': {'dateTime': '2026-09-06T10:30:00.0000000', 'timeZone': 'UTC'},
        'end': {'dateTime': '2026-09-06T11:30:00.0000000', 'timeZone': 'UTC'}
    }


@pytest.fixture(autouse=True)
def no_backoff_sleeping(monkeypatch):
    """The reader retries three times with backoff; do not really wait for it."""
    monkeypatch.setattr(utils.time, 'sleep', lambda *args, **kwargs: None)


@pytest.fixture
def window():
    tz = pytz.timezone('America/Chicago')
    start = tz.localize(datetime(2026, 9, 1, 0, 0, 0))
    end = tz.localize(datetime(2026, 9, 8, 0, 0, 0))
    return start, end


def reader_over(monkeypatch, responses):
    """A CalendarReader whose HTTP layer replays the given responses in order."""
    calls = {'count': 0}

    def fake_get(*args, **kwargs):
        index = calls['count']
        calls['count'] += 1
        if index < len(responses):
            return responses[index]
        return responses[-1]

    monkeypatch.setattr(calendar_ops.requests, 'get', fake_get)
    return CalendarReader(auth_manager=FakeAuth()), calls


class TestAFailedPageIsNotAShortCalendar:

    def test_a_failed_second_page_raises_instead_of_returning_page_one(self, monkeypatch, window):
        """The whole defect in one test: page 1 arrives, page 2 fails."""
        start, end = window
        page_one = FakeResponse(200, {
            'value': [event('Mass'), event('Confession')],
            '@odata.nextLink': 'https://graph.microsoft.com/v1.0/nextpage'
        })
        page_two = FakeResponse(500, text='Internal Server Error')
        reader, _ = reader_over(monkeypatch, [page_one, page_two])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)

    def test_a_read_that_fails_outright_is_never_reported_as_empty(self, monkeypatch, window):
        """[] means "no events on this calendar" and nothing else. It is the
        answer that makes the sync delete the target, so a failure may not
        borrow it."""
        start, end = window
        reader, _ = reader_over(monkeypatch, [FakeResponse(500, text='Service Unavailable')])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)

    def test_the_retry_decorator_actually_gets_its_attempts(self, monkeypatch, window):
        """Breaking out of the loop returned normally, so the retry decorator
        never saw a failure and never retried. Raising is what wakes it up."""
        start, end = window
        reader, calls = reader_over(monkeypatch, [FakeResponse(500, text='boom')])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)

        assert calls['count'] == 3, (
            f"expected 3 attempts from retry_with_backoff, saw {calls['count']}"
        )


class TestAnAuthFailureIsNotAnEmptyCalendar:
    """The two returns the failed-page fix left behind. Neither raised, so the
    retry decorator never fired and _sync_pair's `or []` read the None as a
    week with nothing on it: deletions planned on a source miss, duplicates
    created on a target miss."""

    def test_a_401_that_cannot_be_refreshed_raises(self, monkeypatch, window):
        start, end = window
        reader, _ = reader_over(monkeypatch, [FakeResponse(401, text='Unauthorized')])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)

    def test_a_401_that_cannot_be_refreshed_raises_mid_pagination(self, monkeypatch, window):
        """Page 1 arrives, page 2 is a 401 and the refresh fails. The events
        from page 1 must not come back as the whole calendar."""
        start, end = window
        page_one = FakeResponse(200, {
            'value': [event('Mass')],
            '@odata.nextLink': 'https://graph.microsoft.com/v1.0/nextpage'
        })
        reader, _ = reader_over(monkeypatch, [page_one, FakeResponse(401, text='Unauthorized')])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)

    def test_no_headers_at_all_raises_before_any_request(self, monkeypatch, window):
        """get_headers() returns None when the token refresh itself failed."""
        start, end = window

        class NoHeadersAuth(FakeAuth):
            def get_headers(self):
                return None

        def must_not_be_called(*args, **kwargs):
            raise AssertionError('no request should go out without headers')

        monkeypatch.setattr(calendar_ops.requests, 'get', must_not_be_called)
        reader = CalendarReader(auth_manager=NoHeadersAuth())

        with pytest.raises(Exception, match='Authentication failed'):
            reader.get_calendar_events('cal-id', start=start, end=end)

    def test_instances_read_with_no_headers_raises(self, monkeypatch):
        """get_calendar_instances had the same pair of None returns."""

        class NoHeadersAuth(FakeAuth):
            def get_headers(self):
                return None

        reader = CalendarReader(auth_manager=NoHeadersAuth())

        with pytest.raises(Exception, match='Authentication failed'):
            reader.get_calendar_instances('cal-id', '2026-09-01T00:00:00Z', '2026-09-08T00:00:00Z')

    def test_instances_401_that_cannot_be_refreshed_raises(self, monkeypatch):
        reader, _ = reader_over(monkeypatch, [FakeResponse(401, text='Unauthorized')])

        with pytest.raises(Exception, match='Authentication failed'):
            reader.get_calendar_instances('cal-id', '2026-09-01T00:00:00Z', '2026-09-08T00:00:00Z')

    def test_an_empty_week_from_get_public_events_is_a_list_not_none(self, monkeypatch, window):
        """Downstream, get_public_events turned an empty read into None, the
        same value the failed reads used. Empty is now spelled []."""
        start, end = window
        reader, _ = reader_over(monkeypatch, [FakeResponse(200, {'value': []})])

        assert reader.get_public_events('cal-id', start=start, end=end) == []


class TestAnEmptyCalendarIsStillAllowedToBeEmpty:
    """The correction must not swing the other way: a parish calendar with
    nothing on it is a legitimate answer, not an error."""

    def test_a_genuinely_empty_calendar_returns_an_empty_list(self, monkeypatch, window):
        start, end = window
        reader, _ = reader_over(monkeypatch, [FakeResponse(200, {'value': []})])

        assert reader.get_calendar_events('cal-id', start=start, end=end) == []

    def test_a_single_full_page_returns_its_events(self, monkeypatch, window):
        start, end = window
        reader, _ = reader_over(monkeypatch, [
            FakeResponse(200, {'value': [event('Mass'), event('Adoration')]})
        ])

        events = reader.get_calendar_events('cal-id', start=start, end=end)
        assert [e['subject'] for e in events] == ['Mass', 'Adoration']

    def test_every_page_of_a_complete_read_is_returned(self, monkeypatch, window):
        start, end = window
        reader, _ = reader_over(monkeypatch, [
            FakeResponse(200, {
                'value': [event('Mass')],
                '@odata.nextLink': 'https://graph.microsoft.com/v1.0/nextpage'
            }),
            FakeResponse(200, {'value': [event('Adoration')]})
        ])

        events = reader.get_calendar_events('cal-id', start=start, end=end)
        assert [e['subject'] for e in events] == ['Mass', 'Adoration']


class TestRunawayPaginationIsNotSilentlyTruncated:

    def test_passing_the_page_cap_raises_rather_than_returning_what_it_has(self, monkeypatch, window):
        """A calendar that never stops paging used to stop at 50 pages and hand
        back those 50 as if they were the whole calendar."""
        start, end = window
        endless = FakeResponse(200, {
            'value': [event('Mass')],
            '@odata.nextLink': 'https://graph.microsoft.com/v1.0/nextpage'
        })
        reader, _ = reader_over(monkeypatch, [endless])

        with pytest.raises(Exception):
            reader.get_calendar_events('cal-id', start=start, end=end)
