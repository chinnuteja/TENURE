const levels = ["OBSERVE", "SHADOW", "EXECUTE_BOUNDED", "EXECUTE_FULL"];
const levelLabels = ["Observe", "Shadow", "Execute / bounded", "Execute / full"];
const stepStories = [
  ["Authority must be earned.", "The agent begins with proposal rights only. Advance the evidence chain and watch the boundary—not the model—decide what it may do."],
  ["Evidence enters the chain.", "Outcome and controlling reason are recorded as separate facts. Neither is allowed to impersonate the other."],
  ["Confidence is accumulating.", "The grant remains SHADOW until the deterministic minimum sample and both evidence rates hold."],
  ["Bounded authority earned.", "The capability—not the whole agent—may now execute inside a mechanically enforced ₹50,000 ceiling."],
  ["Correct answer. Wrong reason.", "RAWR is visible now: the outcome passed, grounding failed, and TENURE awarded exactly zero additional trust."],
  ["The boundary permits one action.", "A known vendor and ₹40,000 sit inside the earned grant. A short-lived scoped token exists only for this action."],
  ["The agent is compromised.", "It attempts ₹10,00,000. The model complies with the injection; the gateway does not."],
  ["Safety before judgment.", "The capability is frozen immediately. No model call stands between the failure and containment."],
  ["Incident resolved.", "The Supervisor Agent mapped six exposed actions, requested four rollbacks, and escalated two irreversible consequences."],
];
const eventLabels = {
  SCENARIO_STARTED: ["Scenario initialized", "LOCAL"],
  VERIFICATION_RECORDED: ["Dual-gate evidence accepted", "EVIDENCE"],
  CAPABILITY_PROMOTED: ["Bounded authority earned", "PROMOTE"],
  RAWR_BLOCKED: ["Right answer / wrong reason blocked", "RAWR"],
  ACTION_TRUST_RECEIPT: ["Gateway decision receipted", "GATEWAY"],
  MODEL_ARMOR_SCREENED: ["Model Armor screened prompt", "ARMOR"],
  INCIDENT_ENVELOPE_PUBLISHED: ["Signed incident envelope published", "PUB/SUB"],
  CAPABILITY_FROZEN: ["Capability frozen immediately", "CONTAIN"],
  SUPERVISOR_INVESTIGATION_COMPLETED: ["Blast radius investigation complete", "AGENT"],
  COMPENSATING_ROLLBACK_REQUESTED: ["Compensating rollback requested", "ROLLBACK"],
  HUMAN_ESCALATION_FILED: ["Irreversible effects escalated", "ESCALATE"],
  SUPERVISOR_DEMOTION_APPLIED: ["Policy-bounded demotion applied", "DEMOTE"],
};
const metricDefinitions = [
  ["rawr_blocks", "RAWR blocks"],
  ["model_armor_blocks", "Armor blocks"],
  ["unsafe_actions_executed", "Unsafe executed"],
  ["human_approvals_avoided", "Approvals avoided"],
  ["containment_latency_ms", "Containment / ms"],
  ["supervisor_latency_ms", "Supervisor / ms"],
  ["rollbacks_requested", "Rollbacks"],
  ["escalations_filed", "Escalations"],
];

const $ = (id) => document.getElementById(id);
const money = (value) => value == null ? "—" : `₹${Number(value).toLocaleString("en-IN")}`;
const shown = (value) => value == null ? "—" : value;
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function api(path, method = "GET") {
  const response = await fetch(path, { method });
  if (!response.ok) throw new Error(`TENURE API ${response.status}`);
  return response.json();
}

function setBusy(busy) {
  document.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
}

function render(data) {
  renderStory(data);
  renderAuthority(data);
  renderEvidence(data);
  renderTopology(data);
  renderSupervisor(data);
  renderTimeline(data.events);
  renderReceipt(data.latest_receipt);
  renderMetrics(data.metrics);
  renderIntegrations(data.integrations);
  $("cloudTruth").textContent = data.cloud_truth.toUpperCase();
  $("integrityLabel").textContent = data.ledger_integrity ? "CHAIN / VERIFIED" : "CHAIN / FAILED";
}

function renderStory(data) {
  const [heading, copy] = stepStories[data.step] || stepStories[0];
  $("scenarioHeading").innerHTML = heading.replace(/earned\.|resolved\.|judgment\.|compromised\./i, (match) => `<em>${match}</em>`);
  $("scenarioCopy").textContent = copy;
  $("progressBar").style.width = `${(data.step / data.step_count) * 100}%`;
  $("progressLabel").textContent = `${String(data.step).padStart(2, "0")}—${String(data.step_count).padStart(2, "0")} / ${data.complete ? "RESOLVED" : "LIVE"}`;
  $("nextStepLabel").textContent = data.complete ? "END" : String(data.step + 1).padStart(2, "0");
  $("advanceButton").disabled = data.complete;
}

