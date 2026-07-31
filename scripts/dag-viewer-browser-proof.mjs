import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);

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

const observed = {
  graph_rendered: false,
  source_dag_visible: false,
  dag_plan_tab_visible: false,
  creator_attempt_1_visible: false,
  reviewer_revise_visible: false,
  revision_overlay_visible: false,
  creator_attempt_2_visible: false,
  reviewer_pass_remained_unaccepted: false,
  receipt_admission_turned_green: false,
  dependent_released_after_acceptance: false,
  refresh_reconstructed_state: false,
  read_only_requests: false,
  layout_non_overlapping: false,
  selected_node_inspector_visible: false,
  selected_node_sections_visible: false,
  selected_node_read_only: false,
  selected_node_no_mutation_controls: false,
};

await page.click('[data-qid="dag:inspector:source"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:source"]')?.getAttribute("aria-pressed") === "true");
observed.source_dag_visible = await page.$eval(
  '[data-qid="dag:workspace:inspector-content"]',
  (element) => (element.textContent || "").includes("tau.generic_dag_spec.v1"),
);
await page.click('[data-qid="dag:inspector:cause"]');

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
await page.reload({ waitUntil: "networkidle0", timeout: 15000 });
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
  const canvas = rect("dag:workspace:canvas");
  const attempts = rect("dag:transaction:attempts");
  const inspector = rect("dag:workspace:inspector");
  const inspectorContent = rect("dag:workspace:inspector-content");
  const proofBoundary = rect("dag:workspace:proof-boundary");
  const timeline = rect("dag:timeline:events");
  if (!graph || !canvas || !inspector || !inspectorContent || !proofBoundary || !timeline) return false;

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
