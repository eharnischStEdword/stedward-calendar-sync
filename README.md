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
     ┌───────────────────────┐     │    1. read master, keep events whose
     │  SyncEngine._do_sync  │     │       category matches AND showAs is busy
     │  loops the pairs      │     │       AND not cancelled
     └───────────────────────┘     │    2. read the target calendar
          │            │           │    3. compare by signature + sourceEventId
          ▼            ▼           │    4. add / update / delete
  ┌──────────────┐ ┌────────────┐  │
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
drops any pair whose target is `SOURCE_CALENDAR`, so a configuration typo
cannot aim writes at the master.

**Deletions only ever touch events this service created.**
`_is_synced_event()` gates every deletion. An event is "ours" only if it
carries a `sourceEventId` extended property or a legacy `SYNC_ID:` body
marker. This is what makes it safe for staff to have write access to a target
calendar: anything a person adds by hand is invisible to the delete logic and
survives every sync.

**Mass deletions abort.** If a single pair plans more than
`MAX_DELETIONS_WITHOUT_APPROVAL` (currently 150, in `sync.py`) deletions, the
sync stops and changes nothing. This is per pair, so with N pairs the
effective ceiling is N times that number.

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

### Behavior

```bash
SYNC_INTERVAL_MIN=23         # minutes between automatic syncs
SYNC_CUTOFF_DAYS=1825        # how far back to sync
SYNC_LOOKAHEAD_DAYS=365      # how far ahead to sync
DRY_RUN_MODE=False           # True plans changes and writes nothing
MASTER_CALENDAR_PROTECTION=true
MAX_SYNC_REQUESTS_PER_HOUR=20
ALLOWED_DASHBOARD_USERS=     # optional comma-separated allowlist
PORT=10000
```

---

## 5. Code map

Eight core modules. Sizes are a rough guide to where complexity lives.

| File | Lines | Responsibility |
|---|---|---|
| `app.py` | ~4000 | Flask routes, dashboard, ~67 endpoints (most are debug) |
| `sync.py` | ~2500 | `SyncEngine`, `SyncScheduler`, `SyncHistory`, `ChangeTracker` |
| `calendar_ops.py` | ~1250 | `CalendarReader` and `CalendarWriter` over Microsoft Graph |
| `auth.py` | ~800 | OAuth flow, token refresh and persistence, request signing |
| `utils.py` | ~850 | Timezone handling, retry with backoff, structured logging |
| `signature_utils.py` | ~200 | Event signature generation, the basis of change detection |
| `config.py` | ~120 | Environment configuration and pair resolution |
| `gunicorn.conf.py` | ~35 | One worker, 3600s timeout, 30s graceful shutdown |

### The sync path, in call order

```
SyncScheduler                     sync.py    every SYNC_INTERVAL_MIN
  └─ SyncEngine.sync_calendars()             circuit breaker wrapper
       └─ _do_sync()                         rate limit, lock, loop pairs
            ├─ config.get_sync_pairs()       config.py
            ├─ _sync_pair(category, target)  once per pair
            │    ├─ reader.find_calendar_id()
            │    ├─ reader.get_public_events(..., category=)   filters
            │    ├─ reader.get_calendar_events(target)
            │    ├─ _determine_sync_operations()   add / update / delete
            │    ├─ _execute_sync_operations_batch()
            │    └─ _handle_cancelled_occurrences() / _handle_modified_...()
            └─ _merge_pair_results()         one result for the dashboard
```

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

Two independent mechanisms, because each covers a case the other misses:

- **Signature matching** (`signature_utils.py`): normalized subject plus start
  time. Detects an event whose content changed.
- **Source ID tracking**: the `sourceEventId` extended property links a copy
  back to its origin. Detects an origin event that was deleted or untagged, so
  its copy can be removed.

Mechanics worth knowing before touching either:

- Copies are stamped twice. A `singleValueExtendedProperties` entry under the
  GUID namespace `{66f5a359-4659-4830-9070-00047ec6ac6e}` carries
  `sourceEventId` and `lastSynced`, and the event body is set to contain only
  an HTML comment marker, `<!-- SYNC_ID:... -->`. The body carries no event
  description, deliberately, so nothing private leaks to a public calendar.
- **Extended properties are invisible unless you ask for them.**
  `get_calendar_events()` passes `$expand` for that GUID namespace. Drop the
  `$expand` and the properties are simply absent from the response, every copy
  stops looking like ours, and deletion detection fails silently. This is the
  single easiest way to break the sync without any error appearing.
- The `SYNC_ID:` body marker is still load-bearing, not legacy trivia. Queries
  that do not expand extended properties, such as the occurrence handling, rely
  on it. `/admin/migrate-extended-properties` was written to retire it and has
  never been run to completion.

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
  `preview_sync()` has not been made pair-aware; making preview cover every
  pair is unfinished work.
- **Advanced Actions**: three gated steps, nothing preselected. Pick a
  calendar, pick a scope, type DELETE. The button label and the final browser
  confirmation both name the calendar.

---

## 7. Endpoints worth knowing

Roughly 67 routes exist; most are single-purpose debug endpoints accumulated
during past incidents. These are the ones that matter.

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

70 tests, no network access required. Coverage concentrates on the parts where
a mistake is expensive: signature generation, duplicate detection, the auth
gate on sensitive endpoints, pair resolution and its guards, category
filtering, multi-pair routing, and the destructive-route target validation.

### Deployment

Runs on Render. Deploys are triggered from the Render dashboard, which also
holds the environment variables and the persistent `/data` disk. Gunicorn runs
a single worker, since the scheduler lives in-process and multiple workers
would sync concurrently.

After changing an environment variable, redeploy. The app reads configuration
only at startup.

---

## 9. Known gaps

Honest list. None are blocking; all are real.

- `preview_sync()` covers the primary pair only, while executing a sync writes
  to every pair. The preview heading says so, but the mismatch remains.
- `/bulletin-events` and `/event-search` read the primary target calendar only,
  so a second calendar's events do not appear in either.
- `/admin/migrate-extended-properties` migrates the primary pair only.
- Sync history stores per-pair detail inside each entry, but `/history` exposes
  aggregates only, so per-calendar history is not visible in the UI.
- `/debug/current-sync-status` references a `config.PUBLIC_CALENDAR` that does
  not exist and always returns 500.
- The footer and the auto-sync label say "every 15 minutes" while the interval
  defaults to 23.
- `MAX_DELETIONS_WITHOUT_APPROVAL` is 150 with a note to restore it to 50 after
  a duplicate cleanup that has since finished.
- Both destructive routes are reachable by any authenticated dashboard user;
  the typed DELETE confirmation is a client-side gate only.

---

## 10. Troubleshooting

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

**Duplicates appeared.** Signature matching failed, usually because a subject
or start time changed in a way that broke the match. `/debug/duplicates`
reports them; the sync also cleans up duplicates it detects on the target.

---

## License

Proprietary. See `LICENSE.txt`.

Copyright (c) 2024-2026 Harnisch LLC. Licensed for use by St. Edward Church &
School, Nashville, TN.
