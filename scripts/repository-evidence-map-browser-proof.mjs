import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
const [urlPath, readyPath, scenario, desktopPath, mobilePath, outputPath] = process.argv.slice(2);
if (!urlPath || !readyPath || !["positive", "negative"].includes(scenario)) {
  throw new Error("repository evidence-map browser arguments invalid");
}
const ids = [
  "inventory-repository",
  "analyze-documentation",
  "analyze-tests",
  "analyze-package",
  "publish-evidence-map",
];
const branchIds = ids.slice(1, 4);
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
fs.writeFileSync(readyPath, "ready\n");
const urlDeadline = Date.now() + 15000;
while (!fs.existsSync(urlPath) && Date.now() < urlDeadline) {
  await new Promise((resolve) => setTimeout(resolve, 10));
}
if (!fs.existsSync(urlPath)) throw new Error("viewer URL unavailable");
const url = fs.readFileSync(urlPath, "utf8").trim();
const requests = [];
let navigations = 0;
page.on("request", (request) => requests.push({ method: request.method(), url: request.url() }));
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) navigations += 1;
});
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:overview"]', { timeout: 10000 });

const snapshot = async () => page.evaluate((nodeIds) => {
  const text = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.textContent?.trim() || "";
  const nodes = Object.fromEntries(nodeIds.map((id) => {
    const element = document.querySelector(`[data-qid="dag:node:${id}"]`);
    return [id, {
      state: element?.getAttribute("data-node-state") || null,
      admission: element?.getAttribute("data-admission-state") || null,
      blocker: text(`dag:node:${id}:blocker`),
    }];
  }));
  return {
    workflow: text("dag:overview:workflow"),
    goal: text("dag:overview:goal"),
    result: text("dag:overview:result"),
    blocker: text("dag:overview:blocker"),
    nodes,
  };
}, ids);

const checks = {
  workflow_title_visible: false,
  goal_visible: false,
  inventory_running: false,
  inventory_accepted: false,
  all_branches_running_together: false,
  all_branches_accepted: false,
  publish_running: false,
  publish_accepted: false,
  test_surface_missing_exact: false,
  publish_not_dispatched: false,
  final_result_visible: false,
  final_result_absent: false,
  read_only_requests: false,
  no_manual_reload: false,
  desktop_layout_non_overlapping: false,
  mobile_layout_non_overlapping: false,
};
const observations = [];
const seen = new Set();
const deadline = Date.now() + 30000;
let latest;
while (Date.now() < deadline) {
  latest = await snapshot();
  const signature = JSON.stringify(latest);
  if (!seen.has(signature)) {
    seen.add(signature);
    observations.push(latest);
  }
  const inventory = latest.nodes[ids[0]];
  const branches = branchIds.map((id) => latest.nodes[id]);
  const publish = latest.nodes[ids[4]];
  checks.workflow_title_visible ||= latest.workflow.includes("Repository Evidence Map");
  checks.goal_visible ||= latest.goal.includes("Map this repository for focused work.");
  checks.inventory_running ||= inventory.state === "running";
  checks.inventory_accepted ||= inventory.state === "settled" && inventory.admission === "accepted";
  checks.all_branches_running_together ||= branches.every((node) => node.state === "running");
  checks.all_branches_accepted ||= branches.every(
    (node) => node.state === "settled" && node.admission === "accepted",
  );
  checks.publish_running ||= publish.state === "running";
  checks.publish_accepted ||= publish.state === "settled" && publish.admission === "accepted";
  checks.test_surface_missing_exact ||=
    latest.nodes["analyze-tests"].blocker === "test_surface_missing";
  checks.final_result_visible ||=
    latest.result.includes("Repository evidence map validated")
    && latest.result.includes("repository-evidence-map.json")
    && latest.result.includes("repository-evidence-map.md");
  const positiveDone = checks.inventory_running && checks.inventory_accepted
    && checks.all_branches_running_together && checks.all_branches_accepted
    && checks.publish_running && checks.publish_accepted && checks.final_result_visible;
  const negativeDone = checks.inventory_accepted && checks.all_branches_running_together
    && checks.test_surface_missing_exact;
  if ((scenario === "positive" && positiveDone) || (scenario === "negative" && negativeDone)) break;
  await new Promise((resolve) => setTimeout(resolve, 40));
}
checks.publish_not_dispatched = scenario === "negative" && observations.every((item) => {
  const state = item.nodes["publish-evidence-map"].state;
  return state !== "running" && state !== "settled";
});
checks.final_result_absent = scenario === "negative"
  && observations.every((item) => !item.result.includes("repository-evidence-map."));
checks.read_only_requests = requests.length > 0 && requests.every((item) => item.method === "GET");
checks.no_manual_reload = navigations === 1;

const layout = async () => page.evaluate((nodeIds) => {
  const rect = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.getBoundingClientRect();
  const overview = rect("dag:overview");
  const graph = rect("dag:workspace:graph");
  const nodes = nodeIds.map((id) => rect(`dag:node:${id}`));
  if (!overview || !graph || nodes.some((node) => !node)) return false;
  const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  return overview.bottom <= graph.top + 1 && nodes.every((node, index) =>
    node.width > 0 && node.height > 0 && nodes.slice(index + 1).every((other) => !overlaps(node, other)));
}, ids);
checks.desktop_layout_non_overlapping = await layout();
await page.screenshot({ path: desktopPath, fullPage: false });
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
await new Promise((resolve) => setTimeout(resolve, 250));
checks.mobile_layout_non_overlapping = await layout();
await page.screenshot({ path: mobilePath, fullPage: true });
await browser.close();

const required = scenario === "positive"
  ? ["workflow_title_visible", "goal_visible", "inventory_running", "inventory_accepted",
    "all_branches_running_together", "all_branches_accepted", "publish_running",
    "publish_accepted", "final_result_visible", "read_only_requests", "no_manual_reload",
    "desktop_layout_non_overlapping", "mobile_layout_non_overlapping"]
  : ["workflow_title_visible", "goal_visible", "inventory_accepted",
    "all_branches_running_together", "test_surface_missing_exact", "publish_not_dispatched",
    "final_result_absent", "read_only_requests", "no_manual_reload",
    "desktop_layout_non_overlapping", "mobile_layout_non_overlapping"];
const receipt = {
  schema: "tau.repository_evidence_map_browser_proof.v1",
  scenario,
  status: required.every((key) => checks[key]) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  checks,
  observations,
  request_methods: [...new Set(requests.map((item) => item.method))],
  desktop_screenshot: desktopPath,
  desktop_screenshot_sha256: `sha256:${createHash("sha256").update(fs.readFileSync(desktopPath)).digest("hex")}`,
  mobile_screenshot: mobilePath,
  mobile_screenshot_sha256: `sha256:${createHash("sha256").update(fs.readFileSync(mobilePath)).digest("hex")}`,
};
fs.writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`);
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
