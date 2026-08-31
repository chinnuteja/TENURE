"use strict";
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n);
const pretty = (s) => String(s).replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
const subjects = {
  "vendor-intelligence-agent": { short: "vendor", title: "Vendor intelligence", department: "Procurement", capability: "vendor.onboard", copy: "Verifies a supplier before downstream invoice and payment actions can proceed." },
  "invoice-agent": { short: "invoice", title: "Invoice agent", department: "Accounts Payable", capability: "invoice.approve", copy: "Matches the invoice to a purchase order and an onboarded supplier before approval." },
  "treasury-agent": { short: "treasury", title: "Treasury agent", department: "Finance", capability: "payment.release", copy: "Releases only a bounded, reversible sandbox payment against an approved invoice." },
  "supervisor-agent": { short: "supervisor", title: "Supervisor agent", department: "Enterprise Risk", capability: "incident.investigate", copy: "Investigates evidence and dependencies, proposes bounded demotion, requests compensation, and files escalation." },
};
const state = { case: null, recovery: null, audit: null, registry: [], selected: "vendor-intelligence-agent", busy: false, connected: false, recoveryAttempted: false, health: null, gauntlet: null, pair: null, generation: 0 };
let identityReceipt = null;
let identityTimer = null;
function renderIdentityStatus(status) {
  const cooldown = status.cooldown_seconds || 0;
  $("runIdentity").disabled = !status.enabled || cooldown > 0;
  $("runIdentity").textContent = !status.enabled ? "Native proof unavailable" : cooldown ? `Retry in ${cooldown}s` : "Run native identity pair ↗";
  $("identityAvailability").textContent = status.message;
  if (identityTimer) clearTimeout(identityTimer);
  if (status.enabled && cooldown > 0) identityTimer = setTimeout(async () => {
    try { renderIdentityStatus(await api("/api/proofs/identity")); }
    catch { $("identityAvailability").textContent = "Proof status unavailable. Refresh before retrying."; }
  }, Math.min(cooldown, 60) * 1000);
}
async function runIdentityPair() {
  $("runIdentity").disabled = true;
  $("runIdentity").textContent = "Checking native identities…";
  $("identityAvailability").textContent = "Waiting for two runtime responses. No result is assumed.";
  $("identityOwner").textContent = "PENDING";
  $("identityOther").textContent = "PENDING";
  $("inspectIdentityProof").hidden = true;
  try {
    identityReceipt = await api("/api/proofs/identity", { method: "POST" });
    const result = identityReceipt;
    $("identityOwner").textContent = result.checks.find((check) => check.role === "owner")?.outcome || "UNVERIFIED";
    $("identityOther").textContent = result.checks.find((check) => check.role === "other")?.outcome || "UNVERIFIED";
    $("identityResult").textContent = result.status === "PASS" ? "Verified: the resource owner was allowed and the other native identity received a permission-specific denial. Zero model calls. This proves memory isolation, not a deployed Agent Gateway." : `Proof ${result.status.toLowerCase()}: ${result.error_code || "observed outcomes did not match the expected boundary"}. No successful-isolation claim.`;
    $("identityResult").classList.toggle("error", result.status !== "PASS");
    $("inspectIdentityProof").hidden = false;
  } catch (error) {
    $("identityOwner").textContent = "UNVERIFIED";
    $("identityOther").textContent = "UNVERIFIED";
    $("identityResult").textContent = `Native proof unavailable: ${error.message}`;
    $("identityResult").classList.add("error");
  } finally {
    try { renderIdentityStatus(await api("/api/proofs/identity")); }
    catch { $("identityAvailability").textContent = "Status unavailable. Refresh before another attempt."; }
  }
}
function newWorkspace() {
  const id = crypto.randomUUID().slice(0, 8);
  Object.assign(state, { tenant: `ui-${id}`, caseId: `case-${id}`, case: null, recovery: null, audit: null, recoveryAttempted: false, recoveryError: false, amount: null, generation: state.generation + 1 });
  $("workspaceId").textContent = `CASE ${id.toUpperCase()} / ISOLATED TENANT`;
  $("amount").value = "18400";
  $("recoveryResult").hidden = true;
  $("eventList").innerHTML = '<p class="empty-record">No events yet. Every completed action will link to a receipt and a decision.</p>';
  $("supervisorStatus").textContent = "Investigation authority. No promotion rights.";
  setStatus("Ready. This is a separate synthetic tenant; previous restrictions are not reset.");
  render();
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(180000) });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body); } catch { /* Preserve status when a proxy returns HTML. */ }
    throw new Error(detail);
  }
  return response.json();
}
function params(extra = {}) { return new URLSearchParams({ tenant_id: state.tenant, amount: state.amount ?? 18400, ...extra }); }
function setStatus(message, error = false) { $("caseStatus").textContent = message; $("caseStatus").classList.toggle("error", error); }
function setBusy(value) { state.busy = value; $("authorityMap").classList.toggle("busy", value); $("page-fleet").setAttribute("aria-busy", String(value)); render(); }
function journey(step) { for (let i = 1; i <= 4; i++) { const el = $(`step-${i}`); el.classList.toggle("done", i < step); if (i === step) el.setAttribute("aria-current", "step"); else el.removeAttribute("aria-current"); } }
function levelFor(agent) {
  const subject = subjects[agent];
  const current = state.audit?.current_authority?.[`${agent}:${subject.capability}`];
  if (current) return current.freezes.length ? "FROZEN" : current.level;
  return state.recovery?.authority_after?.[`${agent}:${subject.capability}`] || state.case?.passports.find((p) => p.agent_id === agent)?.grant.level || "UNISSUED";
}
function render() {
  $("runCase").disabled = state.busy || !state.connected || !!state.case;
  $("runCase").innerHTML = state.busy && !state.case ? 'Executing case… <span>↗</span>' : state.case ? 'Case completed <span>✓</span>' : 'Run this case <span>↗</span>';
  $("amount").disabled = state.busy || state.amount !== null;
  $("recoverCase").disabled = state.busy || !state.case || state.recoveryAttempted;
  $("incidentScenario").disabled = state.busy || state.recoveryAttempted;
  $("recoverCase").innerHTML = state.recovery ? 'Recovery completed <span>✓</span>' : state.busy && state.case ? 'Investigating… <span>↗</span>' : 'Inject & investigate <span>↗</span>';
  $("downloadCase").disabled = state.busy || !state.audit;
  $("freshCase").hidden = state.amount === null;
  $("freshCase").disabled = state.busy;
  $("authorityMap").classList.toggle("active", !!state.case);
  $("authorityMap").classList.toggle("recovered", !!state.recovery);
  const records = state.recovery?.state_after || state.case?.state;
  for (const [key, id] of [["vendor", "vendorState"], ["invoice", "invoiceState"], ["payment", "paymentState"]]) {
    $(id).textContent = records ? pretty(records[key].status) : "Not started";
  }
  for (const [agent, subject] of Object.entries(subjects)) {
    const button = document.querySelector(`[data-agent="${agent}"]`);
    button.setAttribute("aria-pressed", String(state.selected === agent));
    button.classList.toggle("selected", state.selected === agent);
    if (subject.short !== "supervisor") {
      const level = levelFor(agent);
      $(`level-${subject.short}`).textContent = level.replace("EXECUTE_", "");
      button.classList.toggle("earned", level.startsWith("EXECUTE"));
      button.classList.toggle("restricted", ["OBSERVE", "SHADOW", "FROZEN"].includes(level));
    }
  }
  const mapStatus = $("mapStatus");
  mapStatus.textContent = state.recoveryError ? "Recovery unconfirmed" : state.recovery ? "Authority restricted" : state.case ? "3 bounded capabilities" : "Awaiting evidence";
  mapStatus.className = `status ${state.recovery || state.recoveryError ? "warning" : state.case ? "success" : ""}`;
  $("nextAction").textContent = state.recoveryError ? "Inspect the recorded events. Recovery is unconfirmed; do not repeat the investigation or assume authority was restored." : state.recovery ? "Inspect the recovery record and export the evidence. A fresh case uses a separate tenant, not restored authority." : state.case ? "Select a failure context, then investigate. Watch how the scope of recovery changes." : "Run the case to inspect earned grants and three business receipts.";
  if (!state.busy) journey(state.recovery ? 4 : state.case ? 3 : 1);
  renderInspector();
}
function renderInspector() {
  const agent = state.selected, subject = subjects[agent];
  const passport = state.case?.passports.find((p) => p.agent_id === agent);
  const supervisor = subject.short === "supervisor";
  $("inspectorTitle").textContent = subject.title;
  $("inspectorDepartment").textContent = subject.department.toUpperCase();
  $("inspectorCopy").textContent = subject.copy;
  $("inspectorIndex").textContent = subject.short[0].toUpperCase();
  const facts = supervisor ? [
    ["CAPABILITY", subject.capability], ["MODE", state.health?.supervisor_mode || "Not connected"], ["PROMOTION RIGHTS", "None — policy only"],
  ] : [["CAPABILITY", subject.capability], ["CURRENT AUTHORITY", pretty(levelFor(agent))], ["ISSUED CEILING", passport ? money(passport.grant.amount_ceiling) : "No issued grant"]];
  $("inspectorFacts").innerHTML = facts.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
  $("passportProof").innerHTML = supervisor ? `<span>JUDGMENT WITHIN GUARDRAILS</span><p>${state.recovery ? `${state.recovery.tool_categories.length} tool categories used. Deterministic validation checked the proposal before application.` : "Containment must happen before investigation. The model cannot expand execution authority."}</p>` : passport ? `<span>${state.recovery ? "ISSUED PASSPORT / CURRENT GRANT ABOVE" : "SIGNED CAPABILITY PASSPORT"}</span><p>${esc(passport.evidence_window.fresh_count)} fresh fixture observations · ${esc(passport.policy_revision)}. ${state.recovery ? "The historical passport does not override the current demotion." : "Policy and build bound. Inspect the receipt for linked action evidence."}</p>` : '<span>NO GRANT ISSUED</span><p>A registered identity is not permission to execute. Run a case to inspect the signed capability passport.</p>';
  $("inspectReceipt").disabled = supervisor ? !state.recovery : !passport;
  $("inspectReceipt").textContent = supervisor ? "Open investigation ↗" : "Open receipt ↗";
}
async function readAudit() {
  state.audit = await api(`/api/fleet/cases/${state.caseId}/audit?${params()}`);
  renderEvents();
}
const interesting = new Set(["CAPABILITY_PASSPORT_ISSUED", "SANDBOX_MUTATION_COMMITTED", "FLEET_CAPABILITY_FROZEN", "FLEET_INCIDENT_OPENED", "FLEET_DEMOTION_APPLIED", "SANDBOX_ROLLBACK_APPLIED", "FLEET_RECOVERY_COMPLETED", "SUPERVISOR_PROPOSAL_REJECTED"]);
function renderEvents() {
  const events = state.audit.events.filter((event) => interesting.has(event.kind || event.event_type));
  // Ledger serialization uses event_type; keep the full audit export available.
  const visible = events.length ? events : state.audit.events.slice(-18);
  $("eventList").innerHTML = visible.map((event, index) => {
    const kind = event.kind || event.event_type || "EVENT";
    const p = event.payload;
    const description = p.capability_key || (p.before && p.after ? `${p.entity_type}: ${pretty(p.before)} → ${pretty(p.after)}` : p.agent_id || p.demotion_depth || p.scenario || p.incident_id || "Audited decision");
    return `<button class="event-row" data-event="${index}" aria-label="Inspect ${esc(pretty(kind))}"><span class="seq">${String(event.sequence).padStart(2, "0")}</span><span class="event-type">${esc(kind.replace("CAPABILITY_", "").replace("SANDBOX_", "").replace("FLEET_", "").replaceAll("_", " "))}</span><strong>${esc(description)}</strong><span class="event-id">${esc(event.event_id)}</span></button>`;
  }).join("");
  $("eventList").querySelectorAll("button").forEach((el) => el.addEventListener("click", () => showEvidence("Decision record", "An individual event from the case-scoped append-only ledger.", visible[Number(el.dataset.event)])));
}
function showEvidence(title, description, body) {
  $("dialogTitle").textContent = title;
  $("dialogDescription").textContent = description;
  $("dialogBody").textContent = JSON.stringify(body, null, 2);
  $("dialogBody").hidden = false;
  $("confirmRecovery").hidden = true;
  $("evidenceDialog").showModal();
}
async function executeCase(event) {
  event.preventDefault();
  if (state.busy || state.case || !state.connected || !$("caseForm").reportValidity()) return;
  state.amount ??= Number($("amount").value);
  setBusy(true); journey(2); setStatus("Running the actual sandbox workflow. Waiting for server-confirmed grants, mutations and receipts…");
  try {
    state.case = await api(`/api/fleet/cases/${state.caseId}?${params()}`, { method: "POST" });
    await readAudit();
    setStatus(`${money(state.amount)} released in the sandbox. ${state.case.receipts.length} linked receipts; ledger ${state.audit.ledger_integrity ? "verified" : "FAILED verification"}. No real funds moved.`, !state.audit.ledger_integrity);
  } catch (error) { setStatus(`Case request: ${error.message}. Retry only with the same amount and case ID; execution may have completed on the server.`, true); }
  finally { setBusy(false); }
}
async function recover() {
  if (state.busy || !state.case || state.recoveryAttempted) return;
  state.recoveryAttempted = true;
  setBusy(true); journey(3);
  setStatus("Recovery requested. Waiting for server evidence of containment, investigation and compensation; no outcome is assumed yet.");
  try {
    state.recovery = await api(`/api/recovery/cases/${state.caseId}?${params({ scenario: $("incidentScenario").value })}`, { method: "POST" });
    const r = state.recovery;
    $("supervisorStatus").textContent = `${r.tool_categories.length} tool categories · ${pretty(r.proposal.demotion_depth)}`;
    $("recoveryResult").hidden = false;
    $("recoveryResult").innerHTML = `<div><span class="eyebrow">${esc(r.reasoner_mode)} / ${r.freeze_preceded_supervision ? "FREEZE PRECEDED INVESTIGATION" : "ORDERING CHECK FAILED"}</span><h3>${esc(pretty(r.proposal.demotion_depth))} → ${esc(pretty(r.proposal.target_level))}</h3><p>${esc(r.proposal.narrative)}</p><button id="openRecovery" class="text-button">Inspect the complete investigation ↗</button></div><div class="recovery-metrics"><div><strong>${r.proposal.affected_capability_keys.length}</strong><small>capabilities restricted</small></div><div><strong>${r.rollback_results.length}</strong><small>sandbox compensations</small></div><div><strong>${r.escalation_action_ids.length}</strong><small>escalations recorded<br>(bank export is a fixture)</small></div></div>`;
    $("openRecovery").addEventListener("click", () => showEvidence("Supervisor investigation", "Bounded proposal, tool evidence, applied demotion and sandbox compensation.", r));
    await readAudit();
    setStatus(`Recovery verified: ${r.rollback_results.length} compensations. ${r.freeze_preceded_supervision ? "Containment preceded supervision." : "WARNING: containment ordering failed."} Current restrictions remain enforced.`, !r.freeze_preceded_supervision || !r.ledger_integrity);
  } catch (error) {
    state.recoveryError = true;
    setStatus(`Recovery request: ${error.message}. Do not assume recovery succeeded. Containment may remain active; automatic retry is disabled to avoid duplicate paid investigations.`, true);
    try { await readAudit(); } catch { /* Preserve the original failure. */ }
  } finally { setBusy(false); }
}
function requestRecovery() {
  if (state.busy || !state.case || state.recoveryAttempted) return;
  if (state.health?.supervisor_mode === "GEMINI_ADK") {
    showEvidence("Run a live Supervisor investigation?", "This invokes Gemini through ADK and may consume cloud credits. It changes only this synthetic case. One investigation is allowed per case in this interface; token usage is not yet metered here.", {});
    $("dialogBody").hidden = true; $("confirmRecovery").hidden = false;
  } else { recover(); }
}
function download(body, filename) { const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" })); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
function route(path, push = false) {
  const page = ({ "/": "fleet", "/proof": "proof", "/conformance": "proof", "/platform": "platform", "/limitations": "limitations" })[path] || "fleet";
  document.querySelectorAll(".page").forEach((el) => { el.hidden = el.id !== `page-${page}`; });
  document.querySelectorAll(".mast [data-page]").forEach((link) => { if (link.dataset.page === page) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
  if (push) { history.pushState({}, "", path); $("main").focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); }
  document.title = `TENURE — ${{ fleet: "Fleet control room", proof: "Proof lab", platform: "Platform evidence", limitations: "Scope & limits" }[page]}`;
}
function renderComparison() {
  if (!state.gauntlet?.summary) return;
  const family = $("familyFilter").value;
  const summary = family === "all" ? state.gauntlet.summary : state.gauntlet.slices[family];
  const names = { static_broad: ["Static broad credentials", "Same validity checks; no adaptive revocation"], human_review: ["Permanent human approval", "Modeled queue; no human trial"], tenure: ["TENURE", "Evidence-backed, scoped, revocable"] };
  $("comparisonRows").innerHTML = Object.entries(names).map(([mode, [name, note]]) => {
    const r = summary[mode];
    return `<tr class="${mode === "tenure" ? "highlight" : ""}"><td>${name}<small>${note}</small></td><td>${r.safe_autonomous} / ${r.safe_opportunities}</td><td class="${r.unsafe_authorized ? "unsafe" : ""}">${r.unsafe_authorized} / ${r.unsafe_opportunities}</td><td>${r.deferred}</td><td>${r.errors}</td></tr>`;
  }).join("");
  $("sliceDescription").textContent = `${summary.tenure.cases} parameterized cases. Safe and unsafe denominators are shown separately; 0 / 0 means no opportunities in that slice.`;
}
function renderGauntlet(report) {
  state.gauntlet = report;
  if (!report.summary) { $("gauntletSummary").textContent = report.message || "No saved benchmark is available."; $("downloadGauntlet").hidden = true; return; }
  const r = report.summary.tenure;
  $("gauntletSummary").innerHTML = `<div><strong>${r.cases}</strong><span>SYNTHETIC CASES</span></div><div><strong>${Object.keys(report.family_weights).length}</strong><span>SCENARIO FAMILIES</span></div><div><strong>${report.model_calls}</strong><span>MODEL CALLS</span></div><p>Saved local run · ${esc(report.generated_at.slice(0, 10))}. These are control decisions, not a claim of live agent accuracy.</p>`;
  $("familyFilter").innerHTML = '<option value="all">All families</option>' + Object.entries(report.family_weights).map(([family, count]) => `<option value="${esc(family)}">${esc(pretty(family))} (${count})</option>`).join("");
  renderComparison();
  $("methodologyBody").innerHTML = Object.entries(report.definitions).map(([mode, definition]) => `<p><strong>${esc(pretty(mode))}.</strong> ${esc(definition)}</p>`).join("") + report.limitations.map((text) => `<p>${esc(text)}</p>`).join("") + `<p>Provider usage in this local run: ${report.provider_tokens} tokens; ${money(report.provider_cost_inr)}. Live Gemini sample: ${esc(report.live_model_sample.status)}.</p><p>TENURE descriptive Wilson 95% intervals: safe autonomy ${esc(JSON.stringify(r.safe_autonomy_wilson95))}; unsafe authorization ${esc(JSON.stringify(r.unsafe_authorization_wilson95))}. These are not production guarantees.</p><pre>Seed: ${report.seed}\nCorpus SHA-256: ${esc(report.corpus_sha256)}\nReproduce: python -m tenure.gauntlet --output src/tenure/static/gauntlet-report.json</pre>`;
  $("gauntletFailures").textContent = report.failures.length ? `${report.failures.length} failures recorded. Inspect the full report; they have not been removed.` : "No TENURE oracle or invariant failures in this saved synthetic run.";
  $("gauntletFailures").className = "field-help";
  const c = report.concurrency;
  $("concurrencyProof").textContent = c.passed ? `${c.completed} callers completed; ${c.mutations} mutations and ${c.receipts} receipts. Shared-memory, single-case test—not cloud load testing.` : "Concurrency proof has not passed. Inspect the report.";
}
async function runPair() {
  $("runPair").disabled = true; $("runPair").textContent = "Comparing fixed evidence…";
  try {
    state.pair = await api(`/api/authority/proof?${new URLSearchParams({ tenant_id: state.tenant })}`, { method: "POST" });
    const p = state.pair;
    $("pairResult").innerHTML = [ ["Grounded agent", p.grounded_agent], ["Right answer / wrong reason", p.rawr_agent] ].map(([name, agent]) => `<div class="pair-row"><div>${name}<small>${Math.round(agent.evidence_window.outcome_accuracy * 100)}% outcome · ${Math.round(agent.evidence_window.controlling_clause_accuracy * 100)}% controlling clause</small></div><strong>${esc(agent.applied_level.replace("EXECUTE_", ""))}</strong></div>`).join("") + `<p>Ceiling expansion to ${money(p.stress_promotion.proposed_ceiling)}: ${esc(pretty(p.stress_promotion.decision))}. Existing ceiling preserved at ${money(p.stress_promotion.applied_ceiling)}.</p><button id="inspectPair" class="text-button">Inspect policy checks & counterfactual replay ↗</button>`;
    $("inspectPair").addEventListener("click", () => showEvidence("Same outcome, different authority", "A fixed demonstration corpus; separate from the 500-case evaluation.", p));
  } catch (error) { $("pairResult").textContent = `Comparison failed: ${error.message}`; }
  finally { $("runPair").disabled = false; $("runPair").textContent = "Run comparison again ↗"; }
}
function renderPlatform(p) {
  const live = !!p.cloud_run.service;
  const cards = [
    ["THIS SERVICE", live ? "Cloud Run" : "Local runtime", live ? `${p.cloud_run.service} / ${p.cloud_run.revision}` : "This interface is running locally. It is not the deployed cloud service."],
    ["SUPERVISION", state.health?.supervisor_mode === "GEMINI_ADK" ? "Gemini + ADK" : "Deterministic fixture", state.health?.supervisor_mode === "GEMINI_ADK" ? "A recovery button invokes the live Supervisor. Resource configuration alone is not evidence that a new call completed." : "No paid model calls from local recovery. Use the cloud runtime for live Supervisor investigation."],
    ["EXECUTION BOUNDARY", "TENURE application gate", p.agent_gateway.resource ? "A native gateway resource is configured; inspect its verification separately." : "Native Google Agent Gateway is not deployed. The TENURE guard enforces capability state at mutation."],
    ["OPERATING FLEET", "Four registered roles", "Three operating workflows are fixture-driven. Distinct runtime identities are not evidence of three live reasoning calls."],
    ["PERSISTENCE", state.health?.fleet_persistence === "firestore" ? "Firestore" : "Shared memory", "Current capability state governs new mutations. Business records and audit events are separate commits."],
    ["COST BOUNDARY", "Explicit model invocation", "Local evaluation uses zero provider tokens. Cloud budgets alert; they do not hard-stop spending. Per-invocation token metering remains pending."],
  ];
  $("platformCards").innerHTML = cards.map(([label, title, copy]) => `<article><span class="eyebrow">${label}</span><h2>${esc(title)}</h2><p>${esc(copy)}</p></article>`).join("");
  $("platformManifest").textContent = JSON.stringify(p, null, 2);
}
document.querySelectorAll("[data-page]").forEach((link) => link.addEventListener("click", (event) => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); route(new URL(link.href).pathname, true); }));
document.querySelectorAll("[data-agent]").forEach((button) => button.addEventListener("click", () => { state.selected = button.dataset.agent; render(); }));
$("caseForm").addEventListener("submit", executeCase);
$("recoverCase").addEventListener("click", requestRecovery);
$("confirmRecovery").addEventListener("click", () => { $("evidenceDialog").close(); recover(); });
$("freshCase").addEventListener("click", () => { if (!state.busy) { newWorkspace(); $("amount").focus(); } });
$("closeDialog").addEventListener("click", () => $("evidenceDialog").close());
$("inspectReceipt").addEventListener("click", () => {
  if (state.selected === "supervisor-agent") showEvidence("Supervisor investigation", "The applied proposal and its evidence.", state.recovery);
  else showEvidence("Capability receipt & passport", "The passport is historical. Current restrictions can be narrower after an incident.", { current_authority: levelFor(state.selected), passport: state.case.passports.find((p) => p.agent_id === state.selected), receipt: state.case.receipts.find((r) => r.agent_id === state.selected) });
});
$("inspectIdentity").addEventListener("click", () => showEvidence("Agent registration", "Registry configuration is not a live native-identity authorization probe.", state.registry.find((r) => r.agent_id === state.selected) || { status: "Registration unavailable" }));
$("downloadCase").addEventListener("click", () => download({ provenance: { operating_mode: "DETERMINISTIC_FIXTURES", supervisor_mode: state.health.supervisor_mode, persistence: state.health.fleet_persistence }, case: state.case, recovery: state.recovery, audit: state.audit }, `tenure-${state.caseId}.json`));
$("runPair").addEventListener("click", runPair);
$("runIdentity").addEventListener("click", runIdentityPair);
$("inspectIdentityProof").addEventListener("click", () => showEvidence("Native identity pair", "Two runtime-scoped reads of one pinned memory. Contents and credentials are never included.", identityReceipt));
$("familyFilter").addEventListener("change", renderComparison);
window.addEventListener("popstate", () => route(location.pathname));
async function initialize() {
  newWorkspace(); route(location.pathname);
  const results = await Promise.allSettled([api("/api/health"), api("/api/fleet/registry"), api("/api/platform"), api("/api/gauntlet"), api("/api/proofs/identity")]);
  if (results[0].status === "fulfilled") {
    state.health = results[0].value; state.connected = true;
    const live = state.health.supervisor_mode === "GEMINI_ADK";
    const cloud = state.health.mode === "GOOGLE_CLOUD_LIVE";
    $("environment").textContent = live ? (cloud ? "CLOUD / LIVE GEMINI" : "LOCAL / LIVE GEMINI") : "LOCAL / FIXTURE MODE";
    $("runtimeTruth").textContent = live ? `${cloud ? "Cloud" : "Local"} Supervisor: Gemini + ADK. Operating workflows: deterministic fixtures. Synthetic records only.` : "Local sandbox · fixture-driven operating agents and Supervisor · no model spend.";
    $("recoveryHint").textContent = live ? "Invokes one live Gemini Supervisor investigation after confirmation. No real funds move." : "Local deterministic recovery · zero model calls. Set the local Supervisor provider to Gemini for a live investigation.";
  } else { $("connectionError").hidden = false; $("connectionError").textContent = "The runtime is unavailable. No execution is enabled. Refresh after the server is healthy."; $("environment").textContent = "DISCONNECTED"; $("runtimeTruth").textContent = "Runtime unavailable. No live claims."; }
  if (results[1].status === "fulfilled") state.registry = results[1].value.agents;
  if (results[2].status === "fulfilled") renderPlatform(results[2].value); else $("platformCards").textContent = "Platform manifest unavailable; no resource claims are shown.";
  if (results[3].status === "fulfilled") renderGauntlet(results[3].value); else { $("gauntletSummary").textContent = "Saved benchmark unavailable. No results are assumed."; $("downloadGauntlet").hidden = true; }
  if (results[4].status === "fulfilled") renderIdentityStatus(results[4].value); else $("identityAvailability").textContent = "Native identity proof endpoint unavailable. No local substitute is used.";
  render();
}
initialize();
