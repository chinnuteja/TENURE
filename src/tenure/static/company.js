const tenantId = "acme-robotics";
const caseId = `sso-${Math.random().toString(16).slice(2, 10)}`;
const reason = {
  injected_slack_instruction: [
    "The Slack message was not Acme.",
    "Every capability derived from that instruction is frozen before the supervisor runs. The issue, pull request, deal, and draft invoice can be undone. A production deploy and an email already sent cannot.",
  ],
  bad_merge: [
    "The merge itself was wrong.",
    "Only github.merge_pr is demoted. The issue, the deal, and the invoice keep the authority they earned. The production deploy is escalated.",
  ],
  billing_mismatch: [
    "The deal and the invoice disagree.",
    "Attio and Stripe are contained together. GitHub and Linear stay executable. The invoice email is escalated.",
  ],
  pricing_policy_changed: [
    "The pricing policy changed underneath.",
    "Policy drift is not a local bug. The whole operating set returns to observe. What a customer already saw is escalated.",
  ],
};

const TOOL_DESCRIPTIONS = {
  read_incident_ledger: "Read the signed incident and every ledger event for this case.",
  read_agent_registry: "Read the registered agent identities and their declared capabilities.",
  read_supervisor_memory: "Retrieve verified lessons from Supervisor Memory about this failure class.",
  read_trace: "Read every event linked to this incident's trace id.",
  traverse_dependency_graph: "Walk downstream capabilities that depend on the compromised one.",
  request_compensating_rollbacks: "Request rollback for the exact reversible actions in scope — nothing more.",
  file_irreversible_escalation: "File escalation for the exact irreversible consequences in scope.",
};

const $ = (id) => document.getElementById(id);
let lastCase = null;

function setPipelineStage(stage) {
  const order = ["detect", "freeze", "investigate", "validate", "recover"];
  const upto = order.indexOf(stage);
  document.querySelectorAll(".pipeline li").forEach((item) => {
    item.classList.toggle("active", order.indexOf(item.dataset.stage) <= upto);
  });
}

function paintPromptScreen(screen) {
  const panel = $("promptScreen");
  if (!screen) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("promptMessage").textContent = `"${screen.message}"`;
  const badge = $("promptVerdict");
  badge.textContent = screen.allowed ? "ALLOWED" : "BLOCKED";
  badge.className = `tiny-tag ${screen.allowed ? "allowed" : "blocked"}`;
  $("promptMatches").textContent = screen.matched_phrases.length
    ? `${screen.provider} matched: ${screen.matched_phrases.join(" · ")}`
    : `${screen.provider} found no risk phrases.`;
  $("promptDisclosure").textContent = screen.provider === "local_heuristic_v1"
    ? "This is a local phrase-match stand-in, not Google Model Armor — zero network calls, zero cost, real evaluation of the text above. Cloud mode swaps in the actual regional Model Armor endpoint behind the same interface."
    : `Screened by ${screen.provider}, Google's regional Model Armor endpoint — a live cloud call, not a local stand-in.`;
}

function paintToolTrace(trace, reasonerMode, modelCalls) {
  const panel = $("toolTrace");
  if (!trace?.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("reasonerMode").textContent = modelCalls
    ? `Reasoner: ${reasonerMode} — this run made ${modelCalls} live Gemini call through Google ADK.`
    : `Reasoner: ${reasonerMode} — deterministic fixture, 0 live model calls. The tool sequence below is identical in shape to what the live Gemini + ADK Supervisor produces.`;
  $("toolTraceList").innerHTML = trace.map((item, index) => `
    <li>
      <span class="n">${String(index + 1).padStart(2, "0")}</span>
      <span class="cat">${item.category}</span>
      <p>${TOOL_DESCRIPTIONS[item.tool] || item.tool}<br><code>${item.tool}(${Object.entries(item.detail).slice(0, 2).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</code></p>
    </li>`).join("");
}

function showError(error) {
  const node = $("error");
  node.hidden = false;
  node.textContent = error.message || String(error);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function paintChain(state, reverted) {
  for (const item of document.querySelectorAll("#chain li")) {
    const record = state?.[item.dataset.step];
    item.classList.remove("done", "reverted");
    const label = item.querySelector("em");
    const id = item.querySelector("code");
    if (!record) {
      label.textContent = "WAITING";
      id.hidden = true;
      continue;
    }
    const undone = reverted?.has(record.entity_type);
    item.classList.add(undone ? "reverted" : "done");
    label.textContent = undone || record.status;
    id.hidden = false;
    id.textContent = record.entity_id;
  }
}

function paintAuthority(rows, probes) {
  const extras = (probes || []).filter((probe) =>
    rows.some((row) => row.earned && row.capability === probe.capability));
  const denied = new Map(
    (probes || []).filter((probe) => !extras.includes(probe)).map((probe) => [probe.capability, probe]),
  );
  const body = rows.map((row) => {
    const probe = row.earned ? null : denied.get(row.capability);
    const why = probe
      ? `${probe.gateway_decision} — ${probe.ask}`
      : row.earned
        ? (row.level === "EXECUTE_BOUNDED" ? "Earned from verified work" : "Earned, then reduced")
        : "Registered. Never earned.";
    const klass = probe ? "denied" : row.earned && row.level !== "EXECUTE_BOUNDED" ? "held" : "";
    return `<tr class="${klass}"><td>${row.capability}</td><td>${row.tool}</td><td>${row.level}</td><td>${why}</td></tr>`;
  });
  for (const probe of extras) {
    body.push(`<tr class="denied"><td>${probe.capability}</td><td>${probe.tool}</td><td>${probe.gateway_decision}</td><td>${probe.ask}</td></tr>`);
  }
  $("authorityTable").querySelector("tbody").innerHTML = body.join("");
}

function paintReceipts(receipts) {
  const list = $("receipts");
  if (!receipts?.length) {
    list.innerHTML = `<p class="empty-record">Receipts appear after the case runs.</p>`;
    return;
  }
  list.innerHTML = receipts.map((receipt, index) => `
    <button class="event-row" type="button" data-index="${index}">
      <span class="seq">${String(index + 1).padStart(2, "0")}</span>
      <span class="event-type">${receipt.tool}</span>
      <strong>${receipt.title}</strong>
      <span class="event-id">${receipt.passport_id || ""}</span>
    </button>`).join("");
  list.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => openReceipt(receipts[Number(button.dataset.index)]));
  });
}

