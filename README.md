# St. Edward Calendar Sync

Copies events from one private Microsoft 365 calendar to one or more public
calendars, filtered by Outlook category.

Staff keep a single master calendar. Tagging an event with a category
publishes it to the matching public calendar. Nothing is entered twice, and
nothing untagged is ever exposed.

---

## 1. What problem this solves

St. Edward Church & School runs its operations from one shared mailbox,
`calendar@stedward.org`. Its `Calendar` holds everything: Masses, weddings,
maintenance visits, staff meetings, private appointments.

The parish website needs to show some of those events. Handing the public a
view of the master calendar would leak private entries, and asking staff to
re-enter events into a second calendar guarantees the two drift apart.

This service resolves that. A staff member tags an event with an Outlook
category. Every 23 minutes the service copies the tagged events to the
matching public calendar, updates the ones that changed, and removes the ones
it previously copied that no longer qualify. The master calendar is never
modified.

**Two categories are live today:**

| Outlook category | Copied to | Consumed by |
|---|---|---|
| `Public` | `St. Edward Public Calendar` | stedward.org parish calendar |
| `SAS` | `Sundays At St. Edward` | the Sundays at St. Edward campaign site |

An event tagged with both appears on both. An event tagged with neither
appears on neither.

---

## 2. How an event actually flows

```
  Outlook: calendar@stedward.org  ─┐
  ┌─────────────────────────────┐  │
  │ Calendar   (master, private)│  │  Staff tag an event: "Public", "SAS", both
  │  • Daily Mass      [Public] │  │
  │  • SAS Kickoff        [SAS] │  │
  │  • Launch    [Public][SAS]  │  │
  │  • Staff mtg   (no category)│  │
  └─────────────────────────────┘  │
                 │                 │
                 ▼                 │  every 23 min, per configured pair:
     ┌───────────────────────┐     │    1. read master in weekly chunks, keep
     │  SyncEngine._do_sync  │     │       events whose category matches AND
     │  loops the pairs      │     │       showAs is busy AND not cancelled
     └───────────────────────┘     │    2. read the target calendar
          │            │           │    3. collapse repeat sightings by Graph id
          ▼            ▼           │    4. compare by signature + sourceEventId
  ┌──────────────┐ ┌────────────┐  │    5. add / update / delete
  │ St. Edward   │ │ Sundays At │  │
  │ Public Cal.  │ │ St. Edward │  │
  │ Daily Mass   │ │ SAS Kickoff│  │
  │ Launch       │ │ Launch     │  │
  └──────────────┘ └────────────┘  │
        │                 │        │
        ▼                 ▼        │
   stedward.org    published .ics ─┘
                   feed to campaign site
```

**An event qualifies only if all four hold:**

