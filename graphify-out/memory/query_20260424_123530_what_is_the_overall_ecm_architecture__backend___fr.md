---
type: "query"
date: "2026-04-24T12:35:30.115019+00:00"
question: "What is the overall ECM architecture (backend + frontend)?"
contributor: "graphify"
source_nodes: ["AutoCreationEngine", "ActionExecutor", "NormalizationEngine", "useEditMode", "useChangeHistory", "App.tsx", "api.ts"]
---

# Q: What is the overall ECM architecture (backend + frontend)?

## Answer

BACKEND - Auto-creation pipeline: AutoCreationEngine.run_pipeline → _process_streams per-stream loop → ConditionEvaluator (matches StreamContext against rule) → ActionExecutor (dispatches to 15+ action verbs). Data currency: StreamContext, ExecutionContext, Action, ActionType, ActionResult. On failure: rollback_execution. Normalization is a SEPARATE subsystem loosely coupled via singleton factory: _process_streams() calls get_normalization_engine() to obtain the NormalizationEngine (in normalization_engine.py), which exposes normalize(), extract_core_name(), test_rule(). The two subsystems share no other high-confidence edges. FRONTEND - App.tsx is the centralized useState hub. It wraps NotificationProvider, routes via useHashRoute to React.lazy-loaded tab components with per-tab ErrorBoundary, and prop-drills state to ChannelsPane/StreamsPane. Edit-commit architecture: useEditMode stages changes in-memory, useChangeHistory provides undo/redo, then bulkCommit() flushes via api.ts. api.ts delegates HTTP to httpClient.ts (fetchJson, buildQuery) and is intercepted by MSW handlers in tests. Lazy per-group stream loading avoids loading 27k streams at once.

## Source Nodes

- AutoCreationEngine
- ActionExecutor
- NormalizationEngine
- useEditMode
- useChangeHistory
- App.tsx
- api.ts