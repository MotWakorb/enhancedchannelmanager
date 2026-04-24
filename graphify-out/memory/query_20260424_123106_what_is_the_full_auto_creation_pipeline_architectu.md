---
type: "query"
date: "2026-04-24T12:31:06.405652+00:00"
question: "What is the full auto-creation pipeline architecture in ECM?"
contributor: "graphify"
source_nodes: ["AutoCreationEngine", "ActionExecutor", "StreamContext", "ConditionEvaluator", "ExecutionContext"]
---

# Q: What is the full auto-creation pipeline architecture in ECM?

## Answer

AutoCreationEngine is the orchestrator (run_pipeline / run_rule). It loads rules and streams, applies global filters, batch-probes unprobed streams, then in _process_streams() it builds a StreamContext per stream, hands it to ConditionEvaluator for rule matching, and on match hands the Action list + context to ActionExecutor. ActionExecutor dispatches to ~15 action verbs (_execute_create_channel, _execute_assign_epg, _execute_merge_streams, _execute_assign_logo, _execute_assign_tvg_id, _execute_assign_profile, etc.) that mutate Dispatcharr. Post-processing reorders channel streams, reconciles orphans, retries dummy EPG. rollback_execution undoes on failure. Data currency: 5 DTOs (StreamContext, ExecutionContext, Action, ActionType, ActionResult). StreamContext is test-heavy in usage (73 of 77 high-conf callers are tests) because in production it is built once inside _process_streams. Real production callers of AutoCreationEngine: ConditionEvaluator, ActionExecutor, init_auto_creation_engine. The 600+ ORM-model god nodes (NormalizationRuleGroup, StreamStats, AutoCreationRule, etc.) are AST extraction noise and do not reflect architectural centrality.

## Source Nodes

- AutoCreationEngine
- ActionExecutor
- StreamContext
- ConditionEvaluator
- ExecutionContext