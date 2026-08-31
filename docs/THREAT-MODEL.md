# Threat model

## Assets

Capability authority, business mutations, tenant isolation, evidence lineage, incident
records, supervisor tools, credentials, and the audit ledger.

## Adversaries and failures

- A compromised or misconfigured operating agent.
- Stale, insufficient, or manipulated evidence.
- Correct output justified by the wrong policy clause.
- Passport theft, tampering, replay, scope expansion, or cross-tenant use.
- Upstream compromise affecting downstream actions.
- Prompt injection or malformed Supervisor output.
- Duplicate requests, concurrent execution, and partial recovery.

## Enforced invariants

- Registration is not authority; every mutation requires a valid scoped passport.
- Promotion is deterministic and requires sufficient, fresh, grounded evidence.
- Passports bind tenant, agent identity, capability, ceiling, expiry, and policy version.
- Containment freezes known affected authority before Supervisor reasoning begins.
- The Supervisor has investigation and proposal tools, not promotion or credential tools.
- Every proposal is schema-checked and policy-checked before execution.
- Rollbacks must reference known reversible actions; irreversible actions are escalated.
- Replays are idempotent and tenant boundaries are checked at the API and storage layers.
- Ledger events are hash chained and verified after recovery.

## Prototype boundaries

The workflow uses synthetic vendor, invoice, and payment data. The local operating agents
are deterministic fixtures; only the Supervisor has a live Gemini path. Firestore provides
durable cloud records, but business mutation and receipt commits are not a distributed
transaction. Native Google Agent Gateway is not deployed. The native identity proof is
implemented and locally tested but its final cloud allow/deny execution was blocked after
the billing account closed. These are explicit next production-hardening steps, not hidden
claims.
