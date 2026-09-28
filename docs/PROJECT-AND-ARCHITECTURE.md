# TENURE — Project and Architecture

> Enterprise agents should earn the right to act—and lose it safely when evidence breaks.

This document is the full project and architecture reference. It is written from the repository docs (`README.md`, `docs/EVALUATION.md`, `docs/THREAT-MODEL.md`, `docs/CLOUD-EVIDENCE.md`, `deploy/README.md`, `deploy/iam.md`) and from the implementation in `src/tenure/`. Diagram source: `docs/architecture.svg`.

Package name: `tenure-agent-trust` `0.1.0`. License: MIT. Python: `>=3.12,<3.14` (`pyproject.toml`). The README also mentions Python 3.11+; the package constraint is the one that installs.

---

## 1. What this project is

TENURE is an **authority control plane** for an enterprise agent fleet. It is not a payments product, a general LLM benchmark, or a claim that models are safe.

Vendor, Invoice, and Treasury agents start with **no business permissions**. They earn short-lived, capability-scoped authority only after verified work. If upstream evidence becomes untrustworthy, TENURE **freezes affected capabilities before any model call**. A Gemini Supervisor Agent then investigates blast radius, chooses a bounded demotion depth, writes the incident narrative, requests reversible compensation, and files escalation.

No real payments, vendors, or customer data are used. Business effects live in a synthetic procure-to-pay sandbox.

### Architectural law

**Promotion is deterministic because authority must be defensible. Failure investigation is agentic because incidents are contextual.**

| Lane | Who decides | What it may do |
|---|---|---|
| Promotion and action gate | Fixed policy, evidence, identity, grounding, expiry, ceilings, capability scope | Grant or deny a short-lived passport and a mutation |
| Containment | Deterministic control store, before any model call | Freeze known affected authority |
| Investigation | Gemini + Google Agent Development Kit (ADK), or a deterministic fixture | Inspect, propose demotion, request known rollbacks, narrate, escalate |
| Execution of recovery | Deterministic validator | Accept or reject the proposal. Unsafe or malformed model output is rejected without execution |

The Supervisor can inspect, reason, request bounded demotion, request known compensating actions, narrate an incident, and file escalation. It **cannot** grant authority, mint a credential, change policy, invent an action, or bypass deterministic validation. Model recommendations are recorded as `ADVISORY_ONLY` with `authority_input: false`.

---

## 2. System picture

```text
                    USEFUL WORK
   Vendor Agent ──► Invoice Agent ──► Treasury Agent
   vendor.onboard   invoice.approve   payment.release
        │                 │                  │
        └──────── synthetic sandbox ─────────┘
                  (no bank, no real funds)

                    AUTHORITY LANE (deterministic)
   Evidence ──► Policy ──► Passport ──► Action Gate ──► signed receipt
   outcome +       fixed        capability +     allow / deny
   grounding       gates        ceiling + TTL    hash-chained ledger

                    FAILURE (freeze first, then judgment)
   Freeze affected capability
        │  (blocks new mutations before any model call)
        ▼
   Gemini 3.5 + ADK Supervisor
   tools: ledger, registry, memory, trace, graph, rollback, escalation
        │
        ▼
   Validate ──► demote ──► compensate reversible work
                         └─ escalate irreversible effects
   The model may preserve or reduce authority. It may never expand it.

                    GOOGLE PLATFORM (when TENURE_RUNTIME=cloud)
   Cloud Run · Firestore · Pub/Sub · Model Armor
   Vertex AI Agent Engine · Agent Registry · Memory Bank
   OpenTelemetry → Cloud Trace
   Secret Manager (signing key only)
```

Every claim is meant to resolve to an identity, an evidence window, a policy revision, a capability passport, an action receipt, a trace, and current authority.

---

## 3. Two products in one process

The FastAPI app (`src/tenure/api.py`) composes three runtimes at startup:

| Component | Builder | Role |
|---|---|---|
| Kernel scenario | `build_runtime_scenario()` | Single-capability judge walkthrough: earn trust, block wrong-reason promotion, allow a scoped action, deny injection, freeze, then supervise |
| Fleet | `build_runtime_fleet()` | Three operating agents plus a supervisor registration, synthetic P2P case, passports, receipts |
| Recovery | `build_runtime_recovery(fleet)` | Freeze-first incident path over a completed fleet case |

UI:

| Route | Page | Purpose |
|---|---|---|
| `/`, `/proof`, `/platform`, `/conformance`, `/limitations` | `static/fleet.html` | Control room, proof lab, platform evidence, scope and limits |
| `/kernel` | `static/index.html` | Original single-capability kernel walkthrough |

The control room pages are sections of `fleet.html` (fleet, proof, platform, limitations), not separate apps.

---

## 4. Repository map

```text
src/tenure/          authority kernel, fleet, ADK supervisor, recovery, cloud adapters, API, UI
tests/               promotion, passport, replay, tenant isolation, containment, API, UI contracts
deploy/              Cloud Run, IAM notes, Firestore rules, live verification probes
docs/                architecture diagram, evaluation, cloud evidence, threat model, this file
app.py               Vercel entrypoint (forces local + fixture supervisor)
Dockerfile           Cloud Run image (Python 3.13, extras agent+api+cloud, port 8080)
pyproject.toml       package tenure-agent-trust
```