function renderAuthority(data) {
  const grant = data.grant;
  const index = levels.indexOf(grant.level);
  $("authorityLevels").innerHTML = levels.map((level, position) => `
    <li class="${position < index ? "reached" : ""} ${position === index ? "active" : ""}">
      <span>0${position}</span><strong>${levelLabels[position]}</strong><i></i>
    </li>`).join("");
  $("ceiling").textContent = money(grant.amount_ceiling);
  $("levelNumber").textContent = `0${index}`;
  $("levelName").textContent = grant.level.replaceAll("_", " / ");
  $("grantBadge").textContent = grant.frozen ? "FROZEN" : grant.level;
  $("grantBadge").className = `signal-tag ${grant.frozen ? "danger" : ""}`;
  $("authorityArc").style.strokeDasharray = `${[8, 28, 62, 100][index]} ${100 - [8, 28, 62, 100][index]}`;
  $("authorityArc").style.stroke = grant.frozen ? "var(--red)" : "var(--acid)";
  $("containmentRing").classList.toggle("frozen", grant.frozen);
  $("freezeState").className = grant.frozen ? "frozen" : "";
  $("freezeState").innerHTML = `<i></i> ${grant.frozen ? "CAPABILITY FROZEN" : "GATEWAY ACTIVE"}`;
  $("evidenceCount").textContent = `${data.metrics.verified_tasks} verified / ${data.metrics.verified_tasks} grounded`;
}

function renderEvidence(data) {
  $("rawrCount").textContent = data.metrics.rawr_blocks;
  $("validCount").textContent = data.metrics.verified_tasks;
  document.querySelector(".matrix-cell.lucky").classList.toggle("active", data.metrics.rawr_blocks > 0);
  document.querySelector(".matrix-cell.earned").classList.toggle("active", data.metrics.verified_tasks > 0);
  $("matrixNote").textContent = data.metrics.rawr_blocks
    ? "One lucky answer was quarantined from the promotion evidence. Outcome correctness did not launder invalid reasoning."
    : "Promotion requires both coordinates to hold. A lucky answer cannot move the grant.";
}

function renderTopology(data) {
  const incident = data.incident;
  const resolved = incident?.resolution;
  $("topologyHeading").textContent = incident ? (resolved ? "Six actions under examination." : "Capability isolated.") : "No exposed actions.";
  $("incidentTag").textContent = resolved ? "RESOLVED" : incident ? "CONTAINED" : "STANDBY";
  $("incidentTag").className = `signal-tag ${incident && !resolved ? "danger" : incident ? "" : "quiet"}`;
  const actionNodes = [
    [555, 60, "A/01", "rollback"], [665, 110, "A/02", "rollback"],
    [700, 215, "A/03", "rollback"], [625, 300, "A/04", "rollback"],
    [475, 300, "A/05", "escalate"], [430, 100, "A/06", "escalate"],
  ];
  const base = `
    <path class="link" d="M105 175H270"/><path class="link ${incident ? "hot" : ""}" d="M270 175H430"/>
    <g class="node root"><circle cx="105" cy="175" r="39"/><text x="105" y="171">AGENT</text><text x="105" y="185">SUBJECT</text></g>
    <g class="node root"><circle cx="270" cy="175" r="52"/><text x="270" y="171">INVOICE</text><text x="270" y="185">APPROVE</text></g>`;
  const actions = incident ? actionNodes.map(([x, y, label, type], position) => `
    <path class="link hot" d="M322 175L${x - 17} ${y}"/>
    <g class="node ${type} pulse" style="animation-delay:${position * 80}ms"><circle cx="${x}" cy="${y}" r="22"/><text x="${x}" y="${y + 3}">${label}</text></g>`).join("") : `
    <g class="node"><circle cx="515" cy="175" r="17"/><text x="515" y="205">NO INCIDENT</text></g>`;
  $("topologySvg").innerHTML = base + actions;
}

function renderSupervisor(data) {
  const incident = data.incident;
  const resolution = incident?.resolution;
  if (!resolution) {
    $("supervisorBody").className = "supervisor-empty";
    $("supervisorBody").innerHTML = `<span>${incident ? "02" : "01"}</span><p>${incident ? "Containment is active. The Supervisor Agent may now inspect evidence without holding promotion authority." : "Deterministic containment has not opened an incident."}</p>`;
    return;
  }
  $("supervisorBody").className = "supervisor-resolution";
  $("supervisorBody").innerHTML = `
    <div class="resolution-stat"><span>LASTING DEMOTION</span><strong>${resolution.target_level}</strong></div>
    <div class="resolution-stat"><span>BLAST RADIUS</span><strong>${resolution.affected_action_ids.length}</strong></div>
    <div class="resolution-stat"><span>ROLLBACK / ESCALATE</span><strong>${resolution.rollback_action_ids.length} / ${resolution.escalation_action_ids.length}</strong></div>
    <p class="resolution-narrative">${resolution.narrative}</p>`;
}

