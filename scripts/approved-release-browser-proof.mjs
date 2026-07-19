import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
const [urlPath, readyPath, approvalSeenPath, desktopPath, mobilePath, outputPath] = process.argv.slice(2);
const ids = [
  "prepare-release",
  "draft-release-notes",
  "build-release-manifest",
  "verify-release-policy",
  "assemble-release-bundle",
  "publish-approved-release",
  "finalize-approved-release",
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
    transaction: text("dag:transaction:attempts"),
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
  prepare_running: false,
  parallel_branches_running: false,
  revise_then_pass_visible: false,
  approval_required_visible: false,
  no_publication_before_approval: false,
  publish_running_after_resume: false,
  final_result_visible: false,
  final_transaction_evidence_visible: false,
  read_only_requests: false,
  no_manual_reload: false,
  desktop_layout_non_overlapping: false,
  mobile_layout_non_overlapping: false,
};
const observations = [];
const seen = new Set();
const approvalDeadline = Date.now() + 35000;
while (Date.now() < approvalDeadline) {
  const value = await snapshot();
  const signature = JSON.stringify(value);
  if (!seen.has(signature)) { seen.add(signature); observations.push(value); }
  checks.workflow_title_visible ||= value.workflow.includes("Approved Release Bundle");
  checks.goal_visible ||= value.goal.includes("Publish an approved release bundle.");
  checks.prepare_running ||= value.nodes["prepare-release"].state === "running";
  checks.parallel_branches_running ||= [
    "draft-release-notes", "build-release-manifest", "verify-release-policy",
  ].every((id) => value.nodes[id].state === "running");
  checks.revise_then_pass_visible ||=
    value.transaction.includes("Attempt 1") && value.transaction.includes("REVISE")
    && value.transaction.includes("Attempt 2") && value.transaction.includes("PASS");
  const publish = value.nodes["publish-approved-release"];
  checks.approval_required_visible ||=
    publish.state === "blocked" && publish.blocker.includes("approval packet not found");
  if (checks.approval_required_visible && checks.revise_then_pass_visible) break;
  await new Promise((resolve) => setTimeout(resolve, 40));
}
checks.no_publication_before_approval = observations.every(
  (item) => item.nodes["finalize-approved-release"].state !== "settled",
);
if (!checks.approval_required_visible) throw new Error("approval boundary not observed");
fs.writeFileSync(approvalSeenPath, "seen\n");

const finalDeadline = Date.now() + 30000;
while (Date.now() < finalDeadline) {
  const value = await snapshot();
  const signature = JSON.stringify(value);
  if (!seen.has(signature)) { seen.add(signature); observations.push(value); }
  checks.publish_running_after_resume ||= value.nodes["publish-approved-release"].state === "running";
  checks.final_result_visible ||=
    value.nodes["finalize-approved-release"].state === "settled"
    && value.nodes["finalize-approved-release"].admission === "accepted"
    && value.result.includes("Approved release bundle published")
    && value.result.includes("approved-release-bundle.json")
    && value.result.includes("approved-release-bundle.md");
  checks.final_transaction_evidence_visible ||=
    (value.transaction.match(/Creator PASS/g) || []).length === 2
    && (value.transaction.match(/Validator PASS/g) || []).length === 2
    && value.transaction.includes("Reviewer REVISE")
    && value.transaction.includes("Reviewer PASS")
    && !value.transaction.includes("Creator pending")
    && !value.transaction.includes("Validator pending");
  if (
    checks.final_result_visible
    && checks.publish_running_after_resume
    && checks.final_transaction_evidence_visible
  ) break;
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

const required = Object.keys(checks);
const receipt = {
  schema: "tau.approved_release_browser_proof.v1",
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