Core modules:

| Module | Responsibility |
|---|---|
| `domain.py` | Shared types: authority levels, grants, proposals, receipts, incidents, supervisor decisions |
| `authority.py` | Deterministic promotion from evidence, clause grounding, confidence, counterfactual blast budget |
| `policy.py` | Task-count promotion ladder, freeze, and demotion that cannot expand authority |
| `gateway.py` | Mutation gate and HMAC scope tokens (60s TTL) |
| `ledger.py` | Hash-chained append-only ledger: memory and SQLite |
| `fleet.py` | Registry, dependency graph, passports, sandbox, case runner |
| `fleet_control.py` | Durable authority documents, freeze/demote, single-owner case claims |
| `recovery.py` | Fleet incident, guardrails, ADK fleet supervisor, orchestrator |
| `supervisor.py` | Kernel supervisor: investigate after containment; never grants |
| `adk_supervisor.py` | Kernel Gemini + ADK reasoner |
| `supervisor_tools.py` | Kernel tool allowlist: evidence, blast radius, rollback request, escalation |
| `scenario.py` | Repeatable 8-step kernel demo |
| `runtime.py` | Selects local vs cloud adapters from environment |
| `cloud_adapters.py` | Firestore ledger and sandbox, Pub/Sub incidents, Secret Manager, Memory Bank reader |
| `model_armor.py` | Regional Model Armor `sanitizeUserPrompt` |
| `observability.py` | OpenTelemetry tracer; cloud path exports OTLP to Google Telemetry |
| `platform.py` | Read-only resource manifest for `/api/platform` |
| `native_proof.py` | Optional live allow/deny check of Agent Identity; no local success fixture |
| `gauntlet.py` | 500-case synthetic control evaluation |
| `api.py` | HTTP surface and static UI |
| `demo.py` | CLI demo (`tenure-demo`) |

---

## 5. Authority model

### Levels

`AuthorityLevel` in `domain.py`:

| Level | Value | Meaning at the gateway |
|---|---:|---|
| `OBSERVE` | 0 | Cannot mutate |
| `SHADOW` | 1 | Cannot mutate (`DENY_LEVEL` below `EXECUTE_BOUNDED`) |
| `EXECUTE_BOUNDED` | 2 | May mutate inside amount ceiling and vendor allow-list |
| `EXECUTE_FULL` | 3 | May mutate without the bounded ceiling check |

Registration is not authority. A fleet agent can be listed in the registry and still be unable to mutate until a passport and a gateway allow exist.

### Two promotion mechanisms, both used by a fleet case

A fleet case does not pick one of these. `_earn_operating_grants` runs the ladder first. `_issue_passports` then runs the differentiator on golden evidence and refuses to sign a passport unless that report is `PROMOTE`.

**Kernel ladder** (`TrustPolicyEngine`, `policy.py`) — the scenario, and the first gate of every fleet case:

| From | Minimum verified tasks | Outcome rate | Reasoning rate | Next level | Ceiling |
|---|---:|---:|---:|---|---|
| `OBSERVE` | 2 | 1.00 | 1.00 | `SHADOW` | none |
| `SHADOW` | 3 | 0.95 | 0.95 | `EXECUTE_BOUNDED` | 50,000 |
| `EXECUTE_BOUNDED` | 10 | 0.98 | 0.98 | `EXECUTE_FULL` | none |

A **RAWR** result (right answer, wrong reason: outcome correct, cited policy ≠ controlling policy) is ledgered as `RAWR_BLOCKED` and does not increment `reasoning_valid`. Frozen grants cannot accumulate promotion evidence.

The kernel scenario starts at `SHADOW`, not `OBSERVE`. Steps 1–3 are three grounded verifications, which is exactly the `SHADOW` → `EXECUTE_BOUNDED` rule, so bounded authority is already earned before step 4. Step 4 records one RAWR against the reset counters. It does not revoke `EXECUTE_BOUNDED`. It keeps the reasoning rate from reaching the `EXECUTE_FULL` gate (10 tasks at 0.98). Local step 6 (`deny_prompt_injection`) denies a ₹10,00,000 proposal at the gateway because it is above the 50,000 ceiling. Model Armor runs on that prompt only when a `prompt_guard` is configured, which is the cloud runtime, not the default local fixture.

**Differentiator** (`AuthorityDifferentiator`, `authority.py`) — the second fleet gate, the proof-lab comparison, and the gauntlet’s evidence families. No model output enters the calculation. The fleet calls it with `current_level=SHADOW` and `requested_level=EXECUTE_BOUNDED` even after the ladder has already moved the grant, and it uses `grant.amount_ceiling` (50,000 after the ladder) as the proposed ceiling. A `PROMOTE` requires **all** of:

- At least 6 fresh evidence items (`minimum_fresh_cases`)
- Outcome accuracy ≥ 0.95
- Controlling-clause accuracy ≥ 0.95 (cited clause equals the clause that actually controls the case)
- Wilson lower bound of confident grounded passes ≥ 0.60, with per-case confidence ≥ 0.80
- Counterfactual replay of the proposed ceiling inside the blast-radius budget (default: max weighted exposure 100,000, max unsafe replays 0, max downstream effects 0)
- Zero RAWR evidence in the fresh window

