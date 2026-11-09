# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for the text the weekly bulletin page prints and copies.

Eric asked on 2026-11-09 for three things: the Sunday heading reads
"Sunday at St. Edward, November 8", the "PARISH EVENTS" title line is gone (he deleted it
by hand every week), and day numbers carry no leading zero ("November 3",
never "November 03"). The template prints everything twice, once for display
and once in the hidden block the Copy button reads, so the render test checks
both copies agree.
"""
import os
import sys
from datetime import date, datetime

import pytest
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import bulletin_date, bulletin_day_header  # noqa: E402

TEMPLATES = os.path.join(os.path.dirname(__file__), '..', 'templates')


@pytest.mark.unit
class TestBulletinDate:
    def test_single_digit_day_is_not_padded(self):
        assert bulletin_date(date(2026, 11, 3)) == "November 3"

    def test_double_digit_day_unchanged(self):
        assert bulletin_date(date(2026, 11, 14)) == "November 14"

    def test_with_year(self):
        assert bulletin_date(date(2026, 1, 3), with_year=True) == "January 3, 2026"


@pytest.mark.unit
class TestBulletinDayHeader:
    def test_sunday_reads_sunday_at_st_edward(self):
        assert bulletin_day_header(date(2026, 11, 8)) == "Sunday at St. Edward, November 8"

    def test_other_days_carry_the_date(self):
        assert bulletin_day_header(date(2026, 11, 9)) == "Monday, November 9"
        assert bulletin_day_header(date(2026, 11, 14)) == "Saturday, November 14"


def _render_week(start=date(2026, 11, 1)):
    """Render the bulletin template for a Sunday-to-Saturday week with one event."""
    from datetime import timedelta
    days = []
    for offset in range(7):
        d = start + timedelta(days=offset)
        events = []
        if offset == 1:
            events.append({
                'start': datetime(d.year, d.month, d.day, 9, 5),
                'is_all_day': False,
                'subject': 'Rosary',
                'location': 'Church',
            })
        days.append({
            'date': d,
            'day_name': d.strftime('%A'),
            'date_str': bulletin_date(d),
            'header': bulletin_day_header(d),
            'events': events,
        })
    env = Environment(loader=FileSystemLoader(TEMPLATES))
    end = start + timedelta(days=6)
    return env.get_template('bulletin_events.html').render(
        start_date=bulletin_date(start),
        end_date=bulletin_date(end, with_year=True),
        days=days,
        week_label='Upcoming Week',
        week_param='upcoming',
        generated_time='Nov 9, 2026 at 10:00 AM CT',
    )


@pytest.mark.unit
class TestBulletinTemplate:
    def test_parish_events_title_is_gone(self):
        assert "PARISH EVENTS" not in _render_week()

    def test_sunday_header_in_display_and_plaintext(self):
        html = _render_week()
        assert html.count("Sunday at St. Edward, November 1") == 2
        assert "Sunday, November" not in html

    def test_no_leading_zero_anywhere(self):
        html = _render_week(date(2026, 11, 1))
        assert "November 1 - November 7, 2026" in html
        assert "Monday, November 2" in html
        assert "November 01" not in html and "November 02" not in html

    def test_plaintext_block_starts_with_date_range(self):
        html = _render_week(date(2026, 11, 1))
        plaintext = html.split('<div id="plaintext-content">', 1)[1]
        assert plaintext.startswith("November 1 - November 7, 2026\n")
        assert "9:05 AM - Rosary (Church)" in plaintext
