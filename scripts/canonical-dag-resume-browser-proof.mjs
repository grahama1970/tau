import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
const [url, desktopPath, mobilePath, outputPath] = process.argv.slice(2);
if (!url || !desktopPath || !mobilePath || !outputPath) {
  throw new Error("resume browser-proof arguments missing");
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
const methods = [];
const navigations = [];
page.on("request", (request) => methods.push(request.method()));
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) navigations.push({ at: new Date().toISOString(), url: frame.url() });
});
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:discover"]', { timeout: 10000 });

const runIdentity = (runId) => {
  const marker = ":generation:";
  const markerIndex = runId.indexOf(marker);
  if (!runId || markerIndex < 0 || markerIndex !== runId.lastIndexOf(marker)) {
    return { logicalRunId: runId, generation: 0 };
  }
  const suffix = runId.slice(markerIndex + marker.length);
  if (!/^[1-9][0-9]*$/.test(suffix)) return { logicalRunId: runId, generation: 0 };
  return { logicalRunId: runId.slice(0, markerIndex), generation: Number(suffix) };
};

const outputDir = path.dirname(outputPath);
const frameDir = path.join(outputDir, "trace-frames");
fs.mkdirSync(frameDir, { recursive: true });
const tracePath = path.join(outputDir, "browser-trace.jsonl");
const journalReadbackPath = path.join(outputDir, "viewer-journal-readback.json");
const initialFramePath = path.join(frameDir, "00-initial.png");
await page.screenshot({ path: initialFramePath, fullPage: false });