1. It carries the pair's category (compared case-insensitively).
2. `showAs` is one of `busy`, `tentative`, `oof`, `workingElsewhere`.
   A `free` event is treated as a placeholder and skipped. The other three
   qualify alongside `busy` because each still means the person is not free.
   Graph's full set is `free`, `tentative`, `busy`, `oof`, `workingElsewhere`,
   `unknown` ([reference](https://learn.microsoft.com/en-us/graph/api/resources/event)).
3. It is not cancelled.
4. It falls inside the sync window: `SYNC_CUTOFF_DAYS` back to
   `SYNC_LOOKAHEAD_DAYS` ahead.

---

## 3. The safety rules that matter

Read this section before changing sync or delete logic. Each rule exists
because its absence caused or nearly caused data loss.

**The master calendar is never written to.** `MASTER_CALENDAR_PROTECTION`
guards every write path in `calendar_ops.py`. Separately, `get_sync_pairs()`
(`config.py:79`) drops any pair whose target is `SOURCE_CALENDAR`, so a
configuration typo cannot aim writes at the master.

**The public calendar never receives a source description.** This is the one
rule that protects people rather than data. Source events routinely carry gate
codes and door-access times, and `St. Edward Public Calendar` is embedded on
the parish website. `_prepare_event_for_api` (`calendar_ops.py:1052-1056`)
writes those copies a body containing nothing but the sync marker. Opting the
public calendar into `COPY_BODY_TARGETS` is not merely discouraged, it is
refused and logged in `config.py:64-69`, because a typo in that one variable is
the single mistake that would publish a door code. Do not weaken either half.

**Deletions only ever touch events this service created.**
`_is_synced_event()` (`sync.py:1496`) gates every deletion. An event is "ours"
only if it carries a `sourceEventId` extended property or a legacy `SYNC_ID:`
body marker. This is what makes it safe for staff to have write access to a
target calendar: anything a person adds by hand is invisible to the delete
logic and survives every sync.

That gate now also covers duplicate cleanup, which previously ran on the raw
unfiltered target list. `_build_event_map` (`sync.py:1811`) refuses to condemn
a signature collision when either side fails `_is_synced_event`, checked on
both sides, so a hand-entered event can neither be deleted nor cause the synced
event opposite it to be deleted.

**A signature is not an identity.** `generate_event_signature`
(`signature_utils.py:23`) is subject plus start plus location, and nothing
else. Two different events can share one. Never delete on a signature match
alone. `_build_event_map` compares Graph ids first (`sync.py:1858-1862`) for
exactly this reason.

**Mass deletions abort.** `MAX_RUN_DELETIONS` (`sync.py:36`, default 150,
overridable by environment variable) is a budget for the whole **run**, not per
pair. `_run_deletions_used` (`sync.py:1075`, `sync.py:1406`) accumulates across
pairs, so with N pairs the ceiling is still 150 and not N times 150. When the
budget would be exceeded the pair returns a failure and changes nothing at all,
including its adds. The counter is incremented only on the live path, after the
dry-run return at `sync.py:1391`, so a dry run does not spend the budget.

**Destructive HTTP routes require an explicit, validated target.**
`/clear-target` and `/clear-synced-only` take a JSON body naming the calendar.
There is deliberately no default: a missing target returns 400 rather than
falling back to a calendar the caller did not intend. The name must match a
configured sync target, which makes the master calendar and every unrelated
calendar in the mailbox unreachable from these routes.

**One failing pair does not stop the others.** `_sync_pair()` catches its own
exceptions and returns a failure result. `_do_sync()` aggregates.

---

## 4. Configuration

All configuration is environment variables, read at process start
(`config.py`). Changing one requires a restart or redeploy.

### Identity and access

```bash
CLIENT_ID=...            # Azure AD app registration
CLIENT_SECRET=...
TENANT_ID=...
REDIRECT_URI=https://<host>/auth/callback
SHARED_MAILBOX=calendar@stedward.org
```

Authentication is **delegated** OAuth, not app-only: a human signs in once
through the dashboard and the refresh token is persisted to `/data/token.json`.
If that token is lost or revoked, syncing stops until someone signs in again.

### Calendars

```bash
SOURCE_CALENDAR=Calendar                          # the master, read-only
TARGET_CALENDAR=St. Edward Public Calendar        # primary pair's target
SYNC_CATEGORY=Public                              # primary pair's category
EXTRA_SYNC_PAIRS=SAS=Sundays At St. Edward        # optional, see below
COPY_BODY_TARGETS=Sundays At St. Edward           # optional, see below
```

`EXTRA_SYNC_PAIRS` adds pairs beyond the primary one. Format is
`Category=Calendar Name`; separate multiple pairs with semicolons:

```bash
EXTRA_SYNC_PAIRS=SAS=Sundays At St. Edward;Youth=Youth Ministry Calendar
```

`config.get_sync_pairs()` resolves this into a list, primary pair first, and
applies these guards:

- Whitespace inside a name is collapsed, so a line break introduced by pasting
  into a hosting panel textarea does not break the calendar lookup.
- Calendar names are matched exactly first, then case-insensitively, so a wrong
  capital produces a logged warning rather than a silent no-op.
- A pair targeting `SOURCE_CALENDAR` is dropped.
- Malformed and duplicate entries are ignored.

Leave `EXTRA_SYNC_PAIRS` unset and the service behaves exactly as it did
before multi-pair support existed, including the shape of its result objects.

`COPY_BODY_TARGETS` (`config.py:51`) names the target calendars that keep the
real event description. Anything not named gets a body containing only the sync
marker, which has always been the default. Semicolon-separated, matched
case-insensitively. `Sundays At St. Edward` is opted in because its whole
purpose is publishing what each Sunday holds; its ICS feed is read by the
campaign site. **`TARGET_CALENDAR` cannot be opted in.** Naming the public
calendar here is ignored and logged (`config.py:64-69`). See section 3.

### Behavior

```bash
SYNC_CUTOFF_DAYS=180         # how far back to sync (default 180)
SYNC_LOOKAHEAD_DAYS=365      # how far ahead to sync (default 365)
DRY_RUN_MODE=False           # True plans changes and writes nothing
MASTER_CALENDAR_PROTECTION=true
MAX_RUN_DELETIONS=150        # run-wide deletion budget, see section 3
MAX_SYNC_REQUESTS_PER_HOUR=20
SYNC_OCCURRENCE_EXCEPTIONS=True   # handle cancelled/modified occurrences
OCCURRENCE_SYNC_DAYS=60           # window for the occurrence pass
ALLOWED_DASHBOARD_USERS=     # optional comma-separated allowlist
PORT=5000                    # Render sets this; local default is 5000
LOG_LEVEL=INFO
```

**`SYNC_INTERVAL_MIN` is dead configuration.** It is defined at `config.py:177`
and read by nothing. The scheduler hardcodes the interval at `sync.py:2701`
(`schedule.every(23).minutes`). Setting the variable changes no behavior. Either
wire it up or delete it, but do not trust it as documentation of the interval.

Do not set `DRY_RUN_MODE` and walk away. The sync silently does nothing and
reports itself perfectly healthy while doing it.

---

## 5. Code map

Sizes are a rough guide to where complexity lives. Counted 2026-08-14.

| File | Lines | Responsibility |
|---|---|---|
| `app.py` | 4170 | Flask routes, dashboard, 67 endpoints (most are debug) |
| `sync.py` | 2833 | `SyncEngine`, `SyncScheduler`, `SyncHistory`, `ChangeTracker` |
| `calendar_ops.py` | 1284 | `CalendarReader` and `CalendarWriter` over Microsoft Graph |
| `utils.py` | 852 | Timezone handling, retry with backoff, structured logging |
| `auth.py` | 792 | OAuth flow, token refresh and persistence, request signing |
| `signature_utils.py` | 204 | Event signature generation, the basis of change detection |
| `config.py` | 199 | Environment configuration and pair resolution |
| `clear_target_calendar.py` | 77 | Standalone script, wipes one named target |
| `gunicorn.conf.py` | 33 | One worker, 3600s timeout, 30s graceful shutdown |

`app.py` and `sync.py` are both far past the size where a single file is
comfortable to work in. Splitting them is real, deferred work, not a stylistic
preference. See section 9.

### The sync path, in call order

```
SyncScheduler                     sync.py:2701   every 23 minutes, hardcoded
  └─ SyncEngine.sync_calendars()                 circuit breaker wrapper
       └─ _do_sync()                             rate limit, lock, loop pairs
            ├─ config.get_sync_pairs()           config.py:79
            ├─ _sync_pair(category, target)      once per pair
            │    ├─ reader.find_calendar_id()
            │    ├─ reader.get_public_events(..., category=)   filters
            │    ├─ reader.get_calendar_events(target)
            │    ├─ dedupe_events_by_id()        sync.py:66, both lists
            │    ├─ _build_event_map()           sync.py:1811
            │    ├─ _determine_sync_operations() add / update / delete
            │    ├─ MAX_RUN_DELETIONS check      sync.py:1359
            │    ├─ _execute_sync_operations_batch()
            │    └─ _handle_cancelled_occurrences() / _handle_modified_...()
            └─ _merge_pair_results()             one result for the dashboard
```

### How calendars are read, and why it matters

Both calendars are fetched a week at a time. `generate_weekly_ranges`
(`sync.py:826-831`) yields `(current, current + 7d)` and then advances
`current` by exactly 7 days, so one window's end instant **is** the next
window's start instant. The fetch is a `calendarView` query
(`calendar_ops.py:256`), which returns every event that *overlaps* the window.
An event in progress at a boundary instant therefore comes back from both
adjacent windows, under one Graph id, and the weekly loop concatenates the
results.

`dedupe_events_by_id` (`sync.py:66`) collapses those repeat sightings before
anything keys on signature, at `sync.py:1295-1303` for a live pair and
`sync.py:915-921` for preview. It runs before the "Retrieved N source events
and M target events" log line, so that figure reports distinct events rather
than sightings. Section 10 records what happened when it was missing.

The two fetch methods do not select the same fields, and the difference is
load-bearing:

- `get_calendar_events` (`calendar_ops.py:214`) selects
  `id,subject,body,start,end,categories,showAs,type,seriesMasterId,isCancelled,recurrence,sensitivity,isAllDay,responseStatus,organizer`
  (`calendar_ops.py:261`) and `$expand`s the extended-property namespace
  (`calendar_ops.py:262`). It does **not** select `location`,
  `createdDateTime`, or `lastModifiedDateTime`.
- `get_calendar_instances` (`calendar_ops.py:334`) selects `location`, `body`
  and `lastModifiedDateTime` (`calendar_ops.py:353`) but does **not** `$expand`
  extended properties.

Consequences worth internalizing before editing either `$select`:

- Neither source nor target rows carry a location on the main path, so both
  sides produce an empty location component and their signatures agree.
  Adding `location` to `calendar_ops.py:261` alone would give source rows a
  real `displayName` while target rows still present an empty one, every
  signature would disagree at once, and the public pair would plan roughly
  1,900 adds and 1,900 deletions in one run. That trips `MAX_RUN_DELETIONS`
  and aborts the pair including its adds.
- `createdDateTime` is absent, which is why the old "keep the older event"
  tie-break in `_build_event_map` compared `''` to `''` and decided nothing.
  It is gone, replaced by documented first-seen-wins.
- On the occurrence path, extended properties are absent, so
  `_is_synced_event(inst)` (`sync.py:2512`) can only match on the body marker.
  See section 9.

### Result shapes

A **single** configured pair returns its result unchanged, preserving the
pre-multi-pair shape that the dashboard and history already consume:

```json
{"success": true, "message": "ok", "added": 3, "updated": 1, "deleted": 0,
 "duration": 12.4, "category": "Public",
 "target_calendar": "St. Edward Public Calendar"}
```

**Two or more** pairs return a merged result with a `pairs` list:

```json
{"success": false, "added": 3, "updated": 1, "deleted": 0,
 "error": "SAS -> Sundays At St. Edward: Calendar not found",
 "failed_pairs": ["SAS"],
 "pairs": [
   {"category": "Public", "target_calendar": "St. Edward Public Calendar",
    "success": true, "added": 3, "updated": 1, "deleted": 0},
   {"category": "SAS", "target_calendar": "Sundays At St. Edward",
    "success": false, "error": "Calendar not found"}
 ]}
```

Note `success` is `all(pairs)`. Anything judging overall health should read
the `pairs` list, not the top-level flag, so one failing calendar does not
report as a total outage.

### Change detection

Three mechanisms, because each covers a case the others miss:

- **Signature matching** (`signature_utils.py:23`): normalized subject, start
  time, and location. Detects an event whose content changed. It carries no
  identity, so it can never authorize a deletion by itself.
- **Source ID tracking**: the `sourceEventId` extended property links a copy
  back to its origin. Detects an origin event that was deleted or untagged, so
  its copy can be removed.
- **Body comparison** (`body_comparison_key`, `sync.py:42`): compares the
  visible text a reader would see, after stripping the sync marker and the
  HTML that Outlook rewrites on its own. Only meaningful for calendars in
  `COPY_BODY_TARGETS`, since every other target's body is a fixed marker.

Mechanics worth knowing before touching any of them:

- Copies are stamped twice, and the two stamps are independent. A
  `singleValueExtendedProperties` entry under the GUID namespace
  `{66f5a359-4659-4830-9070-00047ec6ac6e}` carrying `sourceEventId` and
  `lastSynced` is written **unconditionally** at `calendar_ops.py:1009-1022`,
  before and outside the body branch. The body marker
  `<!-- SYNC_ID:... -->` is written separately at `calendar_ops.py:1025`, and
  for a calendar in `COPY_BODY_TARGETS` it is appended to the real description
  rather than replacing it (`calendar_ops.py:1046-1047`).
- **Extended properties are invisible unless you ask for them.**
  `get_calendar_events()` passes `$expand` for that GUID namespace. Drop the
  `$expand` and the properties are simply absent from the response, every copy
  stops looking like ours, and deletion detection fails silently. This is the
  single easiest way to break the sync without any error appearing.
- **A marker-only body appears to read back as an empty string.** Commit
  `0d337dc` recorded a prepared body of 169 characters returning as 0
  characters from Graph. Anything that identifies the sync's own events by
  searching the body for `SYNC_ID:` therefore cannot match on the public
  calendar. `_is_synced_event` is safe because it checks the extended property
  first (`sync.py:1499-1503`); `clear_synced_events_only` and the occurrence
  path are not. See section 9.

`ChangeTracker` caches the last-seen source events at `/data/event_cache.json`.
Its `update_cache()` **replaces** the cache wholesale, so only the primary pair
writes to it; letting a second pair write would erase the first pair's entries
on every cycle.

---

## 6. The dashboard

At `/`, behind Microsoft sign-in. Built for parish staff, not engineers.

- **Status bar**: one sentence on overall health. A single failing calendar
  shows amber and names that calendar, rather than red with no attribution.
- **Calendar cards**: one per *configured* pair, not per pair that happened to
  run. A calendar missing from the last run is flagged rather than absent,
  which is the failure that would otherwise be invisible. Each card shows its
  category, freshness, counts, and on failure the last known good sync time.
- **Preview**: covers the primary pair only, and says so in its heading.
  `preview_sync()` (`sync.py:866`) reads `config.TARGET_CALENDAR` and
  `config.SYNC_CATEGORY` directly and has never been made pair-aware. It does
  set `writer.copy_body` for the primary pair (`sync.py:879`) and it applies
  the same id de-duplication the live path applies, so its plan matches what a
  live run of that pair would do. Making preview cover every pair is unfinished
  work.
- **Advanced Actions**: three gated steps, nothing preselected. Pick a
  calendar, pick a scope, type DELETE. The button label and the final browser
  confirmation both name the calendar.

The footer (`templates/index.html:1041`) and the auto-sync status label
(`templates/index.html:1415`) both say "every 15 minutes". The real interval is
23 minutes (`sync.py:2701`). The strings are wrong, not the scheduler.

---

## 7. Endpoints worth knowing

67 routes exist; most are single-purpose debug endpoints accumulated during
past incidents. These are the ones that matter.

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Dashboard |
| `/health` | GET | Liveness, answers immediately |
| `/ready` | GET | Readiness, checks initialization |
| `/status` | GET | Full status including `sync_pairs` |
| `/sync` | POST | Trigger a sync of every pair |
| `/sync/preview` | POST | Plan changes for the primary pair, write nothing |
| `/sync/progress` | GET | Progress of a running sync |
| `/clear-target` | POST | Delete **all** events from a named target |
| `/clear-synced-only` | POST | Delete only synced events from a named target |
| `/history` | GET | Aggregate statistics |
| `/metrics` | GET | Operational metrics |
| `/debug/verify-config` | GET | Configuration sanity check |
| `/debug/calendars` | GET | List calendars visible in the mailbox |

Most `/debug/*` routes predate multi-pair support and report on the primary
pair only. Treat their output as partial.

---

## 8. Running it

### Locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export CLIENT_ID=... CLIENT_SECRET=... TENANT_ID=... SHARED_MAILBOX=...
export DRY_RUN_MODE=True          # strongly recommended locally
python app.py
```

Then open the app, sign in with an account that can read the shared mailbox,
and trigger a sync from the dashboard.

### Tests

```bash
python -m pytest tests/ -q
```

124 tests across seven files, no network access required. Coverage
concentrates on the parts where a mistake is expensive: signature generation,
duplicate detection and the guards around it, update detection, body-copy
targeting, the auth gate on sensitive endpoints, pair resolution and its
guards, category filtering, multi-pair routing, and the destructive-route
target validation.

A Python 3.10 interpreter fails a handful of these regardless of the code
under test, because 3.10 cannot parse Graph's seven-digit fractional seconds.
Use the project's own Python before treating a failure as real.

### Deployment

Runs on Render, service `srv-d1a16mmmcj7s73f23n90` in workspace
`tea-d1a13aadbo4c73c0plig`, from the private repo
`eharnischStEdword/stedward-calendar-sync`. Render auto-deploys on push to
`main` and also holds the environment variables and the persistent `/data`
disk. Gunicorn runs a single worker, since the scheduler lives in-process and
multiple workers would sync concurrently.

After changing an environment variable, redeploy. The app reads configuration
only at startup.

### Verifying a change actually reached the website

The chain is slow and every link has its own lag. Checking one link and
declaring victory is how a broken deploy looks healthy.

1. **The sync wrote it.** Render logs. Look for `✅ Updated event: <subject>`.
   The SAS pair runs after the Public pair finishes, so give a run time.
2. **Outlook has it.** Open the event on the target calendar in Outlook and
   read the body.
3. **The published feed has it.** Microsoft's publish cache lags the mailbox,
   sometimes by hours. Fetch the ICS URL with a cache-busting query string
   (`?cb=1`) and grep for the `DESCRIPTION:` line.
4. **The website has it.** The ICS Calendar WordPress plugin caches feeds in a
   transient for an hour. Clear it from WP admin, ICS Calendar, Clear Cached
   Calendar Data. The block renders via AJAX, so verify in a real browser.
   Page source will never show the events.

---

## 9. Known gaps

Honest list. None are blocking; all are real. Ordered by what would hurt most.

- **A failed page fetch is reported as success.** `get_calendar_events`
  (`calendar_ops.py:292-294`) does `if not response.ok: break` and returns the
  pages it already collected, so the retry decorator at `calendar_ops.py:213`
  never sees the failure. One transient 503 on any window makes the events it
  would have returned look orphaned to `_identify_events_to_delete`, which
  plans them for deletion. `MAX_RUN_DELETIONS` is the only backstop. This is
  the highest-value follow-up in this list and it deserves its own change,
  because it alters error handling on every fetch in the service.
- **`clear_synced_events_only` cannot match anything on the public calendar.**
  It finds the sync's own events by searching the body for `SYNC_ID:`
  (`calendar_ops.py:1076`), and a marker-only body appears to read back empty.
  It fails closed, deleting nothing, so the SAFE clear option silently does
  less than the destructive one. Flagged in commit `0d337dc`, still true.
- **The occurrence pass cannot identify public-calendar events either.**
  `get_calendar_instances` does not `$expand` extended properties
  (`calendar_ops.py:353`), so `_is_synced_event(inst)` at `sync.py:2512` falls
  through to the body marker and returns False for every public copy. Also
  fails closed: cancelled and missing occurrences are simply never cleaned up
  on that calendar.
- **`SYNC_INTERVAL_MIN` is dead configuration.** Defined at `config.py:177`,
  read by nothing, while `sync.py:2701` hardcodes 23 minutes.
- **The dashboard says 15 minutes.** `templates/index.html:1041` and
  `templates/index.html:1415`.
- **`preview_sync()` covers the primary pair only**, while executing a sync
  writes to every pair. The preview heading says so, but the mismatch remains.
- **Two genuinely distinct events with the same subject, start and location
  collapse to one.** `location` is not in the main `$select`, so the signature
  cannot separate two rooms at the same hour, and `source_signatures_seen`
  (`sync.py:2074-2091`) skips the second. Harmless for deletion now that the
  id and synced guards are in place, but only one of the two is created. Fixing
  it means adding `location` to both sides at once, never one.
- `/bulletin-events` and `/event-search` read the primary target calendar only,
  so a second calendar's events do not appear in either.
- `/admin/migrate-extended-properties` migrates the primary pair only, and has
  never been run to completion.
- Sync history stores per-pair detail inside each entry, but `/history` exposes
  aggregates only, so per-calendar history is not visible in the UI.
- `/debug/current-sync-status` references `config.PUBLIC_CALENDAR`
  (`app.py:2248`), which does not exist in `config.py`, and always returns 500.
- Both destructive routes are reachable by any authenticated dashboard user;
  the typed DELETE confirmation is a client-side gate only.
- `app.py` (4170 lines) and `sync.py` (2833 lines) are past the size where a
  single file is workable. Splitting them is deferred, not dismissed.

---

## 10. Incident record: the 23-minute churn

Kept because the failure was invisible for a long time and the shape of it will
repeat if the fetch layer changes.

**Symptom.** On 2026-08-14 the Public pair's Render logs read: created 38 and
deleted 33, then created 33 and deleted 64, then created 64 and deleted 13.
Every run's deleted count became the next run's created count, exactly, with no
drift. For roughly half of every 46-minute window the affected events were
missing from the live parish calendar and its published ICS feed. The titles
were the long ones: Mass- Vigil, RITI, Room in the Inn. A read-only preview
reported "Retrieved 1951 source events and 1952 target events" and "39
duplicate target events to clean up".

**Cause.** Adjacent weekly fetch windows share their boundary instant
(`sync.py:826-831`) and the fetch is a `calendarView` overlap query
(`calendar_ops.py:256`), so an event in progress at a boundary was returned by
both windows under one Graph id, and the weekly loop concatenated it twice with
no de-duplication. `_build_event_map` keyed purely on signature and never
compared ids, so the second sighting was condemned as a duplicate target
carrying the surviving event's own id. The sync deleted the only real copy, and
the next run recreated it.

**Why it looked like a body-marker problem and was not.** The obvious
hypothesis was that the SYNC_ID marker does not survive the round trip on the
public calendar, which is true, and that this broke identity matching, which is
false. `sourceEventId` is written unconditionally at
`calendar_ops.py:1009-1022`, outside the body branch entirely, `$expand`ed on
every read at `calendar_ops.py:262`, and checked first at `sync.py:1499`. The
old `_build_event_map` read no body at all. The marker question is real (see
section 9) but it is not this.

**Why SAS was clean and Public was not.** Not `COPY_BODY_TARGETS`. Both pairs
run through the same `_sync_pair`, the same `_build_event_map`, the same weekly
loop and the same `calendarView` call, and `config.copies_body` feeds only body
handling, never an add, update or delete decision. SAS carries roughly 41 short
Sunday events against 77 fixed boundary instants, so almost nothing was in
progress at a boundary. Public carries roughly 1,900 events including all-day
and overnight ones. SAS was statistically quiet, not structurally immune. Put a
multi-day retreat on it under the old code and it would have churned the same
way, quietly, because one or two deletions per run never trips
`MAX_RUN_DELETIONS`.

**Fix.** Four changes, all in `sync.py`. `dedupe_events_by_id` (`sync.py:66`)
applied to both fetched lists in `_sync_pair` (`sync.py:1295-1303`) and
`preview_sync` (`sync.py:915-921`). `_build_event_map` (`sync.py:1811`) refuses
to condemn a row whose Graph id matches the retained row's, and refuses to
condemn any collision where either side fails `_is_synced_event`. The dead
`createdDateTime` tie-break is gone. The merged `to_delete` is de-duplicated by
id at both call sites (`sync.py:1343-1355`, `sync.py:934-945`). Duplicate
cleanup itself was **kept**: a genuine second copy under its own id, carrying
this sync's identity, is still removed.

**What the first live run should look like.** Every one of those changes is a
suppressor, so the plan can only be identical or smaller than before and
deletions can only go down. Expect the target count to drop once, from roughly
1952 to roughly 1913, with a new `Collapsed weekly-window repeats` line
reporting it. That is the double-count disappearing, not data loss. Two
consecutive dry runs of the Public pair should log `0 to add, 0 to update, 0 to
delete`, the `Found N duplicate target events` line should not appear, and the
retrieved figures should stop moving run to run. Check SAS in the same two runs
as a requirement, not a courtesy.

**Watch item.** `sync.py:1914` logs the synced-target count per pair. If that
ever reads 0 for the Public pair, the extended property is not coming back on
reads and duplicate cleanup there has silently become a no-op.

### Earlier in the same chain, for context

Three bugs stacked before this one, all fixed and deployed:

- `_needs_update` never returned True for any event. It compared
  `lastModifiedDateTime` on both sides, a field absent from the `$select`, so
  both were `None`, `None == None` was True, and it bailed before comparing
  content. Commit `b711a10` requires both timestamps present before trusting
  them. Do not add `lastModifiedDateTime` to the `$select` and revert that
  guard: the target's timestamp records when the sync wrote it, not when a
  person edited the source, so the two are not comparable even when both exist.
- With detection finally working, every event looked time-changed and the
  first run queued 1,890 updates. Graph returns seven fractional digits on
  timestamps and `_prepare_event_for_api` strips them before writing, so the
  comparison was `14:15:00` against `14:15:00.0000000` on every event in
  existence. Commit `4b059ce` compares in the same normalized form the writer
  sends.
- What remained then reported a body change on every public event, comparing a
  169-character marker against `''`. Commit `0d337dc` compares the visible text
  a reader sees, with the marker and Outlook's own HTML rewrites removed from
  both sides. Tradeoff accepted: an edit that changes only formatting no longer
  counts as a change.

---

## 11. Troubleshooting

**A calendar's card says "Not included in the last sync."** The pair is
configured but the run skipped it. Check the logs for `Calendar '<name>' not
found`, which lists the calendar names actually present in the mailbox.

**Tagged events are not appearing.** Confirm all four qualifying conditions in
section 2. The most common cause is `showAs` set to `free`, which is skipped by
design. The second most common is the event falling outside the sync window.

**The category looks right in Outlook but the event still will not sync.**
The desktop calendar app can show a category ticked while Graph returns no
categories at all for that event, so believe the API over the UI and check a
debug endpoint before hunting anything else. The fix that reliably works is to
re-apply the category in Outlook on the web: open the event, click Categorize,
untick the category and save, then tick it again and save. The next sync picks
it up.

**Everything stopped syncing.** The delegated OAuth token likely expired or was
revoked. Sign in again through the dashboard.

**An event keeps reappearing after deletion.** It was deleted from the target
but still qualifies on the master, so the next sync recreates it. Remove the
category on the master instead.

**Events vanish and come back on a cycle.** Read section 10 first. If the
deleted count of one run equals the created count of the next, something is
condemning live events as duplicates again. Check whether a change to the
fetch layer reintroduced repeat sightings, and check the
`Collapsed weekly-window repeats` and `Ignored N repeat sighting(s)` log lines.

**Duplicates appeared.** Signature matching failed, usually because a subject,
start time or location changed in a way that broke the match.
`/debug/duplicates` reports them; the sync also cleans up genuine duplicates it
detects on the target, meaning separate events under their own Graph ids that
this sync created.

**A description is not reaching the campaign site.** Walk the four links in
section 8 in order. Most often it is the ICS Calendar transient or Microsoft's
publish cache, not the sync.

---

## License

Proprietary. See `LICENSE.txt`.

Copyright (c) 2024-2026 Harnisch LLC. Licensed for use by St. Edward Church &
School, Nashville, TN.
