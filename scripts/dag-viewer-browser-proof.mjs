import fs from "node:fs";
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

const [url, screenshotPath, outputPath] = process.argv.slice(2);
if (!url || !screenshotPath || !outputPath) throw new Error("browser-proof arguments missing");
const viewport = {
  width: Number.parseInt(process.env.TAU_DAG_VIEWPORT_WIDTH || "1440", 10),
  height: Number.parseInt(process.env.TAU_DAG_VIEWPORT_HEIGHT || "1000", 10),
  deviceScaleFactor: Number.parseFloat(process.env.TAU_DAG_DEVICE_SCALE_FACTOR || "1"),
};
if (!Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) || !Number.isFinite(viewport.deviceScaleFactor)) {
  throw new Error("invalid viewport environment");
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport(viewport);
const methods = [];
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:workspace:graph"]', { timeout: 10000 });

const checkNames = [
  "orchestration_pool_visible", "workspace_toggles_visible", "live_sync_controls_visible", "live_sync_pause_resume",
  "left_pool_toggle_collapses", "right_inspector_toggle_collapses", "journal_drawer_toggle_collapses",
  "timeline_rendered", "timeline_tracks_visible", "timeline_role_swimlanes_visible", "timeline_role_clips_visible",
  "timeline_role_clips_unclipped", "timeline_duration_modes_truthful", "timeline_scroll_canvas_independent",
  "timeline_step_zoom_controls_visible", "timeline_step_zoom_changes_width", "timeline_playhead_scrubs_sequence",
  "layout_resize_handles_visible", "layout_resize_handles_keyboard_resize", "timeline_clip_selects_node",
  "topology_switch_visible", "graph_viewport_controls_visible", "graph_zoom_controls_change_viewport", "graph_rendered",
  "source_dag_visible", "dag_plan_tab_visible", "creator_attempt_1_visible", "reviewer_revise_visible",
  "revision_overlay_visible", "creator_attempt_2_visible", "reviewer_pass_remained_unaccepted",
  "receipt_admission_turned_green", "dependent_released_after_acceptance", "refresh_reconstructed_state",
  "read_only_requests", "layout_non_overlapping", "selected_node_inspector_visible", "selected_node_sections_visible",
  "selected_node_read_only", "selected_node_no_mutation_controls",
];
const observed = Object.fromEntries(checkNames.map((name) => [name, false]));

observed.workspace_toggles_visible = await page.evaluate(() => [
  '[data-qid="dag:layout:toggle-left"]',
  '[data-qid="dag:layout:toggle-bottom"]',
  '[data-qid="dag:layout:toggle-right"]',
].every((selector) => Boolean(document.querySelector(selector))));
observed.orchestration_pool_visible = await page.evaluate(() => {
  const pool = document.querySelector('[data-qid="dag:pool:browser"]');
  const card = document.querySelector('[data-qid^="dag:pool:orchestration:"]');
  return Boolean(pool && card && (pool.textContent || "").includes("Orchestrations") && (card.textContent || "").includes("creator-reviewer"));
});
observed.live_sync_controls_visible = await page.evaluate(() => {
  const controls = document.querySelector('[data-qid="dag:stream:controls"]');
  const status = document.querySelector('[data-qid="dag:stream:status"]');
  return Boolean(controls && status && ["CONNECTED", "RECONNECTING", "OFFLINE"].includes(status.getAttribute("data-stream-status") || ""));
});
await page.click('[data-qid="dag:stream:toggle-ingestion"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:stream:status"]')?.getAttribute("data-stream-status") === "PAUSED");
await page.click('[data-qid="dag:stream:toggle-ingestion"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:stream:status"]')?.getAttribute("data-stream-status") !== "PAUSED");
observed.live_sync_pause_resume = true;
await page.click('[data-qid="dag:layout:toggle-right"]');
await page.waitForFunction(() => !document.querySelector('[data-qid="dag:workspace:inspector"]'));
observed.right_inspector_toggle_collapses = true;
await page.click('[data-qid="dag:layout:toggle-right"]');
await page.waitForSelector('[data-qid="dag:workspace:inspector"]', { timeout: 10000 });
await page.click('[data-qid="dag:layout:toggle-left"]');
await page.waitForFunction(() => !document.querySelector('[data-qid="dag:pool:browser"]'));
observed.left_pool_toggle_collapses = true;
await page.click('[data-qid="dag:layout:toggle-left"]');
await page.waitForSelector('[data-qid="dag:pool:browser"]', { timeout: 10000 });