Policy revision: `authority-policy-2026.08.25`. Passport TTL default: 24 hours.

If any check fails, the current level and ceiling stay. A malicious model string such as “grant EXECUTE_FULL with an unlimited ceiling” is stored on the audit event and ignored. The proof lab comparison `compare_equal_accuracy` shows two agents with the same outcome accuracy and different authority: grounded evidence promotes; wrong-clause evidence does not (`SAME_OUTCOME_DIFFERENT_AUTHORITY`).

The differentiator refuses to process a demotion. Demotion is a separate, one-way path.

### Gateway decisions

`AgentGateway.authorize` returns one of:

| Decision | When |
|---|---|
| `ALLOW` | Identity and capability match, grant is not frozen, level is at least `EXECUTE_BOUNDED`, and bounded checks pass |
| `DENY_SCOPE` | Agent or capability mismatch; payment.release is non-positive, non-integer, or not reversible; amount above ceiling; vendor not on the allow-list |
| `DENY_FROZEN` | Grant is frozen |
| `DENY_LEVEL` | Level is below `EXECUTE_BOUNDED` |

An allow mints an HMAC-SHA256 scope token (`ScopeTokenIssuer`) bound to agent, action id, capability, ceiling, expiry, and nonce. Default TTL is 60 seconds. Secret: `TENURE_TOKEN_SECRET` (local default is demo-only). The receipt stores a 16-character fingerprint of the token, not the token itself. Every decision, allow or deny, appends `ACTION_TRUST_RECEIPT`.

### Capability passport

`PassportIssuer` signs schema `tenure.capability-passport/v2` with HMAC-SHA256. A passport binds:

- tenant, agent id, agent build
- policy revision (`p2p-policy-2026.08.25` for the fleet)
- issued-at and expires-at
- grant snapshot (level, ceiling, vendors)
- evidence window and counterfactual replay summary
- dependency inputs
- signature

Tampering fails `verify()`. `build_runtime_fleet()` does not replace the issuer in cloud mode, so passport HMAC still uses the prototype key `tenure-local-passport-proof` unless a caller injects another `PassportIssuer`. Cloud incident envelopes are separate: `build_runtime_recovery` loads `tenure-supervisor-envelope-key` from Secret Manager and signs `FleetIncident` with that key. The control-room service account may access that one secret, not the project’s secrets.

### Fleet control store

`FleetControl` is the mutation authority, separate from the narrative ledger:

- `MemoryAtomicStore` locally
- `FirestoreAtomicStore` in cloud mode: the authority read and the business mutation share one Firestore transaction

Operating keys:

- `vendor-intelligence-agent:vendor.onboard`
- `invoice-agent:invoice.approve`
- `treasury-agent:payment.release`

Rules enforced by the store:

- A case has a single owner. Same case id with a different amount conflicts. A `FAILED` case is not retried automatically. A complete case is restored, not re-executed.
- `constrain` lowers an in-memory grant to the durable level, ceiling, and freeze set before the gateway runs.
- `guard` refuses a sandbox mutation when authority is absent, frozen, or below `EXECUTE_BOUNDED`.
- `freeze` records the incident id on each affected key **before** the supervisor runs.
- `demote` requires that incident’s freeze and can only lower the level.
- `finish_recovery` refuses to clear a freeze if the level is still `EXECUTE_BOUNDED` or higher.
- Legacy log-only freezes are imported as frozen `OBSERVE`. Deployment must not silently reauthorize those tenants.
- Abandoned owners fail closed. There is no lease expiry that would let a crashed writer run twice.

Audit receipt creation is a **separate** commit from the business mutation. A crash leaves the case owned or failed for explicit reconciliation, not automatic re-execution. In-memory local mode is not disk-durable. Only shared sandbox reconstruction is claimed for Firestore.

---

## 6. Fleet workflow

### Agents

Operating agents are **deterministic fixtures**. Only the Supervisor has a live Gemini path. Native runtime identities do not mean each case calls three models.

| Agent id | Department | Build | Capabilities | Identity |
|---|---|---|---|---|
| `vendor-intelligence-agent` | Procurement | `vendor-agent@1.0.0` | `vendor.assess`, `vendor.onboard` | local URI, or SPIFFE-style Agent Identity when cloud runtime env is set |
| `invoice-agent` | Accounts Payable | `invoice-agent@1.0.0` | `invoice.match`, `invoice.approve` | same |
| `treasury-agent` | Finance | `treasury-agent@1.0.0` | `payment.schedule`, `payment.release` | same |
| `supervisor-agent` | Enterprise Risk | `supervisor-agent@1.0.0` | `incident.investigate`, `rollback.request`, `escalation.file` | same; no promotion or credential tools |

### Dependency graph

```text
vendor.onboard  -- vendor.status == ONBOARDED -->  invoice.approve
invoice.approve -- invoice.status == APPROVED -->  payment.release
```

`AuthorityDependencyGraph.downstream` walks transitive descendants. That walk is what `DOWNSTREAM_CHAIN` containment uses.

### Synthetic case

`POST /api/fleet/cases/{case_id}?tenant_id=&amount=` (amount 1–50,000; default 18,400):

