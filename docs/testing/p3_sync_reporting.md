# P3 sync reporting verification

Beads `c7ll3`, `zcnl5`, `cruhd`; candidate based on protected `dev` `0bc12600`.

## What changed

- An apply with no computed outcome uses the existing `unknown` task/journal
  fallback, never `dry_run`. `sync_report.outcome` remains null; no enum member,
  fabricated realized/rollback result or successful-apply stamp is introduced.
  The existing indeterminate-outcome helper was inspected but remains for realized
  runs. Destination-unreadable errors still use their established error code.
- Matched stream rows are reused, not updated. The stream category records a
  reasoned `existing_stream_reused` skip without asserting identical fields;
  actual placeholder creation, matching,
  fuzzy warnings, rollback ownership and channel-attachment writes are unchanged.
  No stream-row write was found behind the old match-only increment.
- Preview totals include predicted categories only. Every `predicted=false`
  category and its existing caveat appears in the ordinary summary message, which
  the Sync Targets card already displays. Apply counts and real conflict counts
  are not filtered by preview flags. No new stream predictor is introduced.
  A preview with no predicted categories says so rather than offering zero-valued
  predictions. The additive reuse skip reason has a typed frontend label; fuzzy
  matches are not described as identical.

## Evidence and layers

`test_p3_sync_reporting.py` demonstrated four initial failures before repair with
two positive controls, then additional red/green checks for an all-unpredicted
preview and an explicit reuse reason. It exercises the real mapper and matcher against
the existing recorded, sanitized Dispatcharr stream response, including repeated
reuse and failed channel attachment. It verifies that no stream create/update is
issued and no fixture row is changed. No new third-party field vocabulary or
matcher/filter policy is assumed.

`test_p3_sync_history.py` crosses the real TaskEngine, private SQLite execution
record and history HTTP serialization. The ordinary message and mode survive
persistence; it is not merely a helper-presence test.

`e2e/sync-reporting.spec.ts` uses the existing backend-less verification fixture:
the built app, real frontend API client, Settings card and notification rendering
consume golden TaskResult messages produced by the Python mapper. Screenshots
show the preview's unpredicted-stream caveat without opening JSON, and an aborted
apply as an error rather than a preview. All external API/WebSocket traffic is
intercepted/denied; the test does not contact a live destination. Run with:

```sh
npx playwright test --config=playwright.verification.config.ts e2e/sync-reporting.spec.ts
```

## Sibling survey and explicit limits

An additional local stateful A/B harness probe ran the production sync engine and
importers, then compared destination state on cycles 2 and 3. Stream rows stayed
identical, with zero created/updated and one reuse each cycle. This is a stateful
test dependency, not a real Dispatcharr service.

The required sibling survey also found core settings still report six successful
assignments with unchanged values in that probe. Those are actual no-op update
calls, unlike the stream match-only counter. They remain under **`y5hvo`**, not
silently labelled fixed. Other importer update increments were inspected:
M3U accounts count after a successful patch with detected drift; core settings
count after a successful update call; ECM settings count after validated save.
This does not prove all categories report zero updates on every converged input.

No disposable Dispatcharr-B was running during verification; production services
were neither repointed nor mutated. Live two-instance acceptance remains pending.
Historical records are not rewritten. The four P3 items remain candidates pending
review, protected delivery and appropriate acceptance; existing security/image
blockers are not waived by local tests or browser replay.