await page.click('[data-qid="dag:inspector:source"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:source"]')?.getAttribute("aria-pressed") === "true");
observed.source_dag_visible = await page.$eval(
  '[data-qid="dag:workspace:inspector-content"]',
  (element) => (element.textContent || "").includes("tau.generic_dag_spec.v1"),
);
await page.click('[data-qid="dag:inspector:cause"]');
await page.waitForSelector('[data-qid="dag:timeline:run"]', { timeout: 10000 });
observed.timeline_rendered = await page.$eval(
  '[data-qid="dag:timeline:run"]',
  (element) => (element.textContent || "").includes("Execution")
    && (element.textContent || "").includes("Proof")
    && (element.textContent || "").includes("Control & Effects"),
);
observed.timeline_tracks_visible = await page.evaluate(() => [
  '[data-qid="dag:timeline:execution:creator-reviewer"]',
  '[data-qid="dag:timeline:proof:creator-reviewer"]',
].every((selector) => Boolean(document.querySelector(selector))));
const roleTimeline = await page.evaluate(() => {
  const panel = document.querySelector('[data-qid="dag:timeline:role-swimlanes"]');
  const roleClips = [
    panel?.querySelector('[data-event-id^="execution:creator-reviewer:"]'),
    panel?.querySelector('[data-event-id^="execution:continuation:"]'),
  ];
  const panelRect = panel?.getBoundingClientRect();
  const visible = Boolean(panelRect && panelRect.width > 0 && panelRect.height > 0 && panelRect.top < window.innerHeight && panelRect.bottom > 0);
  return {
    visible,
    roleClipsVisible: roleClips.every((clip) => {
      const rect = clip?.getBoundingClientRect();
      return Boolean(rect && rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0);
    }),
    roleClipsUnclipped: roleClips.every((clip) => {
      const parent = clip?.parentElement;
      if (!clip || !parent) return false;
      return clip.offsetLeft >= -1 && clip.offsetLeft + clip.offsetWidth <= parent.scrollWidth + 1;
    }),
    hasDurationClip: roleClips.some((clip) => clip?.getAttribute("data-duration-mode") === "duration"),
  };
});
observed.timeline_role_swimlanes_visible = roleTimeline.visible;
observed.timeline_role_clips_visible = roleTimeline.roleClipsVisible;
observed.timeline_role_clips_unclipped = roleTimeline.roleClipsUnclipped;
observed.timeline_duration_modes_truthful = roleTimeline.hasDurationClip;
observed.timeline_scroll_canvas_independent = await page.evaluate(() => {
  const root = document.querySelector('[data-qid="dag:timeline:run"]');
  const scroll = document.querySelector('[data-qid="dag:timeline:canvas-scroll"]');
  const tracks = document.querySelector(".run-timeline__tracks");
  if (!root || !scroll || !tracks) return false;
  const minCanvas = Number(root.getAttribute("data-min-canvas-px") || "0");
  const stepWidth = Number(root.getAttribute("data-step-width") || "0");
  const visibleWidth = root.getBoundingClientRect().width;
  return minCanvas >= 390 * 3
    && stepWidth >= 16
    && minCanvas > visibleWidth
    && scroll.scrollWidth >= minCanvas
    && tracks.scrollWidth >= minCanvas;
});
observed.timeline_step_zoom_controls_visible = await page.evaluate(() => [
  '[data-qid="dag:timeline:zoom-out"]',
  '[data-qid="dag:timeline:zoom-slider"]',
  '[data-qid="dag:timeline:zoom-in"]',
  '[data-qid="dag:timeline:fit-view"]',
  '[data-qid="dag:timeline:detail-view"]',
].every((selector) => Boolean(document.querySelector(selector))));
const timelineBeforeZoom = await page.$eval('[data-qid="dag:timeline:run"]', (element) => element.getAttribute("data-step-width"));
await page.click('[data-qid="dag:timeline:detail-view"]');
await page.waitForFunction((before) => document.querySelector('[data-qid="dag:timeline:run"]')?.getAttribute("data-step-width") !== before, {}, timelineBeforeZoom);
const timelineAfterZoom = await page.$eval('[data-qid="dag:timeline:run"]', (element) => element.getAttribute("data-step-width"));
observed.timeline_step_zoom_changes_width = timelineBeforeZoom !== timelineAfterZoom && timelineAfterZoom === "80";
await page.focus('[data-qid="dag:timeline:playhead-scrub"]');
await page.keyboard.press("Home");
await page.waitForFunction(() => document.querySelector('[data-qid="dag:timeline:playhead"]')?.getAttribute("data-active-sequence") === "1");
await page.keyboard.press("End");
await page.waitForFunction(() => {
  const root = document.querySelector('[data-qid="dag:timeline:run"]');
  const playhead = document.querySelector('[data-qid="dag:timeline:playhead"]');
  return Boolean(root && playhead && playhead.getAttribute("data-active-sequence") === root.getAttribute("data-sequence-count"));
});
observed.timeline_playhead_scrubs_sequence = true;
observed.layout_resize_handles_visible = await page.evaluate(() => [
  '[data-qid="dag:layout:resize-left"]',
  '[data-qid="dag:layout:resize-right"]',
].every((selector) => Boolean(document.querySelector(selector))));
const paneWidthsBeforeResize = await page.$eval(".dag-app", (element) => ({
  left: element.getAttribute("data-left-width"),
  right: element.getAttribute("data-right-width"),
}));
await page.focus('[data-qid="dag:layout:resize-left"]');
await page.keyboard.press("ArrowRight");
await page.focus('[data-qid="dag:layout:resize-right"]');
await page.keyboard.press("ArrowLeft");
const paneWidthsAfterResize = await page.$eval(".dag-app", (element) => ({
  left: element.getAttribute("data-left-width"),
  right: element.getAttribute("data-right-width"),
}));
observed.layout_resize_handles_keyboard_resize =
  paneWidthsBeforeResize.left !== paneWidthsAfterResize.left
  && paneWidthsBeforeResize.right !== paneWidthsAfterResize.right;
