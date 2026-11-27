# Pipeline event-loop responsiveness (GH1031)

ECM's pipeline and HTTP handlers share one event loop. Declaring a function
`async` does not make its synchronous stream × rule × condition evaluation
cooperative. An awaited coroutine that finishes without suspending (for example
a dry-run or skip action) does not give other tasks a turn either.

## Bounded cooperative work

`channel_pipeline_engine._PipelineWorkBudget` checks a monotonic 10 ms target
slice and calls `await asyncio.sleep(0)` when it expires. Each instance belongs
to one invocation, not global engine state. Checkpoints are placed:

- Before each stream, rule/provider skip, and condition in Pass 1, including
  unmatched streams and rules that do not apply to the provider.
- Before each stream and exclusion-term/group check in global filtering,
  including streams excluded before they reach Pass 1.
- Between sort-key extractions. Keys are computed in input order, then sorted
  with the same stable key-only comparison and reverse flag as before.
- Before each Pass 2 stream, including struck streams, no-op/preview actions,
  and `stop_processing` continuations. No new suspension is introduced between
  the actions belonging to an individual stream.

There is no worker-thread handoff: the evaluator, Dispatcharr client, detached
rule snapshots, action executor, and existing session lifecycle stay on their
original thread. Evaluation order and mutation rules are not parallelized.
Cancellation can be delivered at a checkpoint without leaving a worker running
after its awaiting task has exited. Existing router/task cancellation handling
continues to own terminal execution status; direct engine cancellation does not
itself finalize an execution row.

The merge-journal `try/finally` begins before Pass 2's first action/checkpoint,
not only around the post-processing passes. A cancellation at a later stream's
checkpoint therefore flushes earlier landed merges that remain below the
executor's 100-row buffer threshold. Cleanup uses the existing synchronous flush
on the owning thread, with no new await, retry, shield or cancellation suppression.
The existing false-return failure warning remains; an unexpected cleanup exception
is logged and does not replace an already-unwinding exception/cancellation. With
no primary exception, a raised cleanup failure still propagates. Persistence is
best effort if the journal database itself is unavailable; a failed flush is not
claimed as a durable recovery record.

## Responsiveness is not throughput or a latency SLA

The slice is a scheduling target, **not** a hard 10 ms response ceiling. A single
condition/normalization/regex call must return before the next checkpoint can
run. `safe_regex` timeouts are best-effort, not preemption. A request crossing
several middleware layers may need several scheduling turns.

This change does not reduce streams × rules × conditions CPU work; clock checks
and scheduling add some overhead. It does not add CPU cores or promise shorter
pipeline runs. A future process-isolated evaluator would need explicit immutable
inputs, cancellation, and result integration rather than sharing ORM sessions
or live clients with another thread/process.

Sort-key staging adds O(matches in one rule) temporary key/entry pairs, released
before the next rule group; it does not retain another stream × rule trace.

Inspection also identified synchronous phases outside this bounded fix:
normalization/index construction in evaluator/executor initialization,
required-provider cohort normalization, native list sorting after key extraction,
stream smart-sort helpers, execution JSON serialization, and synchronous SQLite
queries/commits. Expensive individual actions and Event Sync's separate matcher
are not made preemptible here. These remain potential latency sources, not a
claim that every engine phase now has a hard response bound.

Execution polling remains a lifecycle view (`running` then terminal status and
final counters), not newly introduced incremental per-condition progress.

## Verification

`backend/tests/integration/test_pipeline_responsiveness.py` runs the real engine,
condition evaluator/regexes, persisted rules, execution lifecycle, isolated SQLite,
and full ASGI health/execution-poll middleware. Only upstream Dispatcharr HTTP is
simulated. The evaluation workload uses 1,200 streams, 330 rules and three regex
conditions; provider-filter skips exercise the same stream/rule cardinality.

An event set inside the active phase starts the concurrent request. Assertions
require responses before that phase finishes, rather than using a fragile elapsed
time threshold. Logical-clock cases deterministically expire the production
budget while using real asyncio scheduling; a separate evaluation case uses the
real monotonic clock. Phase checks cover exclusions, regex sort keys, no-op
actions and struck skips. Live/preview creation tests pin winner/conflict
precedence, action order, descending order, stable reverse-sort ties, persisted
counters and client-thread ownership. Cancellation checks stop evaluation before
completion and assert no continuing worker or open engine-session transaction.
The post-write cancellation regression uses the real executor merge and journal
transaction, proves the row was buffered below threshold, cancels at the next
Pass 2 yield, and checks SQLite's execution-correlated before/after stream IDs
with no later actions. Failure variants exercise a false-return commit failure,
session creation failure and synchronous `BaseException`, preserving the original
`CancelledError`; clean-run variants confirm raised cleanup errors still escape.

These are engine/ASGI integration and scheduling tests, not a deployed-container
load benchmark or proof of a universal HTTP latency bound.
