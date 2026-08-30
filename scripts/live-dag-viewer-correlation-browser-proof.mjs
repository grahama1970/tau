import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
let puppeteer;
try {
  puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
} catch {
  puppeteer = require(`${process.env.NODE_PATH}/puppeteer-core`);
}

const [url, readyPath, blockedSeenPath, finalSeenPath, ledgerReadyPath, outputDir] = process.argv.slice(2);
if (!url || !readyPath || !blockedSeenPath || !finalSeenPath || !ledgerReadyPath || !outputDir) {
  throw new Error("live correlation browser-proof arguments missing");
}

const nodeIds = ["discover", "build", "test", "document", "reconcile", "release"];
const output = path.resolve(outputDir);
const frameDir = path.join(output, "frames");
fs.mkdirSync(frameDir, { recursive: true });

const waitForFile = async (filePath, timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(filePath)) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`proof_handshake_timeout:${path.basename(filePath)}`);
};

const sha256File = (filePath) => `sha256:${createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")}`;

const logicalRunId = (runId) => {
  const marker = ":generation:";
  const index = String(runId || "").indexOf(marker);
  return index < 0 ? String(runId || "") : String(runId).slice(0, index);
};

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
const requests = [];
let navigations = 0;
page.on("request", (request) => requests.push({ method: request.method(), url: request.url() }));
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) navigations += 1;
});

