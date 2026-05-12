---
type: "query"
date: "2026-04-24T12:26:51.755734+00:00"
question: "Are the 607 INFERRED edges on NormalizationRuleGroup actually correct?"
contributor: "graphify"
source_nodes: ["NormalizationRuleGroup", "StreamContext", "ActionExecutor", "ExecutionContext"]
---

# Q: Are the 607 INFERRED edges on NormalizationRuleGroup actually correct?

## Answer

No — they are AST extractor noise, not real couplings. Every top-10 god node shows the same signature: ~95% of edges are INFERRED+relation=uses at confidence_score=0.50 exactly (the spec says never use 0.50 as a default). 7 of 10 gods are ORM models in backend/models.py. Sample neighbors of NormalizationRuleGroup are unrelated functions in database.py (SQLite setup, PRAGMAs, seed functions). The real high-confidence degree is ~19, not 612. Real god nodes after filtering: StreamContext (~77 high-conf edges), ActionExecutor (~72), ExecutionContext (~75) — these are the actual pluggable runtime-state objects in the auto-creation pipeline.

## Source Nodes

- NormalizationRuleGroup
- StreamContext
- ActionExecutor
- ExecutionContext