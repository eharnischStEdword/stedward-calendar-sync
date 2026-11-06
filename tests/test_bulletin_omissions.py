# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for is_omitted_from_bulletin, the filter that keeps standing liturgical
times out of the weekly bulletin's event list.

Saturday Confession sits on the public calendar under the bare title
"Confession" (8:50 AM Central), which the "Adoration & Confession" rule never
matched, so it kept landing in the bulletin. The Saturday rule now matches on
the title and the Central weekday only, never the clock time, because the UTC
hour shifts twice a year with daylight saving.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import is_omitted_from_bulletin  # noqa: E402


def utc(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


@pytest.mark.unit
class TestSaturdayConfession:
    def test_saturday_confession_in_standard_time_is_omitted(self):
        # 2026-11-07 14:50Z is Saturday 8:50 AM CST
        assert is_omitted_from_bulletin("Confession", utc("2026-11-07T14:50:00"), None) is True

    def test_saturday_confession_in_daylight_time_is_omitted(self):
        # 2026-06-06 13:50Z is Saturday 8:50 AM CDT
        assert is_omitted_from_bulletin("Confession", utc("2026-06-06T13:50:00"), None) is True

    def test_plural_and_suffix_still_match(self):
        assert is_omitted_from_bulletin("Confessions", utc("2026-11-07T14:50:00"), "Church") is True
        assert is_omitted_from_bulletin("Confession - Church", utc("2026-11-07T14:50:00"), None) is True

    def test_weekday_is_central_not_utc(self):
        # 2026-11-07 02:00Z is Saturday in UTC but Friday 8:00 PM Central
        assert is_omitted_from_bulletin("Confession", utc("2026-11-07T02:00:00"), None) is False

    def test_first_confession_stays_in_bulletin(self):
        assert is_omitted_from_bulletin("First Confession", utc("2026-11-07T15:00:00"), "Church") is False

    def test_confession_on_another_day_is_untouched(self):
        # 2026-11-11 00:00Z is Tuesday 6:00 PM Central; not the standing Saturday slot
        assert is_omitted_from_bulletin("Confession", utc("2026-11-11T00:00:00"), None) is False


@pytest.mark.unit
class TestExistingRulesStillHold:
    def test_adoration_and_confession_omitted_any_day(self):
        assert is_omitted_from_bulletin("Adoration & Confession", utc("2026-11-04T22:30:00"), None) is True

    def test_daily_mass_omitted(self):
        assert is_omitted_from_bulletin("Mass- Daily", utc("2026-11-07T14:00:00"), "Church") is True

    def test_ordinary_saturday_event_kept(self):
        assert is_omitted_from_bulletin("Knights of Columbus Breakfast", utc("2026-11-07T15:00:00"), "Cafeteria") is False

    def test_naive_datetime_is_treated_as_utc(self):
        naive = datetime(2026, 11, 7, 14, 50)
        assert is_omitted_from_bulletin("Confession", naive, None) is True