await page.click('[data-qid^="dag:pool:orchestration:"]');
await page.click('[data-qid="dag:timeline:execution:creator-reviewer"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:node"]')?.getAttribute("aria-pressed") === "true");
await page.waitForSelector('[data-qid="dag:selected-node-inspector"]', { timeout: 10000 });
observed.timeline_clip_selects_node = await page.$eval(
  '[data-qid="dag:selected-node-inspector"]',
  (element) => (element.textContent || "").includes("creator-reviewer"),
);
await page.click('[data-qid="dag:workspace-view:topology"]');
await page.waitForSelector('[data-qid="dag:node:creator-reviewer"]', { timeout: 10000 });
observed.topology_switch_visible = page.url().includes("workspace_view=topology");
observed.graph_viewport_controls_visible = await page.evaluate(() => [
  '[data-qid="dag:graph:viewport-controls"]',
  '[data-qid="dag:graph:zoom-in"]',
  '[data-qid="dag:graph:zoom-out"]',
  '[data-qid="dag:graph:reset-view"]',
  '[data-qid="dag:graph:fit-view"]',
].every((selector) => Boolean(document.querySelector(selector))));
const viewportBeforeZoom = await page.$eval(".react-flow__viewport", (element) => element.getAttribute("style") || "");
await page.click('[data-qid="dag:graph:zoom-in"]');
await page.waitForFunction((before) => (document.querySelector(".react-flow__viewport")?.getAttribute("style") || "") !== before, {}, viewportBeforeZoom);
const zoomPercentAfterZoom = await page.$eval('[data-qid="dag:graph:zoom-percent"]', (element) => element.textContent || "");
const viewportAfterZoom = await page.$eval(".react-flow__viewport", (element) => element.getAttribute("style") || "");
await page.click('[data-qid="dag:graph:reset-view"]');
await page.waitForFunction((after) => (document.querySelector(".react-flow__viewport")?.getAttribute("style") || "") !== after, {}, viewportAfterZoom);
const zoomPercentAfterReset = await page.$eval('[data-qid="dag:graph:zoom-percent"]', (element) => element.textContent || "");
observed.graph_zoom_controls_change_viewport = zoomPercentAfterZoom !== zoomPercentAfterReset;

