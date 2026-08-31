# Smart Sort v0.18.2 Release Readiness

**Bead:** `enhancedchannelmanager-npueh.5`

**Tested code candidate:** `d40d915eacb5190aa6d59f4194f7e86255003d56`

This record maps automated evidence to the Smart Sort consumers included in
v0.18.2. It does not record or imply manual verification.

## Consumer matrix

| Consumer or compatibility path | Behavior covered | Automated evidence |
| --- | --- | --- |
| Manual ordering | The browser sends the current channel/stream sequence to `POST /api/stream-stats/compute-sort`, receives the Points order, and renders that order in Channel Manager Edit Mode. | `e2e/smart-sort-points.spec.ts` |
| Probe-completion reorder | Reordering after a completed probe uses the configured `priority` or `points` strategy and writes the computed stream order. Priority audit metadata omits disabled health categories. | `backend/tests/unit/test_stream_prober_bulk.py::test_probe_completion_reorder_uses_configured_smart_sort`; `backend/tests/unit/test_stream_prober_bulk.py::test_bulk_priority_reorder_does_not_report_disabled_black_screen_bucket` |
| Scheduled probe | The scheduled `StreamProbeTask` retains the resolved strategy/rules and applies the resulting order. Points audit metadata does not claim an unhealthy winning stream was deprioritized. | `backend/tests/unit/test_stream_probe_task_smart_sort.py::test_scheduled_probe_uses_resolved_smart_sort_configuration`; `backend/tests/unit/test_stream_probe_task_smart_sort.py::test_scheduled_points_reorder_does_not_claim_unhealthy_stream_was_deprioritized` |
| Channel Pipeline | Smart Sort normalizes the same sort and health facts as the prober, supports Points health tradeoffs, and reports audit metadata consistent with the active strategy and health toggles. | `backend/tests/unit/test_channel_pipeline_engine.py::TestPointsSmartSortPipeline::test_consumer_families_normalize_every_sort_and_health_fact_identically`; `backend/tests/unit/test_channel_pipeline_engine.py::TestPointsSmartSortPipeline::test_points_can_rank_failed_black_screen_low_fps_stream_first`; `backend/tests/unit/test_channel_pipeline_engine.py::TestPointsSmartSortPipeline::test_points_execution_log_does_not_claim_unhealthy_winner_was_deprioritized`; `backend/tests/unit/test_channel_pipeline_engine.py::TestPointsSmartSortPipeline::test_priority_execution_log_omits_disabled_black_screen_bucket` |
| Event Sync | An Event Sync rule using `smart_sort` heals an already-attached stream order under both Priority and Points, including a steady-state run with nothing new to attach. | `backend/tests/unit/test_event_sync_attach_execution.py::TestEventSyncStreamSortBehavior::test_smart_sort_heals_already_attached_no_op_run`; `backend/tests/unit/test_channel_pipeline_engine.py::TestEventSyncStreamReorderWiring::test_smart_sort_no_op_runs_through_process_streams` |
| Legacy Priority mode | Existing criterion order, codec aliases, health buckets/toggles, missing probe values, and ascending-ID final ties remain characterized. Legacy settings files resolve to Priority with no Points rules. | `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_criteria_fall_through_in_configured_order`; `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_preserves_current_codec_order_and_aliases`; `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_uses_health_buckets_before_criteria`; `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_final_ties_use_ascending_stream_id`; `backend/tests/unit/test_smart_sort_point_settings.py::test_legacy_settings_file_resolves_smart_sort_defaults` |
| Direct one-field sort modes | `resolution`, `bitrate`, `framerate`, `video_codec`, `m3u_priority`, `audio_channels`, `custom_streams`, and `catchup` remain Priority-mode requests rather than inheriting the global Points strategy. Endpoint tests exercise resolution, bitrate, framerate, M3U priority, and audio channels; shared evaluator tests cover codec, custom-stream, and catch-up ordering. | `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_mode_resolution`; `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_mode_bitrate`; `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_mode_framerate`; `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_mode_m3u_priority`; `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_mode_audio_channels`; `backend/tests/unit/test_compute_sort_endpoint.py::test_compute_sort_smart_points_can_override_health_buckets`; `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_orders_each_normalized_criterion_descending`; `backend/tests/unit/test_smart_sort_evaluator.py::test_priority_mode_preserves_current_codec_order_and_aliases` |
| Cached-client settings writes | Omitting `stream_sort_strategy` preserves the stored strategy; omitting `stream_sort_point_rules` preserves stored rules. Explicit `null` is rejected with an actionable `422`, while an explicit empty rule list clears the rules. | `backend/tests/routers/test_smart_sort_point_settings.py::test_omitted_stream_sort_strategy_preserves_stored_value`; `backend/tests/routers/test_smart_sort_point_settings.py::test_omitted_stream_sort_point_rules_preserves_stored_value`; `backend/tests/routers/test_smart_sort_point_settings.py::test_explicit_null_smart_sort_setting_returns_actionable_422`; `backend/tests/routers/test_smart_sort_point_settings.py::test_explicit_empty_point_rules_are_valid` |
| Settings persistence and mode switching | Points rules persist through real settings GET/POST and reload. Priority settings and Points rules survive Priority/Points round trips, and the resulting real compute-sort order is rendered by manual sorting. | `e2e/smart-sort-points.spec.ts`; `frontend/src/components/settings/SmartSortPointsStrategy.test.tsx` tests `defaults omitted strategy settings to the unchanged Priority editor` and `adds, edits, deletes, reorders, saves, and reloads rules without changing rule contents` |