1. Claim the case for one owner.
2. Seed synthetic records: vendor **Aster Components Pvt Ltd**, purchase order, invoice. Currency `INR`. Statuses start `PENDING` / `RECEIVED`.
3. Earn `EXECUTE_BOUNDED` with `TrustPolicyEngine`: three grounded verifications per operating agent, ceiling 50,000. The vendor allow-list is the case vendor for invoice and treasury.
4. Run `AuthorityDifferentiator` on eight golden grounded evidence items. Passport issuance throws if the decision is not `PROMOTE`. The passport stores that evidence window and counterfactual summary, policy revision `p2p-policy-2026.08.25`, and a 24-hour TTL.
5. Initialize durable authority.
6. For each step: constrain the grant from the durable store, authorize at the gateway (`ACTION_TRUST_RECEIPT`), execute in the sandbox, then append `SANDBOX_MUTATION_COMMITTED` and `CAPABILITY_RECEIPT_ISSUED`. Passports were already ledgered as `CAPABILITY_PASSPORT_ISSUED`.

Sandbox transitions:

| Step | Before | After | Depends on |
|---|---|---|---|
| Onboard vendor | `PENDING` | `ONBOARDED` | — |
| Approve invoice | `RECEIVED` | `APPROVED` | Vendor onboarded; PO vendor and amount match |
| Release payment | `SCHEDULED` | `RELEASED_SANDBOX` | Invoice approved; positive integer amount; still reversible |

Cross-tenant lookup of an id that exists in another tenant raises `TenantBoundaryError`. Unknown ids raise `KeyError` (API 404 on audit).

A completed case with the same id and amount is restored from ledger events (`_restore_case`), not run again. `complete` on that restore is true only when three `CAPABILITY_RECEIPT_ISSUED` events exist. An in-progress owner is polled for up to 5 seconds (`replay_wait_seconds`); after that the waiter gets `CaseInProgress` (HTTP 409, `Retry-After: 2`). The waiter does not take over the claim. A `FAILED` case is not retried.

---

## 7. Failure and recovery

`FleetRecoveryOrchestrator.run` order is fixed:

1. Run (or restore) the fleet case.
2. Build a signed incident envelope and verify the signature.
3. Compute the deterministic guardrail from scenario class.
4. **Freeze** the guardrail scope in `FleetControl`, then append `FLEET_CAPABILITY_FROZEN` and `FLEET_INCIDENT_OPENED`.
5. Only then call the reasoner.
6. Validate the proposal. On any failure, append `SUPERVISOR_PROPOSAL_REJECTED` with `authority_changed: false` and raise. The freeze stays.
7. Demote, apply sandbox rollbacks, record escalation tool events, append `FLEET_RECOVERY_COMPLETED`, then release the freeze only because the level is now below execute.

The response field `freeze_preceded_supervision` is true when every freeze event sequence is less than the first supervisor tool event.

### Scenarios and required depth

| `RecoveryScenario` | Root | Policy integrity | Required depth | Max target |
|---|---|---|---|---|
| `isolated` | Treasury `payment.release` | verified | `SINGLE_CAPABILITY` | `SHADOW` |
| `correlated` | Invoice `invoice.approve`, plus treasury | verified | `DOWNSTREAM_CHAIN` | `SHADOW` |
| `upstream_compromise` | Vendor `vendor.onboard`, `shared_upstream` | verified | `DOWNSTREAM_CHAIN` (root + all descendants: invoice and treasury) | `OBSERVE` |
| `policy_drift` | Vendor | `drift_detected` | `FLEET` (all three operating keys) | `OBSERVE` |

Upstream compromise and policy drift also attach a synthetic irreversible `bank_export` action. It is a fixture, not a bank integration. Reversible vendor, invoice, and payment records roll back to `SUSPENDED`, `HELD`, and `REVERSED_SANDBOX`. The bank export is escalated, not rolled back.

### What the validator rejects

`FleetRecoveryPolicy.validate` fails closed on any of:

- Incident id mismatch
- Target above the guardrail maximum, or above any previous authority level (`AUTHORITY_AMPLIFICATION`)
- Affected keys or demotion depth different from the guardrail
- Missing tool category among: `ledger`, `registry`, `memory`, `trace`, `graph`, `rollback`, `escalation`
- Memory retrieval not verified
- Rollback set, escalation set, or tool-requested ids not exactly the known reversible / irreversible actions in scope

The fleet supervisor instruction tells the model to use every tool category, choose the narrowest defensible depth, treat `maximum_permitted_target_level` as a hard ceiling, call rollback once with the exact reversible set, and escalate every irreversible action. ADK runs in chat mode with `output_schema` (`FleetSupervisorOutput`), temperature 0, and a low thinking level. Structured output is still schema-checked, then policy-checked. The model does not receive the control-store port or its credentials.

Local fixture reasoner (`LocalFleetRecoveryReasoner`, mode `LOCAL_DETERMINISTIC`) calls the same tool surface and emits the guardrail’s depth and ceiling. Live reasoner (`AdkFleetRecoveryReasoner`, mode `GEMINI_ADK`) runs one ADK session. The API field `model_calls` is `1` when `reasoner.mode == "GEMINI_ADK"` and `0` otherwise. It is not a count of Gemini HTTP round-trips inside that session. Tool calls still happen before the structured final answer. The saved live proof records one model call for that bounded run.