const deadline = Date.now() + 25000;
while (Date.now() < deadline) {
  const state = await page.evaluate(() => {
    const text = (selector) => document.querySelector(selector)?.textContent || "";
    const creator = document.querySelector('[data-qid="dag:node:creator-reviewer"]');
    const continuation = document.querySelector('[data-qid="dag:node:continuation"]');
    return {
      graph: Boolean(document.querySelector(".react-flow__viewport")),
      attempt1: text('[data-qid="dag:transaction:attempt:1"]'),
      attempt2: text('[data-qid="dag:transaction:attempt:2"]'),
      creatorAdmission: creator?.getAttribute("data-admission-state"),
      creatorClass: creator?.className || "",
      continuationState: continuation?.getAttribute("data-node-state"),
    };
  });
  observed.graph_rendered ||= state.graph;
  observed.creator_attempt_1_visible ||= Boolean(state.attempt1);
  observed.reviewer_revise_visible ||= state.attempt1.includes("REVISE");
  observed.revision_overlay_visible ||= state.attempt1.includes("revision committed");
  observed.creator_attempt_2_visible ||= Boolean(state.attempt2);
  observed.reviewer_pass_remained_unaccepted ||=
    state.attempt2.includes("PASS claim") && state.creatorAdmission !== "accepted";
  observed.receipt_admission_turned_green ||=
    state.creatorAdmission === "accepted" && state.creatorClass.includes("tau-node--accepted");
  observed.dependent_released_after_acceptance ||=
    observed.receipt_admission_turned_green && ["ready", "running", "settled"].includes(state.continuationState);
  if (Object.entries(observed).filter(([key]) => ![
    "dag_plan_tab_visible",
    "refresh_reconstructed_state",
    "read_only_requests",
    "layout_non_overlapping",
    "selected_node_inspector_visible",
    "selected_node_sections_visible",
    "selected_node_read_only",
    "selected_node_no_mutation_controls",
  ].includes(key)).every(([, value]) => value)) break;
  await new Promise((resolve) => setTimeout(resolve, 100));
}

await page.click('[data-qid="dag:inspector:plan"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:plan"]')?.getAttribute("aria-pressed") === "true");
observed.dag_plan_tab_visible = await page.$eval(
  '[data-qid="dag:workspace:inspector-content"]',
  (element) => (element.textContent || "").includes("tau.dag_plan.v1"),
);
await page.click('[data-qid="dag:inspector:source"]');
const beforeRefresh = await page.$eval('[data-qid="dag:node:creator-reviewer"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
}));
const topologyUrl = new URL(url);
topologyUrl.searchParams.set("workspace_view", "topology");
await page.goto(topologyUrl.toString(), { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:creator-reviewer"]', { timeout: 10000 });
const afterRefresh = await page.$eval('[data-qid="dag:node:creator-reviewer"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
}));
observed.refresh_reconstructed_state = JSON.stringify(beforeRefresh) === JSON.stringify(afterRefresh);
observed.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
observed.layout_non_overlapping = await page.evaluate(() => {
  const rect = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.getBoundingClientRect();
  const graph = rect("dag:workspace:graph");
  const pool = rect("dag:pool:browser");
  const canvas = rect("dag:workspace:canvas");
  const attempts = rect("dag:transaction:attempts");
  const inspector = rect("dag:workspace:inspector");
  const inspectorContent = rect("dag:workspace:inspector-content");
  const proofBoundary = rect("dag:workspace:proof-boundary");
  const timeline = rect("dag:timeline:events");
  if (!graph || !pool || !canvas || !inspector || !inspectorContent || !proofBoundary || !timeline) return false;

  const contained = (child, parent) =>
    child.left >= parent.left - 1
    && child.right <= parent.right + 1
    && child.top >= parent.top - 1
    && child.bottom <= parent.bottom + 1;

  const pageHeight = Math.max(document.documentElement.scrollHeight, window.innerHeight);
  const sideBySideLayout =
    graph.right <= inspector.left + 1
    && Math.max(graph.bottom, inspector.bottom) <= timeline.top + 1;
  const stackedLayout =
    graph.bottom <= inspector.top + 1
    && inspector.bottom <= timeline.top + 1;

  return (sideBySideLayout || stackedLayout)
    && pool.right <= graph.left + 1
    && timeline.bottom <= pageHeight + 1
    && contained(canvas, graph)
    && (!attempts || (contained(attempts, graph) && canvas.bottom <= attempts.top + 1))
    && contained(inspectorContent, inspector)
    && contained(proofBoundary, inspector)
    && inspectorContent.bottom <= proofBoundary.top + 1;
});
await page.click('[data-qid="dag:node:creator-reviewer"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:node"]')?.getAttribute("aria-pressed") === "true");
await page.waitForSelector('[data-qid="dag:selected-node-inspector"]', { timeout: 10000 });
const selectedNodeInspector = await page.$eval('[data-qid="dag:selected-node-inspector"]', (element) => ({
  text: element.textContent || "",
  retryButtons: Array.from(element.querySelectorAll("button")).filter((button) => /retry|repair|approve|mutate/i.test(button.textContent || "")).length,
}));
observed.selected_node_inspector_visible = selectedNodeInspector.text.includes("creator-reviewer")
  && selectedNodeInspector.text.includes("read-only backend projection");