## Browser E2E evidence

The exact-build Chromium run used temporary settings and database state on
non-production ports. Before any write, it verified a per-run nonce through
both direct backend health and the frontend proxy. Its critical settings and
compute-sort requests exercised the production routers and Smart Sort logic.
Dispatcharr inventory, authentication/bootstrap, health, and supporting
application-shell endpoints were fixture-backed.

The browser behavior test proves:

- Points persistence with positive, negative, and health rules;
- reload from persisted settings;
- Priority/Points round trips without losing either configuration; and
- the real compute-sort result rendered in manual stream sorting.

The fixture's expected order was `[202, 101]`. The pre-fix result and the
deliberate Priority mutant both produced `[101, 202]`, so the assertion
distinguishes Points behavior from the legacy evaluator.

The additional E2E test proves that an unrelated successful server with the
wrong nonce cannot satisfy harness readiness. The browser behavior test remains
the critical API seam evidence above.

Command and result:

```text
E2E_EXACT_BUILD=true npx playwright test e2e/smart-sort-points.spec.ts --project=chromium --workers=1 --retries=0
PASS: 2/2 in 6.5 seconds
```

## Candidate gate evidence

All results below were parent-verified for candidate
`d40d915eacb5190aa6d59f4194f7e86255003d56` and are automated gate evidence.

| Command | Result |
| --- | --- |
| `scripts/backend-gate.sh` | PASS: 12,110 passed, 3 skipped, 2 deselected; total coverage 80.37%; 567.51 seconds. |
| `cd frontend && npm run lint` | PASS. |
| `cd frontend && npm run typecheck` | PASS. |
| `cd frontend && npm run test:coverage` | Clean isolated rerun PASS: 256 files, 3,620 tests; statements 59.03%, branches 54.91%, functions 51.69%, lines 60.41%. |
| `cd frontend && npm run build` | PASS: Vite 8.0.16, 973 modules; existing chunk-size advisory only. |
| `npm run test:playwright-config` | PASS: 5/5. |
| Exact-build Smart Sort Chromium command above | PASS: 2/2 in 6.5 seconds. |
| `/home/lecaptainc/ecm/enhancedchannelmanager/.venv/bin/mkdocs build --strict` | PASS on the current docs before this evidence refresh. |

The backend gate's three documented skips and two slow-test deselections are
part of the recorded passing result. The Vite build emitted its existing
chunk-size advisory. Immediately before the clean frontend coverage rerun, all
3,620 assertions completed but Vitest rejected the run for one late unhandled
rejection, `window is not defined`, from `ChannelPipelineTab.test.tsx`.

## Scope boundary

v0.18.2 does not include a score preview or explanation UI, profiles, compound
rule groups, per-channel profiles, SQL migrations, or new dependencies.

## Related documentation

- [Channel Defaults: choose how Smart Sort ranks streams](../user_guide/settings/channel-defaults.md#choose-how-smart-sort-ranks-streams)
- [API: Smart Sort settings](../api.md#smart-sort-settings)
- [API: compute-sort](../api.md#post-apistream-statscompute-sort)