function openReceipt(receipt) {
  $("receiptTitle").textContent = receipt.title;
  $("receiptBody").textContent = JSON.stringify(receipt, null, 2);
  $("receiptDialog").showModal();
}

$("closeReceipt").addEventListener("click", () => $("receiptDialog").close());

function closeGuide() {
  $("guide").close();
  sessionStorage.setItem("tenure-company-guide", "seen");
}
$("closeGuide").addEventListener("click", closeGuide);
$("startGuide").addEventListener("click", () => {
  closeGuide();
  $("runButton").focus();
});
$("openGuide").addEventListener("click", () => $("guide").showModal());
if (!sessionStorage.getItem("tenure-company-guide")) $("guide").showModal();

$("scenario").addEventListener("change", () => {
  const [title, copy] = reason[$("scenario").value];
  $("breakTitle").textContent = title;
  $("breakCopy").textContent = copy;
});

$("runForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("error").hidden = true;
  const button = $("runButton");
  button.disabled = true;
  try {
    const amount = $("amount").value;
    lastCase = await api(
      `/api/company/cases/${caseId}?tenant_id=${tenantId}&amount=${amount}`,
      { method: "POST" },
    );
    paintChain(lastCase.state);
    paintAuthority(lastCase.authority, lastCase.unearned_attempts);
    paintReceipts(lastCase.receipts);
    $("caseMeta").textContent = `${lastCase.receipts.length} passports used · ${lastCase.unearned_attempts.length} actions refused · ledger ${lastCase.ledger_integrity ? "intact" : "BROKEN"}`;
    $("breakPanel").hidden = false;
    $("breakResult").hidden = true;
    $("promptScreen").hidden = true;
    $("toolTrace").hidden = true;
    setPipelineStage("");
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

$("breakButton").addEventListener("click", async () => {
  $("error").hidden = true;
  const button = $("breakButton");
  button.disabled = true;
  try {
    const scenario = $("scenario").value;
    const amount = $("amount").value;
    const result = await api(
      `/api/company/recovery/cases/${caseId}?tenant_id=${tenantId}&amount=${amount}&scenario=${scenario}`,
      { method: "POST" },
    );
    const reverted = new Map(result.rollback_results.map((item) => [item.entity_type, item.after]));
    paintChain(result.state_after, reverted);
    paintAuthority(result.authority, lastCase?.unearned_attempts);
    paintPromptScreen(result.prompt_screen);
    paintToolTrace(result.tool_trace, result.reasoner_mode, result.model_calls);
    setPipelineStage(result.escalations.length || result.rollback_results.length ? "recover" : "validate");
    const box = $("breakResult");
    box.hidden = false;
    const order = result.freeze_preceded_supervision
      ? "Freeze was written before the first supervisor tool call."
      : "Containment order failed. Do not trust this run.";
    box.innerHTML = `
      <div>
        <p class="break-order">${order}</p>
        <h3>${result.proposal.demotion_depth} → ${result.proposal.target_level}</h3>
        <p>${result.proposal.narrative}</p>
      </div>
      <div class="recovery-metrics">
        <div><strong>${result.rollback_results.length}</strong><small>Reversible actions undone in the sandbox</small></div>
        <div><strong>${result.escalations.length}</strong><small>Irreversible consequences escalated</small></div>
        <div><strong>${result.authority.filter((row) => row.earned && row.level !== "EXECUTE_BOUNDED").length}</strong><small>Earned capabilities no longer executable</small></div>
        <ul class="escalation-list">${result.escalations.map((item) => `<li>${item.description}</li>`).join("")}</ul>
      </div>`;
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});