observed.selected_node_sections_visible = [
  "Contract",
  "Accepted Inputs",
  "Completion Boundary",
  "Review Scope",
  "Workspace Freshness",
  "Worker",
  "Accepted Evidence and Artifacts",
  "Diagnostics",
].every((label) => selectedNodeInspector.text.includes(label));
observed.selected_node_read_only = selectedNodeInspector.text.includes("read-only backend projection");
observed.selected_node_no_mutation_controls =
  selectedNodeInspector.text.includes("mutation controls: 0")
  && selectedNodeInspector.retryButtons === 0;
await page.click('[data-qid="dag:workspace-view:timeline"]');
await page.waitForSelector('[data-qid="dag:timeline:run"]', { timeout: 10000 });
const finalRoleTimeline = await page.evaluate(() => {
  const panel = document.querySelector('[data-qid="dag:timeline:role-swimlanes"]');
  const roleClips = [
    panel?.querySelector('[data-event-id^="execution:creator-reviewer:"]'),
    panel?.querySelector('[data-event-id^="execution:continuation:"]'),
  ];
  const panelRect = panel?.getBoundingClientRect();
  const visible = Boolean(panelRect && panelRect.width > 0 && panelRect.height > 0 && panelRect.top < window.innerHeight && panelRect.bottom > 0);
  return {
    visible,
    roleClipsVisible: roleClips.every((clip) => {
      const rect = clip?.getBoundingClientRect();
      return Boolean(rect && rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0);
    }),
    roleClipsUnclipped: roleClips.every((clip) => {
      const parent = clip?.parentElement;
      if (!clip || !parent) return false;
      return clip.offsetLeft >= -1 && clip.offsetLeft + clip.offsetWidth <= parent.scrollWidth + 1;
    }),
    hasDurationClip: roleClips.some((clip) => clip?.getAttribute("data-duration-mode") === "duration"),
  };
});
observed.timeline_role_swimlanes_visible ||= finalRoleTimeline.visible;
observed.timeline_role_clips_visible ||= finalRoleTimeline.roleClipsVisible;
observed.timeline_role_clips_unclipped ||= finalRoleTimeline.roleClipsUnclipped;
observed.timeline_duration_modes_truthful ||= finalRoleTimeline.hasDurationClip;
await page.waitForFunction(() => {
  const timeline = document.querySelector('[data-qid="dag:timeline:run"]');
  return Number(timeline?.getAttribute("data-sequence-count") || "0") >= 38;
}, { timeout: 30000 });
observed.timeline_scroll_canvas_independent ||= await page.evaluate(() => {
  const root = document.querySelector('[data-qid="dag:timeline:run"]');
  const scroll = document.querySelector('[data-qid="dag:timeline:canvas-scroll"]');
  const tracks = document.querySelector(".run-timeline__tracks");
  if (!root || !scroll || !tracks) return false;
  const minCanvas = Number(root.getAttribute("data-min-canvas-px") || "0");
  const stepWidth = Number(root.getAttribute("data-step-width") || "0");
  const visibleWidth = root.getBoundingClientRect().width;
  return minCanvas >= 390 * 3
    && stepWidth >= 16
    && minCanvas > visibleWidth
    && scroll.scrollWidth >= minCanvas
    && tracks.scrollWidth >= minCanvas;
});
await page.click('[data-qid="dag:layout:toggle-bottom"]');
await page.waitForFunction(() => !document.querySelector(".event-timeline__scroll"));
observed.journal_drawer_toggle_collapses = true;
await page.click('[data-qid="dag:layout:toggle-bottom"]');
await page.waitForSelector(".event-timeline__scroll", { timeout: 10000 });
await page.screenshot({ path: screenshotPath, fullPage: viewport.width < 900 });
await browser.close();
const screenshotSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`;

const receipt = {
  schema: "tau.dag_viewer_browser_proof.v1",
  status: Object.values(observed).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  viewport,
  screenshot: screenshotPath,
  screenshot_sha256: screenshotSha256,
  request_methods: [...new Set(methods)].sort(),
  checks: observed,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