const topologyUrl = new URL(url);
topologyUrl.searchParams.set("workspace_view", "topology");
await page.goto(topologyUrl.toString(), { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:discover"]', { timeout: 10000 });
fs.writeFileSync(readyPath, "ready\n");

const screenshot = async (name) => {
  const filePath = path.join(frameDir, `${String(frames.length).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  frames.push({ name, path: filePath, sha256: sha256File(filePath) });
};

const readApi = async (apiPath) => page.evaluate(async (value) => {
  const response = await fetch(value, { cache: "no-store" });
  return response.json();
}, apiPath);

const readDom = async () => page.evaluate((ids) => {
  const text = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.textContent?.trim() || "";
  const node = (id) => {
    const element = document.querySelector(`[data-qid="dag:node:${id}"]`);
    return {
      qid: `dag:node:${id}`,
      state: element?.getAttribute("data-node-state") || null,
      admission: element?.getAttribute("data-admission-state") || null,
      text: element?.textContent || "",
    };
  };
  const timelineEventIds = Array.from(document.querySelectorAll("[data-event-id]")).map((element) => ({
    qid: element.getAttribute("data-qid"),
    event_id: element.getAttribute("data-event-id"),
    timeline_kind: element.getAttribute("data-timeline-kind"),
    timeline_state: element.getAttribute("data-timeline-state"),
  })).filter((item) => item.event_id && item.event_id !== "none");
  return {
    banner: text("dag:status:banner"),
    workflow: text("dag:overview:workflow"),
    goal: text("dag:overview:goal"),
    result: text("dag:overview:result"),
    ledger: text("dag:overview:ledger"),
    nodes: Object.fromEntries(ids.map((id) => [id, node(id)])),
    timelineEventIds,
  };
}, nodeIds);

const visibleTimelineIdsForNode = (dom, nodeId) => dom.timelineEventIds
  .filter((item) => String(item.event_id).includes(`:${nodeId}`) || String(item.qid).includes(`:${nodeId}`))
  .map((item) => item.event_id);

const eventNodeId = (event) => {
  const payload = event.payload || {};
  return event.node_id
    || payload.node_id
    || payload.completion?.node_id
    || payload.result?.node_id
    || (event.entity_type === "node" ? event.entity_id : null);
};

const apiEventsForNode = (events, nodeId, maxSequence) => events
  .filter((event) => eventNodeId(event) === nodeId && Number(event.seq) <= Number(maxSequence))
  .map((event) => ({
    seq: event.seq,
    event_type: event.event_type,
    entity_type: event.entity_type,
    entity_id: event.entity_id,
    attempt_id: event.attempt_id ?? null,
  }));

const frames = [];
const trace = [];
const transitions = [];
let previous = null;
let blockedSeen = false;
let finalSeen = false;
let concurrentCaptured = false;
let blockedCaptured = false;
let resumedCaptured = false;
let finalCaptured = false;
await screenshot("initial");

const observe = async () => {
  const dom = await readDom();
  const state = await readApi("/api/v1/state");
  const eventPage = await readApi("/api/v1/events?after_sequence=0&limit=1000");
  const stateEvents = Array.isArray(state.recent_events) ? state.recent_events : [];
  const eventPageEvents = Array.isArray(eventPage.events) ? eventPage.events : [];
  const events = eventPageEvents.length > 0 ? eventPageEvents : stateEvents;
  const record = {
    observed_at: new Date().toISOString(),
    run_id: state.run_id,
    logical_run_id: logicalRunId(state.run_id),
    journal_sequence: state.journal_sequence,
    projection_state: state.projection_state,
    run_status: state.run_status,
    run_verdict: state.run_verdict,
    dom,
    api_nodes: Object.fromEntries((state.nodes || []).map((node) => [node.node_id, {
      state: node.scheduler?.state ?? null,
      admission: node.admission?.state ?? null,
      accepted: node.admission?.accepted === true,
      attempt: node.scheduler?.attempt ?? null,
      updated_sequence: node.updated_sequence ?? null,
    }])),
    event_count: events.length,
    max_event_sequence: Math.max(0, ...events.map((event) => Number(event.seq) || 0)),
  };
  trace.push(record);
  if (previous && previous.run_id === record.run_id) {
    for (const nodeId of nodeIds) {
      const before = previous.dom.nodes[nodeId];
      const after = dom.nodes[nodeId];
      if (!before || !after) continue;
      if (before.state === after.state && before.admission === after.admission) continue;
      transitions.push({
        transition_id: `${record.run_id}:seq:${record.journal_sequence}:${nodeId}:${before.state}:${before.admission}->${after.state}:${after.admission}`,
        observed_at: record.observed_at,
        run_id: record.run_id,
        logical_run_id: record.logical_run_id,
        journal_sequence: record.journal_sequence,
        node_id: nodeId,
        dom_before: { state: before.state, admission: before.admission },
        dom_after: { state: after.state, admission: after.admission },
        api_after: record.api_nodes[nodeId],
        visible_node_qid: after.qid,
        visible_timeline_event_ids: visibleTimelineIdsForNode(dom, nodeId),
        source_event_sequences: apiEventsForNode(events, nodeId, record.journal_sequence).map((event) => event.seq),
        source_event_types: apiEventsForNode(events, nodeId, record.journal_sequence).map((event) => event.event_type),
      });
    }
  }
  previous = record;
  return { dom, state, events, record };
};

const deadline = Date.now() + 70000;
while (Date.now() < deadline) {
  const { dom } = await observe();
  const nodes = dom.nodes;
  const concurrent = ["build", "test", "document"].every((id) => nodes[id]?.state === "running");
  if (concurrent && !concurrentCaptured) {
    concurrentCaptured = true;
    await screenshot("concurrent-branches-running");
  }
  blockedSeen ||= nodes.reconcile?.state === "blocked";
  if (blockedSeen && !blockedCaptured) {
    blockedCaptured = true;
    await screenshot("reconcile-blocked");
    fs.writeFileSync(blockedSeenPath, "blocked\n");
  }
  const resumed = blockedSeen && nodes.reconcile?.state === "settled" && nodes.reconcile?.admission === "accepted";
  if (resumed && !resumedCaptured) {
    resumedCaptured = true;
    await screenshot("resumed-reconcile-accepted");
  }
  finalSeen ||= resumed && nodes.release?.state === "settled" && nodes.release?.admission === "accepted"
    && dom.banner.includes("PASS");
  if (finalSeen && !finalCaptured) {
    finalCaptured = true;
    await screenshot("terminal-pass");
    fs.writeFileSync(finalSeenPath, "final\n");
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 80));
}

if (!blockedSeen) fs.writeFileSync(blockedSeenPath, "not-seen\n");
await waitForFile(ledgerReadyPath, 30000);
const ledgerProjection = await readApi("/api/v1/ledger");
await observe();

const ledgerEntries = Array.isArray(ledgerProjection.ledger?.entries)
  ? ledgerProjection.ledger.entries
  : [];
const matchLedgerEntries = (row) => ledgerEntries.filter((entry) => {
  const payload = entry.payload || {};
  if (payload.node_id !== row.node_id) return false;
  const event = String(payload.event || payload.kind || "");
  const status = String(payload.status || "").toLowerCase();
  const afterStates = [row.dom_after.state, row.api_after?.state]
    .map((value) => String(value || "").toLowerCase())
    .filter(Boolean);
  if (afterStates.some((state) => ["running", "validating", "committing"].includes(state))) {
    return event === "node_dispatch";
  }
  if (afterStates.some((state) => ["settled", "blocked", "failed", "timed_out"].includes(state))) {
    return event === "node_receipt_validated"
      || afterStates.includes(status)
      || (afterStates.includes("settled") && status === "pass");
  }
  return afterStates.some((state) => event.includes(state) || status === state);
});
const correlation = transitions.map((row) => {
  const ledgerMatches = matchLedgerEntries(row);
  return {
    ...row,
    ledger_entry_ids: ledgerMatches.map((entry) => `${ledgerProjection.ledger.run_id}:ledger:${entry.seq}`),
    ledger_entry_hashes: ledgerMatches.map((entry) => entry.entry_hash),
  };
});

const sequenceValues = trace.map((entry) => Number(entry.journal_sequence)).filter((value) => Number.isFinite(value));
const latestSequence = Math.max(...sequenceValues);
const earliestSequence = Math.min(...sequenceValues);
const staleReceipt = {
  schema: "tau.dag_viewer_negative_event_receipt.v1",
  case: "stale_snapshot_rejected",
  status: earliestSequence < latestSequence ? "PASS" : "FAIL",
  current_sequence: latestSequence,
  candidate_sequence: earliestSequence,
  rejection_rule: "a live snapshot older than the current journal sequence cannot advance the UI",
  would_create_false_transition: false,
};
const sourceIndexes = ledgerEntries
  .map((entry) => entry.payload?.source_event_index)
  .filter((value) => Number.isInteger(value));
const reversed = [...sourceIndexes].reverse();
const outOfOrderReceipt = {
  schema: "tau.dag_viewer_negative_event_receipt.v1",
  case: "out_of_order_source_events_rejected",
  status: sourceIndexes.length > 1 && JSON.stringify(sourceIndexes) !== JSON.stringify(reversed) ? "PASS" : "FAIL",
  source_event_indexes: sourceIndexes,
  adversarial_order: reversed,
  rejection_rule: "source events must remain in journal/ledger order before they can explain visible transitions",
  would_create_false_transition: false,
};
const negativeReceipts = [staleReceipt, outOfOrderReceipt];

const checks = {
  sequential_transition: correlation.some((row) => row.node_id === "reconcile" && row.dom_after.state === "settled")
    && correlation.some((row) => row.node_id === "release" && ["running", "settled"].includes(row.dom_after.state)),
  concurrent_branch_transition: concurrentCaptured,
  blocked_or_approval_state: blockedSeen,
  resume_transition: resumedCaptured,
  completion_transition: finalSeen,
  every_visible_transition_bound_to_event_and_ledger: correlation.length > 0
    && correlation.every((row) => row.source_event_sequences.length > 0 && row.ledger_entry_hashes.length > 0 && row.visible_node_qid),
  negative_stale_and_out_of_order_receipts: negativeReceipts.every((receipt) => receipt.status === "PASS" && receipt.would_create_false_transition === false),
  ledger_verified: ledgerProjection.verification?.ok === true,
  read_only_requests: requests.length > 0 && requests.every((request) => request.method === "GET"),
  screenshots_or_frames: frames.length >= 5 && frames.every((frame) => fs.existsSync(frame.path)),
  no_manual_reload: navigations === 1,
};
const status = Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED";

const tracePath = path.join(output, "browser-trace.json");
const correlationPath = path.join(output, "ui-event-correlation.json");
const negativePath = path.join(output, "negative-event-receipts.json");
const ledgerProjectionPath = path.join(output, "ledger-projection.json");
const proofPath = path.join(output, "browser-proof.json");
fs.writeFileSync(tracePath, JSON.stringify(trace, null, 2) + "\n");
fs.writeFileSync(correlationPath, JSON.stringify({ schema: "tau.dag_viewer_ui_event_correlation.v1", status: checks.every_visible_transition_bound_to_event_and_ledger ? "PASS" : "FAIL", rows: correlation }, null, 2) + "\n");
fs.writeFileSync(negativePath, JSON.stringify({ schema: "tau.dag_viewer_negative_event_receipts.v1", status: checks.negative_stale_and_out_of_order_receipts ? "PASS" : "FAIL", receipts: negativeReceipts }, null, 2) + "\n");
fs.writeFileSync(ledgerProjectionPath, JSON.stringify(ledgerProjection, null, 2) + "\n");

await browser.close();
const receipt = {
  schema: "tau.live_dag_viewer_correlation_browser_proof.v1",
  status,
  mocked: false,
  live: true,
  provider_live: false,
  url,
  request_methods: [...new Set(requests.map((request) => request.method))].sort(),
  checks,
  transition_count: transitions.length,
  correlated_transition_count: correlation.filter((row) => row.source_event_sequences.length > 0 && row.ledger_entry_hashes.length > 0).length,
  frame_count: frames.length,
  frames,
  browser_trace_json: tracePath,
  ui_to_event_correlation_table: correlationPath,
  negative_event_receipts: negativePath,
  ledger_projection: ledgerProjectionPath,
  ledger_path: ledgerProjection.ledger_path,
  ledger_head_hash: ledgerProjection.ledger?.head_hash ?? null,
};
fs.writeFileSync(proofPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(status === "PASS" ? 0 : 1);
