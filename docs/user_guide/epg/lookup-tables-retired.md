# Lookup Tables retired — export before you upgrade

> **Audience:** Operators upgrading across the release that removes Lookup
> Tables, who have ever visited **Settings → Lookup Tables**.
>
> **TL;DR:** If that page has any rows, **export them before you upgrade.** The
> upgrade drops the `lookup_tables` database table and the rows are not
> restored by rolling the migration back. Your generated EPG output does **not**
> change — the `{key|lookup:<table>}` pipe never worked in generated XMLTV in
> the first place.

## Does this affect me?

Ask the running instance, **before** you upgrade:

```bash
curl -s http://YOUR-ECM-HOST:6100/api/lookup-tables
```

- `[]` — nothing to do. Upgrade normally.
- Anything else — you have tables. **Export them now**, then upgrade.

Or open **Settings → Lookup Tables** and look at the list.

## Export your tables (do this first)

The list endpoint returns summaries without entries, so fetch each table by id
to get its `entries` dict:

```bash
HOST=http://YOUR-ECM-HOST:6100
curl -s "$HOST/api/lookup-tables" -o lookup-tables-index.json
for id in $(python3 -c "import json;print(*[t['id'] for t in json.load(open('lookup-tables-index.json'))])"); do
  curl -s "$HOST/api/lookup-tables/$id" -o "lookup-table-$id.json"
done
```

If authentication is enabled, add your session cookie or bearer token the same
way you would for any other ECM API call.

There is no import path back into ECM — the feature is gone. Keep the JSON as a
record of your key→value mappings so you can fold them into your templates by
hand (see [What to do instead](#what-to-do-instead)).

## Safety net if you forget

The migration is not silent. Before dropping the table it writes every row it
is about to delete to a JSON file **beside your database**:

```
/config/lookup_tables_dropped_0041.json
```

and logs the path at `WARNING` in the container log:

```
docker logs ecm-ecm-1 2>&1 | grep '\[0041\]'
```

Two caveats: nothing in ECM reads that file (copy it somewhere you keep), and
the dump is best-effort — if `/config` is read-only or full, the log line says
the dump failed and the rows were deleted anyway. It is a safety net, not a
backup. Your pre-upgrade export or your DBAS/ZIP backup is the backup.

## What actually changes

| Surface | Before | After |
|---|---|---|
| **Settings → Lookup Tables** | A Settings destination under Channel Processing | Gone. A bookmarked `#settings/lookup-tables` URL lands on **Settings → General** |
| `{key\|lookup:<table>}` in a template | Resolved in **preview** only | Unknown transform — the field renders the raw template text |
| Generated XMLTV for such a template | Already rendered the raw template text | **Unchanged** |
| `/api/lookup-tables` (5 endpoints) | Available | Removed (`404`) |
| `inline_lookups` / `global_lookup_ids` on `POST /api/dummy-epg/preview` | Merged into the render | Ignored (a stale cached browser tab keeps working, it just stops resolving) |
| `lookup_tables` database table | Present | Dropped (Alembic revision `0041`) |

## Why it was removed rather than fixed

The pipe only ever worked in **preview**. Two code paths resolved lookup
tables — `POST /api/dummy-epg/preview` and `POST /api/dummy-epg/preview/batch`
— and neither is the path that produces your actual EPG. XMLTV generation
called the render helper without a lookups dict, so the engine raised
"unknown lookup table", the surrounding fallback swallowed it, and the **raw
template text was emitted verbatim** as the programme title or description:

```xml
<title>{away|lookup:teams} at {home}</title>
```

So an operator who used the pipe saw a correct preview and garbage in the
guide. Keeping the pipe would have meant documenting that trap permanently;
removing it makes preview agree with what is actually generated. That is the
one behaviour change you may notice: a template using the pipe now previews
the same raw text your EPG was already showing.

## What to do instead

The mappings a lookup table held are almost always expressible with the pipes
that remain, none of which were touched:

- `{name|replace:<from>:<to>}` — a small fixed substitution, chainable.
- **Substitution pairs** on the profile — applied to the source name before
  pattern matching, and the right place for provider-name cleanup.
- **Pattern variants** — when different name shapes need different templates.
- [Channel Normalization](../normalization/index.md) — for renaming at the
  channel level rather than in the EPG template.

Full remaining syntax: [Dummy EPG Template
Engine](../../template_engine.md).

## Rolling back

`alembic downgrade 0040` recreates the `lookup_tables` table and its index, so
the schema is fully reversible — but the table comes back **empty**. There is
no down migration that restores the rows. To get the data back you need the
JSON dump above, your pre-upgrade export, or a backup taken before the
upgrade.

Reference: bead `enhancedchannelmanager-70u0r.1`, PO decision D2 on epic
`enhancedchannelmanager-70u0r`.