const observed = {
  goal_visible: false,
  progressed_without_reload: false,
  concurrent_branches_running: false,
  targeted_repair_block_visible: false,
  unaffected_work_remained_accepted: false,
  resumed_to_completion: false,
  human_release_accepted: false,
  final_pass_visible: false,
  read_only_requests: false,
  desktop_layout_non_overlapping: false,
  mobile_primary_state_visible: false,
  workflow_identity_visible: false,
  human_goal_visible: false,
  accepted_final_result_visible: false,
  same_run_id_visible_throughout: false,
  physical_generation_handoff_visible: false,
  journal_readback_matches_visible_run: false,
  journal_readback_has_state_transitions: false,
  time_sequenced_screenshots: false,
  no_manual_reload: false,
};
const states = new Set();
const trace = [];
const milestoneFrames = [{ name: "initial", screenshot: initialFramePath }];
const capturedMilestones = new Set(["initial"]);
const maybeCaptureMilestone = async (name) => {
  if (capturedMilestones.has(name)) return;
  capturedMilestones.add(name);
  const framePath = path.join(
    frameDir,
    `${String(milestoneFrames.length).padStart(2, "0")}-${name}.png`,
  );
  await page.screenshot({ path: framePath, fullPage: false });
  milestoneFrames.push({ name, screenshot: framePath });
};
const deadline = Date.now() + 60000;
while (Date.now() < deadline) {
  const state = await page.evaluate(() => {
    const element = (id) => document.querySelector(`[data-qid="dag:node:${id}"]`);
    const value = (id) => ({
      state: element(id)?.getAttribute("data-node-state"),
      admission: element(id)?.getAttribute("data-admission-state"),
    });
    return {
      goal: document.querySelector('[data-qid="dag:status:goal"]')?.textContent || "",
      banner: document.querySelector('[data-qid="dag:status:banner"]')?.textContent || "",
      visibleRunId: document.querySelector('[data-qid="dag:status:logical-run-id"]')?.textContent || "",
      visibleGeneration: document.querySelector('[data-qid="dag:status:physical-generation"]')?.textContent || "",
      discover: value("discover"),
      build: value("build"),
      test: value("test"),
      document: value("document"),
      reconcile: value("reconcile"),
      release: value("release"),
      workflow: document.querySelector('[data-qid="dag:overview:workflow"]')?.textContent || "",
      overviewGoal: document.querySelector('[data-qid="dag:overview:goal"]')?.textContent || "",
      result: document.querySelector('[data-qid="dag:overview:result"]')?.textContent || "",
    };
  });
  const apiState = await page.evaluate(async () => {
    const response = await fetch("/api/v1/state", { cache: "no-store" });
    return response.json();
  });
  const stateVector = [
    state.discover.state,
    state.build.state,
    state.test.state,
    state.document.state,
    state.reconcile.state,
    state.release.state,
  ];
  const nodeSummary = Object.fromEntries(
    (apiState.nodes || []).map((node) => [
      node.node_id,
      {
        state: node.scheduler?.state ?? null,
        admission: node.admission?.state ?? null,
      },
    ]),
  );
  trace.push({
    observed_at: new Date().toISOString(),
    run_id: apiState.run_id ?? null,
    journal_sequence: apiState.journal_sequence ?? null,
    projection_state: apiState.projection_state ?? null,
    run_status: apiState.run_status ?? null,
    run_verdict: apiState.run_verdict ?? null,
    banner: state.banner,
    visible_logical_run_id: state.visibleRunId,
    visible_generation: state.visibleGeneration,
    dom_state_vector: stateVector,
    api_nodes: nodeSummary,
  });
  observed.goal_visible ||= state.goal.includes("Tau lets a human launch");
  states.add(JSON.stringify(stateVector));
  observed.progressed_without_reload ||= states.size >= 4;
  observed.concurrent_branches_running ||=
    state.build.state === "running"
    && state.test.state === "running"
    && state.document.state === "running";
  if (observed.concurrent_branches_running) await maybeCaptureMilestone("concurrent-branches-running");
  const blocked = state.banner.includes("BLOCKED") && state.reconcile.state === "blocked";
  observed.targeted_repair_block_visible ||= blocked;
  if (observed.targeted_repair_block_visible) await maybeCaptureMilestone("targeted-repair-blocked");
  observed.unaffected_work_remained_accepted ||=
    blocked
    && [state.discover, state.build, state.test, state.document]
      .every((node) => node.admission === "accepted");
  observed.resumed_to_completion ||=
    observed.targeted_repair_block_visible
    && state.reconcile.state === "settled"
    && state.reconcile.admission === "accepted";
  if (observed.resumed_to_completion) await maybeCaptureMilestone("resumed-reconcile-accepted");
  observed.human_release_accepted ||=
    state.release.state === "settled" && state.release.admission === "accepted";
  observed.final_pass_visible ||= state.banner.includes("COMPLETE") && state.banner.includes("PASS");
  if (observed.final_pass_visible) await maybeCaptureMilestone("terminal-pass");
  observed.workflow_identity_visible ||= state.workflow.includes("Durable mixed topology with targeted repair")
    && !state.workflow.includes("Uncatalogued");
  observed.human_goal_visible ||= state.overviewGoal.includes("Tau lets a human launch")
    && !state.overviewGoal.includes("unavailable");
  observed.accepted_final_result_visible ||= state.result.includes("release produced its accepted human-release artifact")
    && !state.result.includes("No accepted final result");
  if ([
    observed.goal_visible,
    observed.progressed_without_reload,
    observed.concurrent_branches_running,
    observed.targeted_repair_block_visible,
    observed.unaffected_work_remained_accepted,
    observed.resumed_to_completion,
    observed.human_release_accepted,
    observed.final_pass_visible,
    observed.workflow_identity_visible,
    observed.human_goal_visible,
    observed.accepted_final_result_visible,
  ].every(Boolean)) break;
  await new Promise((resolve) => setTimeout(resolve, 100));
}

fs.writeFileSync(tracePath, trace.map((entry) => JSON.stringify(entry)).join("\n") + "\n");
const finalApiState = await page.evaluate(async () => {
  const response = await fetch("/api/v1/state", { cache: "no-store" });
  return response.json();
});
const apiJournal = await page.evaluate(async () => {
  const response = await fetch("/api/v1/events?after_sequence=0&limit=500", {
    cache: "no-store",
  });
  return response.json();
});
fs.writeFileSync(
  journalReadbackPath,
  JSON.stringify({ state: finalApiState, journal: apiJournal }, null, 2) + "\n",
);

observed.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
const visibleRunIds = [...new Set(trace.map((entry) => entry.run_id).filter(Boolean))];
const visibleLogicalRunIds = [...new Set(trace.map((entry) => entry.visible_logical_run_id).filter(Boolean))];
const logicalRunIdsFromApi = [...new Set(visibleRunIds.map((runId) => runIdentity(runId).logicalRunId))];
const physicalGenerations = [...new Set(visibleRunIds.map((runId) => runIdentity(runId).generation))].sort((a, b) => a - b);
observed.same_run_id_visible_throughout = visibleLogicalRunIds.length === 1
  && logicalRunIdsFromApi.length === 1
  && visibleLogicalRunIds[0] === logicalRunIdsFromApi[0];
