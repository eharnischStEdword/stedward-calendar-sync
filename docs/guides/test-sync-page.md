# The Test Fix page

A web page for working out why a specific event is or is not syncing, without
using the command line. Reachable from the dashboard as **Test Fix**, or
directly at `/test-sync`.

## Two caveats before you trust it

- **It reasons about one category only.** `/debug/event/<id>` hardcodes
  `Public`, so a correctly tagged `SAS` event is reported as "Would Sync: NO".
  For any category other than the primary one, this page is wrong.
- **Event search covers the next 90 days only.** An empty result is usually a
  date range miss, not a missing event.

## Workflow

1. **Search for the event.** Type part of the subject. The page lists matches
   with their id, start time, categories, and `showAs` value.
2. **Take the event id.** Use the "Use This Event ID" button rather than
   copying by hand; Graph ids are long and easy to truncate.
3. **Analyze.** The page reports each condition the sync checks and whether the
   event passes it.
4. **Fix and re-check.** Change the event in Outlook, then run the analysis
   again. There is no need to wait for a scheduled sync to see whether the
   event now qualifies.

## Reading the verdict

**Would Sync: YES.** The event qualifies and will be copied on the next run.

**Would Sync: NO**, with:

- **Has Public: NO.** The category is missing or misspelled. Add it in Outlook
  on the web. If the desktop app shows the category as already set, see the
  category re-apply fix in the main README troubleshooting section: the desktop
  UI and Graph do disagree.
- **Is Busy: NO.** The event is marked Free. Change it to Busy or Tentative.

## When this page is not the right tool

It answers "why this one event". For "why is the whole calendar behaving
oddly", use `/debug/verify-config` for configuration and the dashboard's
calendar cards for per calendar state.
