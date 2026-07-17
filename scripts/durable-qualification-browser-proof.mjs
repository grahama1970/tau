import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
const [urlPath, readyPath, repairSeenPath, approvalSeenPath, desktopPath, mobilePath, outputPath] = process.argv.slice(2);
const ids = [
  "capture-repository", "qualify-documentation", "qualify-tests", "qualify-package",
  "reconcile-qualification", "publish-qualification", "finalize-qualification",
];
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
fs.writeFileSync(readyPath, "ready\n");
while (!fs.existsSync(urlPath)) await new Promise((resolve) => setTimeout(resolve, 10));
const url = fs.readFileSync(urlPath, "utf8").trim();
const requests = [];
let navigations = 0;
page.on("request", (request) => requests.push({ method: request.method(), url: request.url() }));
page.on("framenavigated", (frame) => { if (frame === page.mainFrame()) navigations += 1; });
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:overview"]', { timeout: 10000 });

const snapshot = async () => page.evaluate((nodeIds) => {
  const text = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.textContent?.trim() || "";
  return {
    workflow: text("dag:overview:workflow"),
    goal: text("dag:overview:goal"),
    result: text("dag:overview:result"),
    blocker: text("dag:overview:blocker"),
    timeline: text("dag:timeline:events"),
    nodes: Object.fromEntries(nodeIds.map((id) => {
      const element = document.querySelector(`[data-qid="dag:node:${id}"]`);
      return [id, {
        state: element?.getAttribute("data-node-state") || null,
        admission: element?.getAttribute("data-admission-state") || null,
        blocker: text(`dag:node:${id}:blocker`),
      }];
    })),
  };
}, ids);

const checks = {
  workflow_title_visible: false,
  goal_visible: false,
  parallel_branches_running: false,
  recovery_takeover_visible: false,
  targeted_repair_blocker_visible: false,
  unaffected_branches_accepted: false,
  repaired_branch_running: false,
  approval_required_visible: false,
  final_result_visible: false,
  read_only_requests: false,
  no_manual_reload: false,
  desktop_layout_non_overlapping: false,
  mobile_layout_non_overlapping: false,
};
const observations = [];
const seen = new Set();
const observe = async () => {
  const value = await snapshot();
  const signature = JSON.stringify(value);
  if (!seen.has(signature)) { seen.add(signature); observations.push(value); }
  checks.workflow_title_visible ||= value.workflow.includes("Durable Repository Qualification");
  checks.goal_visible ||= value.goal.includes("Qualify this repository through durable recovery.");
  checks.parallel_branches_running ||= [
    "qualify-documentation", "qualify-tests", "qualify-package",
  ].every((id) => value.nodes[id].state === "running");
  checks.recovery_takeover_visible ||= value.timeline.includes("run_lease_taken_over");
  checks.repaired_branch_running ||= value.nodes["qualify-tests"].state === "running"
    && observations.some((item) => item.nodes["qualify-tests"].blocker.includes("targeted_repair_required"));
  return value;
};

const repairDeadline = Date.now() + 45000;
while (Date.now() < repairDeadline) {
  const value = await observe();
  const tests = value.nodes["qualify-tests"];
  checks.targeted_repair_blocker_visible ||=
    tests.state === "blocked" && tests.blocker.includes("targeted_repair_required");
  checks.unaffected_branches_accepted ||= ["qualify-documentation", "qualify-package"].every(
    (id) => value.nodes[id].state === "settled" && value.nodes[id].admission === "accepted",
  );
  if (checks.targeted_repair_blocker_visible && checks.recovery_takeover_visible) break;
  await new Promise((resolve) => setTimeout(resolve, 40));
}
if (!checks.targeted_repair_blocker_visible) throw new Error("repair blocker not observed");
fs.writeFileSync(repairSeenPath, "seen\n");

const approvalDeadline = Date.now() + 30000;
while (Date.now() < approvalDeadline) {
  const value = await observe();
  const publish = value.nodes["publish-qualification"];
  checks.approval_required_visible ||=
    publish.state === "blocked" && publish.blocker.includes("approval packet not found");
  if (checks.approval_required_visible && checks.repaired_branch_running) break;
  await new Promise((resolve) => setTimeout(resolve, 40));
}
if (!checks.approval_required_visible) throw new Error("approval blocker not observed");
fs.writeFileSync(approvalSeenPath, "seen\n");

const finalDeadline = Date.now() + 30000;
while (Date.now() < finalDeadline) {
  const value = await observe();
  checks.final_result_visible ||=
    value.nodes["finalize-qualification"].state === "settled"
    && value.nodes["finalize-qualification"].admission === "accepted"
    && value.result.includes("Repository qualification published")
    && value.result.includes("durable-repository-qualification.json");
  if (checks.final_result_visible) break;
  await new Promise((resolve) => setTimeout(resolve, 40));
}
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
    node.width > 0 && node.height > 0
    && nodes.slice(index + 1).every((other) => !overlaps(node, other)));
}, ids);
checks.desktop_layout_non_overlapping = await layout();
await page.screenshot({ path: desktopPath, fullPage: false });
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
await new Promise((resolve) => setTimeout(resolve, 250));
checks.mobile_layout_non_overlapping = await layout();
await page.screenshot({ path: mobilePath, fullPage: true });
await browser.close();

const receipt = {
  schema: "tau.durable_qualification_browser_proof.v1",
  status: Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED",
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
