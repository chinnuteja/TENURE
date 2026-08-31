# Evaluation

TENURE is evaluated as an authorization control system, not as a financial product or a
general measure of LLM intelligence.

## Reproducible gauntlet

`python -m tenure.gauntlet` generates 500 parameterized synthetic decisions across 20
equally weighted families: valid work, expired or insufficient evidence, wrong identity,
wrong capability, ceiling boundaries, grounding failures, frozen and shadow grants,
blast-radius limits, reversible and irreversible recovery, and several other policy
edges.

The evaluation compares:

- **Static broad:** a permanent `EXECUTE_FULL` credential behind the same basic gateway.
- **Human review:** every request waits in a modeled approval queue.
- **TENURE:** the real local gateway, promotion evaluator, passport checks, and recovery
  policy with deterministic operating fixtures.

The saved result is 100/100 safe opportunities automated and 0/400 unsafe actions
authorized for TENURE. This is an authored control corpus, not external validation. The
report includes per-family results, definitions, Wilson intervals, seed, corpus digest,
and limitations so judges can challenge the comparison.

## Live agent check

One bounded Gemini 3.5 Flash-Lite + ADK Supervisor run exercised graph, Registry,
Memory, trace, ledger, rollback, and escalation tools. Its bounded proposal was accepted
by deterministic policy validation. This confirms the real agent path; it does not claim
general model reliability.

## Other verification

The automated suite covers signature tampering, expiration, identity/capability mismatch,
grounding, ceilings, tenant isolation, replay, concurrency, ledger integrity, containment
ordering, allowed tool calls, rollback, escalation, cloud-adapter boundaries, API contracts,
and UI semantics. Desktop and 390px mobile flows were manually exercised end to end.
