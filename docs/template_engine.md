# Dummy EPG Template Engine

Shared template engine used by the dummy EPG system to render channel titles,
descriptions, and URLs from regex groups. The Python engine lives in
`backend/template_engine.py` and is mirrored one-for-one by
`frontend/src/utils/templateEngine.ts` so the in-browser live preview and the
server-side XMLTV output are always byte-identical.

> **Supported surface: Dummy EPG Profiles.** This engine backs the **Dummy EPG
> Profiles** section of the EPG Manager tab (the `dummy_epg_profiles` table and
> the `/api/dummy-epg/*` endpoints) — the supported way to author dummy EPG in
> ECM, and the one Event Sync integrates with. A second, older surface exists:
> Dispatcharr-native EPG sources of `source_type=dummy` (created via the legacy
> "Dummy EPG Sources" section) also store the same fields in `custom_properties`.
> That legacy path is **deprecated** (bead 09x38.4): its section only appears
> when such sources already exist, and it no longer offers new-creation.
> Existing legacy sources are grandfathered (still editable); nothing is
> deleted. New dummy EPG should be authored as a Dummy EPG Profile.

## Syntax

### Placeholders

- `{name}` — insert the value of the named regex group (or an empty string if
  it's absent).
- `{name_normalize}` — legacy shortcut preserved from the pre-v0.14 engine:
  lowercase the value and strip everything that isn't `a-z` or `0-9`.

### Pipes

Chain left-to-right with `|`; each pipe receives the previous pipe's output.

| Pipe | Effect |
|-|-|
| `uppercase` | `str.upper()` |
| `lowercase` | `str.lower()` |
| `titlecase` | Title-case (first letter of each word) |
| `trim` | Strip leading & trailing whitespace |
| `strip:<chars>` | Strip any of `<chars>` from both ends |
| `replace:<from>:<to>` | Replace every occurrence (`to` may be empty) |
| `normalize` | Same as the `_normalize` suffix |
| `lookup:<table>` | Resolve the value through a table; miss → passthrough |

### Conditionals

Content inside `{if:...}...{/if}` renders only when the condition is true.
Conditionals may nest; no `{else}` branch.

| Form | Evaluates true when… |
|-|-|
| `{if:group}…{/if}` | Group value is non-empty |
| `{if:group=value}…{/if}` | Group value equals `value` exactly |
| `{if:group~regex}…{/if}` | Regex matches the group value |

Invalid regex inside a conditional evaluates to **false** (the engine never
throws from a typo). Oversized regex (> 500 chars) also evaluates to false,
which prevents catastrophic backtracking on untrusted input.

### Lookup tables

Two sources resolve at render time, merged with **inline overrides global**:

- **Inline** — `inline_lookups` on the dummy EPG source's custom_properties,
  or equivalent field on the `POST /api/dummy-epg/preview` request.
- **Global** — saved tables managed under *Settings → Lookup Tables*, attached
  to a source by ID via `global_lookup_ids`.

Referencing a table that doesn't exist raises `TemplateSyntaxError`. The
higher-level `render_template()` wrapper in `dummy_epg_engine.py` catches this
and falls back to the raw template text so a single profile typo can't tank
an XMLTV refresh — the broken tokens become visible in the output, which is
the intended signal to the user.

## Limits

| Limit | Value | Behavior on violation |
|-|-|-|
| Template length | 4096 chars | `TemplateSyntaxError` |
| Group value length | 1024 chars | Silently truncated before any transform or regex |
| Conditional regex length | 500 chars | Conditional evaluates false |

## Example

```
{league|uppercase}: {if:team}{team|titlecase}{/if}
```

With groups `league=nfl, team=chiefs` → `NFL: Chiefs`.
With `team` absent → `NFL: `.

With `team=chiefs` and a global lookup table `teams={chiefs: "Kansas City Chiefs"}`:

```
{league|uppercase}: {team|lookup:teams}
```

→ `NFL: Kansas City Chiefs`.

## Trace mode

Both engines expose a trace-producing variant used by the enhanced preview UI:

- Python: `TemplateEngine.render_with_trace(template, groups, lookups) -> (str, list[dict])`
- TypeScript: `new TemplateEngine().renderWithTrace(template, groups, lookups) -> { output, trace }`

A `trace` is a list of `TraceStep` entries:

```json
[
  {"kind": "literal", "text": "Go "},
  {
    "kind": "placeholder",
    "raw": "{team|titlecase}",
    "group_name": "team",
    "initial_value": "chiefs",
    "pipes": [
      {"transform": "titlecase", "arg": null, "input": "chiefs", "output": "Chiefs"}
    ],
    "final_value": "Chiefs"
  },
  {
    "kind": "conditional",
    "condition": "season=2026",
    "kind_detail": "equality",
    "taken": false,
    "value": "2025",
    "body": []
  }
]
```

Lookup pipes additionally carry `{source: <table>, matched: bool}`. The trace
preserves order, so rendering the `output` strings concatenated from each
step reproduces the final output exactly.

## Related files

- `backend/template_engine.py`, `backend/tests/unit/test_template_engine.py`
- `backend/dummy_epg_engine.py` — calls `render_template()` from the engine
- `backend/routers/dummy_epg.py` — `/preview`, `/preview/batch`, `include_trace`
- `backend/routers/lookup_tables.py` — CRUD for global tables
- `frontend/src/utils/templateEngine.ts`, `frontend/src/utils/templateEngine.test.ts`
- `frontend/src/components/TemplateHelp.tsx` — in-app syntax reference
- `frontend/src/components/settings/LookupTableSection.tsx` — global table management UI
- `frontend/src/components/DummyEPGSourceModal.tsx` — inline tables + global attachment + preview UI