function renderTimeline(events) {
  $("eventCount").textContent = `${String(events.length).padStart(3, "0")} EVENTS`;
  $("timeline").innerHTML = [...events].reverse().map((event) => {
    const [label, plane] = eventLabels[event.event_type] || [event.event_type.replaceAll("_", " "), "EVENT"];
    const severity = event.event_type.includes("FROZEN") || event.event_type.includes("ESCALATION") ? "danger" : event.event_type.includes("RAWR") || event.event_type.includes("ROLLBACK") ? "warn" : "";
    const time = new Date(event.occurred_at).toISOString().slice(11, 19);
    return `<div class="event-row ${severity}"><span class="seq">#${String(event.sequence).padStart(3, "0")}</span><span class="hash">${time}<br>${event.event_hash.slice(0, 12)}</span><strong>${label}</strong><span class="plane">${plane}</span></div>`;
  }).join("");
}

function renderReceipt(receipt) {
  const badge = $("receiptDecision");
  if (!receipt) {
    badge.textContent = "WAITING";
    badge.className = "signal-tag quiet";
    $("receipt").innerHTML = `<div><dt>State</dt><dd>No gateway decision yet.</dd></div><div><dt>Principle</dt><dd>Authority is minted per action.</dd></div>`;
    return;
  }
  badge.textContent = receipt.gateway_decision;
  badge.className = `signal-tag ${receipt.gateway_decision.startsWith("DENY") ? "danger" : ""}`;
  const fields = [
    ["Action", receipt.action_id], ["Decision", receipt.gateway_decision],
    ["Grant", receipt.grant_level], ["Ceiling", money(receipt.scope_ceiling)],
    ["Policy", receipt.controlling_policy], ["Credential", receipt.credential_fingerprint || "NOT MINTED"],
    ["Capability", receipt.capability], ["Trace", receipt.trace_id],
  ];
  $("receipt").innerHTML = fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
}

function renderMetrics(metrics) {
  $("metricTape").innerHTML = metricDefinitions.map(([key, label], index) => `<div class="metric ${index < 2 ? "critical" : ""}"><span>${label}</span><strong>${shown(metrics[key])}</strong></div>`).join("");
}

function renderIntegrations(integrations) {
  $("integrations").innerHTML = Object.entries(integrations).map(([name, status]) => {
    const active = status === "ACTIVE" || status.startsWith("READY");
    return `<span class="${active ? "active" : "waiting"}">${name.replaceAll("_", " ")} / ${status.replaceAll("_", " ")}</span>`;
  }).join("");
}

function buildTicks() {
  const lines = [];
  for (let angle = 0; angle < 360; angle += 10) {
    const radians = (angle - 90) * Math.PI / 180;
    const inner = angle % 30 === 0 ? 188 : 194;
    const outer = 202;
    lines.push(`<line x1="${310 + Math.cos(radians) * inner}" y1="${215 + Math.sin(radians) * inner}" x2="${310 + Math.cos(radians) * outer}" y2="${215 + Math.sin(radians) * outer}"/>`);
  }
  $("apertureTicks").innerHTML = lines.join("");
}

async function runSequence() {
  setBusy(true);
  try {
    render(await api("/api/scenario/reset", "POST"));
    await pause(250);
    for (let index = 0; index < 8; index += 1) {
      render(await api("/api/scenario/advance", "POST"));
      await pause(index === 7 ? 0 : 420);
    }
  } catch (error) {
    $("scenarioCopy").textContent = error.message;
  } finally {
    setBusy(false);
  }
}

async function single(path) {
  setBusy(true);
  try { render(await api(path, "POST")); }
  catch (error) { $("scenarioCopy").textContent = error.message; }
  finally { setBusy(false); }
}

function updateClock() {
  $("clock").textContent = `${new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata" })} IST`;
}

$("runButton").addEventListener("click", runSequence);
$("advanceButton").addEventListener("click", () => single("/api/scenario/advance"));
$("resetButton").addEventListener("click", () => single("/api/scenario/reset"));
buildTicks();
updateClock();
setInterval(updateClock, 1000);
api("/api/scenario").then(render).catch((error) => { $("scenarioCopy").textContent = error.message; });
