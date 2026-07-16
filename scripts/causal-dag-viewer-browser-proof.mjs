import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);
const [url, screenshotPath, outputPath] = process.argv.slice(2);
if (!url || !screenshotPath || !outputPath) throw new Error("browser-proof arguments missing");

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 1 });
const methods = [];
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:decisions:rail"]', { timeout: 10000 });

const checks = {
  graph_rendered: await page.$(".react-flow__viewport") !== null,
  attention_visible: await page.$eval(
    '[data-qid="dag:attention:rail"]',
    (element) => (element.textContent || "").includes("join_all_success_not_met"),
  ),
  selected_and_skipped_route_visible: await page.$eval(
    '[data-qid^="dag:decision:route:"]',
    (element) => (element.textContent || "").includes("2 selected · 1 skipped"),
  ),
  join_contributions_visible: await page.$eval(
    '[data-qid^="dag:decision:join:"]',
    (element) => (element.textContent || "").includes("3 contributions · block"),
  ),
  route_cause_visible: false,
  join_cause_visible: false,
  attention_cause_visible: false,
  refresh_reconstructed_state: false,
  read_only_requests: false,
  layout_non_overlapping: false,
  graph_nodes_fully_visible: false,
};

await page.click('[data-qid^="dag:decision:route:"]');
await page.waitForFunction(
  () => document.querySelector('[data-qid="dag:causal:details"]')?.textContent?.includes("route_selected"),
);
checks.route_cause_visible = await page.$eval(
  '[data-qid="dag:causal:details"]',
  (element) => {
    const text = element.textContent || "";
    return text.includes("route_selected") && text.includes("TRANSITION_RECEIPT");
  },
);

await page.click('[data-qid^="dag:decision:join:"]');
await page.waitForFunction(
  () => document.querySelector('[data-qid="dag:causal:details"]')?.textContent?.includes("join_all_success_not_met"),
);
checks.join_cause_visible = await page.$eval(
  '[data-qid="dag:causal:details"]',
  (element) => (element.textContent || "").includes("join_all_success_not_met"),
);

await page.click(".attention-item");
await page.waitForFunction(
  () => document.querySelector('[data-qid="dag:causal:details"]')?.textContent?.includes("attention-"),
);
checks.attention_cause_visible = await page.$eval(
  '[data-qid="dag:causal:details"]',
  (element) => (element.textContent || "").includes("join_all_success_not_met"),
);

const beforeRefresh = await page.$eval(".dag-app", (element) => element.textContent);
await page.reload({ waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:decisions:rail"]', { timeout: 10000 });
const afterRefresh = await page.$eval(".dag-app", (element) => element.textContent);
checks.refresh_reconstructed_state = beforeRefresh?.includes("join_all_success_not_met") === true
  && afterRefresh?.includes("join_all_success_not_met") === true
  && afterRefresh?.includes("2 selected · 1 skipped") === true;
checks.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
checks.layout_non_overlapping = await page.evaluate(() => {
  const rect = (selector) => document.querySelector(selector)?.getBoundingClientRect();
  const graph = rect('[data-qid="dag:workspace:graph"]');
  const inspector = rect('[data-qid="dag:workspace:inspector"]');
  const timeline = rect('[data-qid="dag:timeline:events"]');
  const attention = rect('[data-qid="dag:attention:rail"]');
  const decisions = rect('[data-qid="dag:decisions:rail"]');
  if (!graph || !inspector || !timeline || !attention || !decisions) return false;
  return attention.bottom <= decisions.top + 1
    && decisions.bottom <= graph.top + 1
    && graph.right <= inspector.left + 1
    && Math.max(graph.bottom, inspector.bottom) <= timeline.top + 1
    && timeline.bottom <= window.innerHeight + 1;
});
checks.graph_nodes_fully_visible = await page.evaluate(() => {
  const canvas = document.querySelector('[data-qid="dag:workspace:canvas"]')?.getBoundingClientRect();
  const nodes = [...document.querySelectorAll(".tau-node")].map((node) => node.getBoundingClientRect());
  if (!canvas || nodes.length === 0) return false;
  return nodes.every((node) => node.left >= canvas.left - 1
    && node.right <= canvas.right + 1
    && node.top >= canvas.top - 1
    && node.bottom <= canvas.bottom + 1);
});

await page.screenshot({ path: screenshotPath, fullPage: false });
await browser.close();
const receipt = {
  schema: "tau.dag_viewer_causal_browser_proof.v1",
  status: Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`,
  request_methods: [...new Set(methods)].sort(),
  checks,
};
fs.writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`);
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