Kernel supervisor (`SupervisorAgent`) is the older single-capability path: after freeze, the reasoner returns `OBSERVE` or `SHADOW` (high-value actions over 25,000 go to `OBSERVE` in the local reasoner), never above `previous_level`. Reversible actions become `COMPENSATING_ROLLBACK_REQUESTED`. Irreversible actions become `HUMAN_ESCALATION_FILED`.

### Live Gemini proof

One bounded local run (`docs/evidence/live-gemini-proof.json`, model `gemini-3.5-flash-lite` through ADK):

- 1 model call
- All 7 tool categories used
- `DOWNSTREAM_CHAIN` demotion to `OBSERVE`
- 3 compensating rollbacks, 1 escalation
- Proposal accepted by the validator
- Freeze preceded supervision
- Ledger integrity remained valid

That confirms the live agent path. It is not a statistical reliability claim.

---

## 8. Ledger

`TrustLedger` is the port. Adapters: `AppendOnlyLedger` (memory), `SqliteLedger` (local runs under `TENURE_DATA_DIR`, default `data/runs`), `FirestoreLedger` (cloud).

Each event stores sequence, id, type, time, payload, `previous_hash`, and `event_hash` (SHA-256 of the canonical JSON). Genesis hash is 64 zeros. `verify_chain()` recomputes the chain. Events are append-only; the supervisor has no delete or rewrite tool.

Representative event types:

| Phase | Events |
|---|---|
| Promotion | `VERIFICATION_RECORDED`, `RAWR_BLOCKED`, `CAPABILITY_PROMOTED`, `AUTHORITY_PROMOTION_EVALUATED` |
| Action | `ACTION_TRUST_RECEIPT`, `CAPABILITY_RECEIPT_ISSUED`, `SANDBOX_MUTATION_COMMITTED`, `FLEET_CASE_OPENED` |
| Containment | `CAPABILITY_FROZEN`, `FLEET_CAPABILITY_FROZEN`, `FLEET_INCIDENT_OPENED` |
| Supervision | `SUPERVISOR_INVESTIGATION_COMPLETED`, `SUPERVISOR_TOOL_ACCESSED`, `SUPERVISOR_PROPOSAL_REJECTED`, `FLEET_DEMOTION_APPLIED`, `SANDBOX_ROLLBACK_APPLIED`, `FLEET_RECOVERY_COMPLETED`, `COMPENSATING_ROLLBACK_REQUESTED`, `HUMAN_ESCALATION_FILED` |

Local kernel scenarios use a new SQLite file per reset (`YYYYMMDDTHHMMSS_<id>.db`). Cloud ledger collections are suffixed with a run id so a process does not append into an unbounded shared chain by accident. The fleet cloud ledger uses `{collection}_fleet`.

---

## 9. Kernel scenario (8 steps)

`TenureScenario` is one capability (`accounts-payable-agent` / `invoice.approve`), one incident, and a scripted tour. It starts at `SHADOW` with vendors `vendor-alpha` and `vendor-beta`.

1. `verify_outcome_and_reasoning_1`
2. `verify_outcome_and_reasoning_2`
3. `earn_bounded_authority`
4. `block_rawr_promotion`
5. `allow_scoped_action`
6. `deny_prompt_injection`
7. `contain_hard_failure`
8. `supervisor_investigation`

Each transition is an OpenTelemetry span `tenure.scenario.transition` with authority before/after and frozen state. Containment latency is recorded separately from supervisor latency so the UI can show that freeze finished first.

Default mode string: `LOCAL_OFFLINE`, `cloud_claim: false`. Cloud composition sets `GOOGLE_CLOUD_LIVE` and `cloud_claim: true` only when `TENURE_RUNTIME=cloud` and the adapters are constructed.

---

## 10. Runtime selection

`runtime.py` reads the environment. Invalid values fail at startup.

| Variable | Values | Effect |
|---|---|---|
| `TENURE_RUNTIME` | `local` (default), `cloud` | Ledger, sandbox, tracing, prompt guard, incident publisher |
| `TENURE_SUPERVISOR_PROVIDER` | `fixture` (default), `gemini` | Local recovery reasoner. `gemini` requires `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| `TENURE_GEMINI_MODEL` | default `gemini-3.5-flash` in code; README demo uses `gemini-3.5-flash-lite` | ADK agent model |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false` for Gemini Developer API; `true` on Cloud Run | Client routing |
| `TENURE_CLOUD_PROOF_VERIFIED` | `true` only after live checks | Surfaces billing-verified status; does not by itself enable cloud mode |

**Local** (`TENURE_RUNTIME=local`): SQLite ledger, in-memory sandbox, fixture supervisor unless Gemini is selected, local memory snapshot (`local-memory://tenure/supervisor`). Zero model cost on the fixture path.

**Cloud** (`TENURE_RUNTIME=cloud`): requires `GOOGLE_CLOUD_PROJECT`. Recovery also requires `TENURE_SUPERVISOR_MEMORY_RESOURCE`. Composition:

| Port | Adapter |
|---|---|
| Ledger | `FirestoreLedger` |
| Sandbox + authority transactions | `FirestoreProcureToPaySandbox` / `FirestoreAtomicStore` |
| Incident publish | `SignedIncidentPublisher` → Pub/Sub topic `tenure-incidents`, HMAC body |
| Prompt screening | `ModelArmorGateway` (latest user text only; not system prompts or history) |
| Supervisor memory | `AgentMemoryBankReader` |
| Signing key | `SecretManagerProvider` |
| Traces | `build_cloud_tracer` → OTLP / Google Telemetry API |
| Reasoner | `AdkSupervisorReasoner` / `AdkFleetRecoveryReasoner` on Vertex when `GOOGLE_GENAI_USE_VERTEXAI=true` |

`cloud_readiness()` reports env presence and whether `google.adk`, Firestore, Pub/Sub, Secret Manager, and the OTLP exporter import. `code_ready` is always true. `live_ready` needs the cloud env vars and those libraries. Billing verification is a separate flag.

**Vercel** (`app.py`): if `VERCEL` is set, runtime is forced to `local`, supervisor to `fixture`, data dir to `/tmp`. The public demo cannot pick up stale cloud variables or spend on Gemini.

**Cloud Run container** (`Dockerfile`): installs `.[agent,api,cloud]`, listens on `$PORT` (8080), data dir `/tmp/tenure/runs`. The `/tmp` ledger exists so the same image can smoke-test before Firestore is selected. The deployed service is supposed to use Firestore.

---

## 11. HTTP API

