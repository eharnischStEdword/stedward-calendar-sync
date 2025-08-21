from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")

def normalize_location(location: str | None) -> str | None:
    if not location:
        return location
    name = location.strip()
    if name == "Cafeteria Rental":
        return "School Cafeteria"
    if name == "Little Carrell Room":
        return "Little Carell Room"  # fix the typo
    return name

def is_omitted_from_bulletin(subject: str, starts_at_utc: datetime, location: str | None) -> bool:
    """
    Return True if this event should be hidden from *bulletin lists only*,
    based on local (America/Chicago) weekday + time + title.
    Never used for Outlook or Public Calendar sync decisions.
    """
    if starts_at_utc.tzinfo is None:
        raise ValueError("starts_at_utc must be timezone-aware (UTC).")

    local = starts_at_utc.astimezone(CENTRAL)
    wd = local.weekday()  # Mon=0 .. Sun=6
    hhmm = local.strftime("%H:%M")
    title = (subject or "").strip()

    # Helper for quick checks
    def has(loc_expected: str | None = None) -> bool:
        if not loc_expected:
            return True
        loc_norm = normalize_location(location or "")
        return loc_norm == loc_expected

    # -----------------------
    # SATURDAYS (wd=5)
    if wd == 5:
        if hhmm == "07:00" and title == "Adoration & Confession" and has("Church"):
            return True
        if hhmm == "08:00" and title == "Mass- Daily":
            return True
        if hhmm == "17:00" and title == "Mass- Vigil":
            return True

    # SUNDAYS (wd=6)
    if wd == 6:
        if hhmm == "08:00" and title.startswith("Mass- 8:00"):
            return True
        if hhmm == "10:30" and title.startswith("Mass- 10:30"):
            return True
        if hhmm == "12:15" and title.startswith("Mass- 12:15"):
            return True
        if hhmm == "14:30" and title == "Zomi Mass":
            return True

    # MONDAYS (wd=0)
    if wd == 0:
        if hhmm == "07:00" and title == "Adoration & Confession" and has("Church"):
            return True
        if hhmm == "08:00" and title == "Mass- Daily":
            return True

    # TUESDAYS (wd=1)
    if wd == 1:
        if hhmm == "07:00" and title == "Adoration & Confession" and has("Church"):
            return True
        if hhmm == "08:00" and title == "Mass- Daily":
            return True

    # WEDNESDAYS (wd=2)
    if wd == 2:
        if hhmm == "16:30" and title == "Adoration & Confession":
            return True
        if hhmm == "17:30" and title == "Mass- Wednesday":
            return True

    # THURSDAYS (wd=3)
    if wd == 3:
        if hhmm == "07:00" and title == "Adoration & Confession" and has("Church"):
            return True
        if hhmm == "08:00" and title == "Mass- Daily":
            return True

    return False