observed.physical_generation_handoff_visible = physicalGenerations.length >= 2
  && physicalGenerations[0] === 0
  && physicalGenerations.at(-1) >= 1;
const journalEvents = Array.isArray(apiJournal.events) ? apiJournal.events : [];
const maxJournalSequence = Math.max(...journalEvents.map((event) => Number(event.seq) || 0), 0);
const traceSequences = trace.map((entry) => Number(entry.journal_sequence) || 0);
const maxTraceSequence = Math.max(...traceSequences, 0);
const minTraceSequence = Math.min(...traceSequences.filter((sequence) => sequence > 0));
observed.journal_readback_matches_visible_run = observed.same_run_id_visible_throughout
  && runIdentity(apiJournal.run_id ?? "").logicalRunId === visibleLogicalRunIds[0]
  && runIdentity(finalApiState.run_id ?? "").logicalRunId === visibleLogicalRunIds[0]
  && Number(apiJournal.after_sequence) === 0
  && journalEvents.length > 0;
observed.journal_readback_has_state_transitions = journalEvents.length >= states.size
  && maxJournalSequence === Number(finalApiState.journal_sequence)
  && maxTraceSequence <= maxJournalSequence
  && minTraceSequence > 0;
observed.time_sequenced_screenshots = milestoneFrames.length >= 5
  && milestoneFrames.every((frame) => fs.existsSync(frame.screenshot));
observed.no_manual_reload = navigations.length === 1
  && trace.length >= states.size
  && states.size >= 4;
observed.desktop_layout_non_overlapping = await page.evaluate(() => {
  const ids = ["discover", "build", "test", "document", "reconcile", "release"];
  const rects = ids.map((id) =>
    document.querySelector(`[data-qid="dag:node:${id}"]`)?.getBoundingClientRect(),
  );
  if (rects.some((rect) => !rect)) return false;
  const overlaps = (a, b) =>
    a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  return rects.every((rect, index) =>
    rect.width > 0 && rect.height > 0
      && rects.slice(index + 1).every((other) => !overlaps(rect, other)),
  );
});
await page.screenshot({ path: desktopPath, fullPage: false });

await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
await new Promise((resolve) => setTimeout(resolve, 250));
observed.mobile_primary_state_visible = await page.evaluate(() => {
  const goal = document.querySelector('[data-qid="dag:status:goal"]');
  const graph = document.querySelector('[data-qid="dag:workspace:graph"]');
  const banner = document.querySelector('[data-qid="dag:status:banner"]');
  return Boolean(goal && graph && banner && graph.getBoundingClientRect().height > 0)
    && document.documentElement.scrollWidth <= window.innerWidth;
});
await page.screenshot({ path: mobilePath, fullPage: false });
await browser.close();

const hash = (path) => `sha256:${createHash("sha256").update(fs.readFileSync(path)).digest("hex")}`;
const receipt = {
  schema: "tau.canonical_dag_resume_browser_proof.v1",
  status: Object.values(observed).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  desktop_screenshot: desktopPath,
  desktop_screenshot_sha256: hash(desktopPath),
  mobile_screenshot: mobilePath,
  mobile_screenshot_sha256: hash(mobilePath),
  browser_trace_jsonl: tracePath,
  browser_trace_sample_count: trace.length,
  viewer_journal_readback: journalReadbackPath,
  viewer_journal_event_count: journalEvents.length,
  raw_runtime_journal: path.join(outputDir, "run-root", "run", "events.jsonl"),
  time_sequenced_screenshots: milestoneFrames.map((frame) => ({
    name: frame.name,
    screenshot: frame.screenshot,
    screenshot_sha256: hash(frame.screenshot),
  })),
  manual_reload_count: Math.max(0, navigations.length - 1),
  navigations,
  visible_run_ids: visibleRunIds,
  visible_logical_run_ids: visibleLogicalRunIds,
  logical_run_ids_from_api: logicalRunIdsFromApi,
  physical_generations: physicalGenerations,
  min_trace_journal_sequence: minTraceSequence,
  max_trace_journal_sequence: maxTraceSequence,
  max_journal_readback_sequence: maxJournalSequence,
  final_api_run_id: finalApiState.run_id ?? null,
  request_methods: [...new Set(methods)].sort(),
  observed_state_count: states.size,
  checks: observed,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