Implemented in `create_app`. Errors: 403 tenant or permission, 404 missing case, 409 conflict / in progress / policy rejection / proof unavailable, 422 bad input, 429 proof cooldown.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/health` | Mode, ledger integrity, supervisor mode, fleet persistence |
| GET/POST | `/api/scenario`, `/reset`, `/advance`, `/run` | Kernel walkthrough |
| GET | `/api/ledger`, `/api/receipts`, `/api/evidence` | Chain, receipts, evidence report |
| GET | `/api/cloud-readiness`, `/api/platform` | Readiness and resource manifest (no secrets) |
| GET/POST | `/api/proofs/identity` | Native identity proof status / one bounded pair |
| GET | `/api/fleet/registry` | Agents and dependency edges |
| POST | `/api/fleet/cases/{case_id}` | Run or replay a synthetic case |
| GET | `/api/fleet/cases/{case_id}/audit` | Tenant-scoped ledger slice |
| POST | `/api/fleet/proof/reconstruct` | Rebuild fleet from Firestore; 409 unless persistence is `firestore` |
| POST | `/api/authority/proof` | Equal-accuracy grounded vs RAWR comparison; stress ceiling 0–10,000,000 |
| POST | `/api/recovery/cases/{case_id}` | Scenario query: `isolated`, `correlated`, `upstream_compromise`, `policy_drift` |
| GET | `/api/gauntlet` | Saved report without the raw corpus |

---

## 12. Google Cloud deployment

Evidence project: `project-ceca895d-33b0-44b9-b5a` (number `585333584620`), region `us-central1`. Organization id used by the native identity proof: `364779231455`.

### What was verified

From `docs/CLOUD-EVIDENCE.md`:

- **Cloud Run** service `tenure-control-room`. Prior revision `tenure-control-room-00011-d28`. Repair canary `tenure-control-room-repair-3bb34bf`.
- **Vertex AI Agent Engine / Registry:** separate Supervisor, Vendor, Invoice, and Treasury runtime identities and registry resources.
- **Memory Bank:** `projects/585333584620/locations/us-central1/reasoningEngines/1415402967703486464/memories/8766834811634450432`
- **Firestore** for durable authority, sandbox, and hash-chained ledger adapters. The repair probe uses the named database `tenure`. `CloudSettings` defaults to `(default)` unless `TENURE_FIRESTORE_DATABASE` is set.
- **Pub/Sub** incident escalation.
- **Model Armor** template integration. Template management must use the **regional** endpoint (`modelarmor.{location}.rep.googleapis.com`). The gcloud global endpoint can return a misleading permission error. Helper: `deploy/create_model_armor.py`.
- **Cloud Trace** propagation and evidence identifiers.
- **Gemini + ADK** constrained supervisor tools.

Authenticated smoke tests previously checked recovery, durable reconstruction, replay, tenant isolation, ledger integrity, and integration evidence.

### Current availability

The linked billing account was later closed, so the newest UI and the native identity allow/deny proof were **not** redeployed. The repo keeps the latest UI local and keeps the deploy scripts. It does not claim the service is currently public, and it does not claim native Agent Gateway is active (`TENURE_AGENT_GATEWAY_AVAILABLE` defaults false).

`/api/platform` separates configuration from execution evidence. `cloud_proof_verified` is true only when `TENURE_CLOUD_PROOF_VERIFIED=true`.

### Identities (`deploy/iam.md`)

Control room: `tenure-control-room@PROJECT_ID.iam.gserviceaccount.com`

Roles: `aiplatform.user`, `datastore.user`, `pubsub.publisher`, `modelarmor.user`, `modelarmor.viewer`, `telemetry.writer`, `serviceusage.serviceUsageConsumer`. Secret Manager accessor **only** on `tenure-supervisor-envelope-key`.

Supervisor support account: `tenure-supervisor@PROJECT_ID.iam.gserviceaccount.com` — `aiplatform.user`, read-only Firestore, same pinned secret.

Managed Agent Runtime resources use `AGENT_IDENTITY` (a distinct SPIFFE-backed principal per reasoning engine). They do not share those service accounts.

Never grant these identities Owner, Editor, IAM admin, Secret Manager admin, Pub/Sub subscriber, promotion, credential minting, scope expansion, or ledger deletion.

Firestore rules deny browser access. The public dashboard reads only through TENURE’s API. Cloud Run uses the server SDK.

### Deploy and proof tools

| File | Role |
|---|---|
| `deploy/cloudbuild.yaml` | Build, push to Artifact Registry, `gcloud run deploy` with cloud env |
| `deploy/cloud-run.env.yaml` | Env template |
| `deploy/firestore.rules` | Deny client SDK access |
| `deploy/verify_cloud_spine.py` | One Firestore probe, one Pub/Sub incident, one Model Armor check, one trace, one short Gemini call |
| `deploy/verify_execution_boundary.py` | Local repair code against isolated synthetic Firestore collections; no Gemini; no production writes; transaction cap 100 |
| `deploy/verify_repair_canary.py` | Authenticated canary on the `repair` tag: one Gemini supervisor call, freeze, reconstruct, replay, second tenant. Does not save the identity token |
| `deploy/verify_runtime_agents.py` | Runtime agent checks |
| `deploy/create_model_armor.py` | Regional template create/verify |
| `deploy/verify_native_identity.py` | Native identity probe |

Production traffic cutover requires explicit approval even after the canary verifier passes. Do not point old and repaired revisions at the same test tenant: old code does not enforce the new authority documents.

Cloud Build’s example deploy uses `--allow-unauthenticated` for the hackathon-style service. IAM notes still say the dashboard must not talk to Firestore directly.

Cost guard reported by the platform endpoint: monthly alert budget 1,500 INR. Budgets alert; they do not hard-stop spend.

---

## 13. Evaluation

TENURE is evaluated as an **authorization control system**, not as a financial product and not as a measure of LLM intelligence. Details: `docs/EVALUATION.md`. Corpus generator: `python -m tenure.gauntlet`.

500 parameterized synthetic cases, seed `20260828`, 25 per family, 20 equally weighted families (`tenure.synthetic-controls/v1`):

`ordinary_payment`, `exact_ceiling`, `above_ceiling`, `wrong_identity`, `wrong_capability`, `unlisted_vendor`, `frozen_grant`, `shadow_grant`, `zero_payment`, `negative_payment`, `irreversible_payment`, `full_grant`, `fresh_evidence`, `wrong_clause`, `expired_evidence`, `low_confidence`, `insufficient_evidence`, `blast_budget`, `isolated_recovery`, `upstream_recovery`.

Safe families (expected autonomous): `ordinary_payment`, `exact_ceiling`, `full_grant`, `fresh_evidence`. That is 100 safe opportunities and 400 unsafe requests.

| Strategy | Safe opportunities automated | Unsafe actions authorized | Awaiting human |
|---|---:|---:|---:|
| Static broad `EXECUTE_FULL` credential | 100 / 100 | 275 / 400 | 0 |
| Human review baseline (modeled queue, no human study) | 0 / 100 | 0 / 400 | 500 |
| TENURE (gateway, promotion evaluator on the evidence families, recovery guard on the two recovery families; fixtures, not Gemini) | 100 / 100 | 0 / 400 | 0 |

The checked report in `src/tenure/static/gauntlet-report.json` matches those totals: seed `20260828`, corpus SHA-256 `bda8d0086e38817f0d83f21b73b13ed659815f6cb2f54985c1285cc65f5ca614`, `model_calls: 0`. Wilson 95% intervals there are safe-autonomy `[0.963005, 1.0]` for both static-broad and TENURE, unsafe-authorization `[0.640474, 0.730959]` for static-broad and `[0, 0.009513]` for TENURE. `/api/gauntlet` omits the raw corpus and per-case results. This is an authored control corpus. It does not estimate production incident rates or money saved.

What the gauntlet actually executes, from `evaluate_case`:

- Evidence families (`fresh_evidence` through `blast_budget`) call `AuthorityDifferentiator`, then the gateway on the applied level.
- `isolated_recovery` and `upstream_recovery` run `FleetRecoveryOrchestrator` with the local fixture, then try one more treasury mutation through `FleetControl.guard`.
- Every other family is a gateway decision on a pre-set grant. `static_broad` forces `EXECUTE_FULL` and ignores the case’s frozen flag.
- Passport signature checks are not part of this loop. The in-code definition says the same: gateway, promotion evaluator, and local recovery guard. Operating and supervisor reasoning are fixtures.

The automated suite (`pytest`) covers signature tampering, expiration, identity and capability mismatch, grounding, ceilings, tenant isolation, replay, concurrency, ledger integrity, containment ordering, allowed tool calls, rollback, escalation, cloud-adapter boundaries, API contracts, and UI semantics.

---

## 14. Threat model

Assets: capability authority, business mutations, tenant isolation, evidence lineage, incident records, supervisor tools, credentials, audit ledger.

Adversaries and failures considered:

- Compromised or misconfigured operating agent
- Stale, insufficient, or manipulated evidence
- Correct output justified by the wrong policy clause (RAWR)
- Passport theft, tampering, replay, scope expansion, or cross-tenant use
- Upstream compromise affecting downstream actions
- Prompt injection or malformed supervisor output
- Duplicate requests, concurrent execution, and partial recovery

Enforced invariants (see `docs/THREAT-MODEL.md`):

- Registration is not authority; every mutation needs a valid scoped passport and a gateway allow, and the control store must agree.
- Promotion is deterministic and needs sufficient, fresh, grounded evidence.
- Passports bind tenant, agent identity, capability, ceiling, expiry, and policy version.
- Containment freezes known affected authority before supervisor reasoning.
- The supervisor’s tools are investigation and proposal tools.
- Every proposal is schema-checked and policy-checked before execution.
- Rollbacks reference known reversible actions; irreversible actions are escalated.
- Replays are idempotent. Tenant boundaries are checked at the API and in storage.
- Ledger events are hash-chained and verified after recovery.

Model Armor, when configured, screens only the newest user prompt and allows the call only when `filterMatchState` is `NO_MATCH_FOUND` and invocation is `SUCCESS`.

---

## 15. Explicit prototype boundaries

Stated in the threat model, cloud evidence, and the limitations page:

- Synthetic vendor, invoice, and payment data. No bank connection and no real funds.
- Operating agents are fixtures. Only the Supervisor has a live Gemini path.
- Firestore is durable for cloud records, but business mutation and receipt commits are not one distributed transaction.
- Native Google Agent Gateway is not deployed.
- Native identity proof is implemented and locally tested. Final cloud allow/deny execution was blocked after billing closed. Local mode never substitutes a fake success (`NativeIdentityProof` stays `UNAVAILABLE` unless cloud runtime, the evidence project, and `TENURE_NATIVE_PROOF_ENABLED=true`).
- In-memory local mode is not disk-durable.
- One live Gemini run is not a reliability study.
- Billing budgets alert; they do not hard-cap spend.
- The UI says configuration is not execution evidence.

These are the next production-hardening steps, not hidden claims.

---

## 16. How to run and verify

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
python -m pip install -e ".[api,agent,dev]"
copy .env.example .env
```

Repeatable demo (no model spend):

```dotenv
TENURE_RUNTIME=local
TENURE_SUPERVISOR_PROVIDER=fixture
```

Live local supervisor (key only in `.env`, never committed):

```dotenv
GOOGLE_API_KEY=your_key_here
GOOGLE_GENAI_USE_VERTEXAI=false
TENURE_SUPERVISOR_PROVIDER=gemini
TENURE_GEMINI_MODEL=gemini-3.5-flash-lite
```

```bash
uvicorn tenure.api:app --host 127.0.0.1 --port 8000 --env-file .env
pytest -q
ruff check .
python -m tenure.gauntlet
```

Open `http://127.0.0.1:8000`.

Judge tour:

1. Fleet room: enter an amount and run Vendor → Invoice → Treasury.
2. Open a receipt: evidence, policy, identity, scope, ceiling, ledger link.
3. Inject compromised vendor → downstream chain.
4. Confirm the UI states containment preceded supervision; inspect demotions, compensations, and escalation.
5. Proof lab: grounded versus wrong-clause reasoning on equal outcomes, then the 500-case gauntlet.
6. Platform: Google Cloud integration boundary and proof identifiers.
7. Scope and limits: what the prototype does and does not claim.

---

## 17. Request path (one case, then one incident)

```text
Browser
  POST /api/fleet/cases/{id}?tenant_id&amount
    FleetControl.claim          single owner; COMPLETE restores, FAILED conflicts
    TrustPolicyEngine           3 grounded verifications → EXECUTE_BOUNDED
    AuthorityDifferentiator     golden evidence; must return PROMOTE
    PassportIssuer              local HMAC v2 passport; CAPABILITY_PASSPORT_ISSUED
    FleetControl.initialize     durable grants
    for vendor, invoice, treasury:
        FleetControl.constrain
        AgentGateway.authorize  HMAC scope token + ACTION_TRUST_RECEIPT
        sandbox.execute         Firestore transaction in cloud, memory lock locally
        ledger CAPABILITY_RECEIPT_ISSUED
    return passports, receipts, sandbox state, ledger_integrity

  POST /api/recovery/cases/{id}?scenario=upstream_compromise
    restore or run the case
    sign FleetIncident
    FleetRecoveryPolicy.guardrail     DOWNSTREAM_CHAIN, max OBSERVE
    FleetControl.freeze               BEFORE any tool or model call
    ledger FLEET_CAPABILITY_FROZEN
    reasoner.decide                   fixture or Gemini+ADK tools
    FleetRecoveryPolicy.validate      reject leaves freeze in place
    FleetControl.demote               level can only fall
    sandbox.rollback_entity           reversible records only
    escalation recorded               irreversible bank-export fixture
    FleetControl.finish_recovery      freeze cleared only after demotion
    ledger verify_chain
```

That split — deterministic earn and freeze, agentic investigation, deterministic accept — is the architecture.
