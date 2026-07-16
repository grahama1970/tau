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
await page.setViewport({ width: 1660, height: 1000, deviceScaleFactor: 1 });
const methods = [];
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:provider-review"]', { timeout: 10000 });
await page.waitForFunction(() => document.querySelectorAll('[aria-label="Committed journal sequence"] option').length > 1);

const liveBefore = await page.$eval('[data-qid="dag:node:provider-review"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
  text: element.textContent || "",
}));
const timelineText = await page.$eval('[data-qid="dag:timeline:events"]', (element) => element.textContent || "");
await page.click('[data-qid="dag:inspector:live"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:live"]')?.getAttribute("aria-pressed") === "true");
const liveText = await page.$eval('[data-qid="dag:workspace:inspector-content"]', (element) => element.textContent || "");

const checks = {
  graph_rendered: Boolean(await page.$(".react-flow__viewport")),
  correction_badge_verified: liveBefore.text.toLowerCase().includes("correction verified"),
  scheduler_settled: liveBefore.scheduler === "settled",
  receipt_admission_accepted: liveBefore.admission === "accepted",
  requested_visible: timelineText.includes("REQUESTED"),
  intent_committed_visible: timelineText.includes("INTENT_COMMITTED"),
  started_visible: timelineText.includes("STARTED"),
  applied_visible: timelineText.includes("APPLIED"),
  verified_visible: timelineText.includes("VERIFIED"),
  live_state_has_incident: liveText.includes("tau.correction_incident.v1"),
  live_state_has_action_receipt: liveText.includes("tau.correction_action_receipt.v1"),
  live_state_has_verification: liveText.includes("tau.correction_verification.v1"),
  live_provider_evidence_visible: liveText.includes('"provider_live": true'),
  read_only_requests: false,
  refresh_reconstructed_state: false,
  historical_mode_visible: false,
  historical_applied_visible: false,
  historical_verified_absent: false,
  historical_not_accepted: false,
  historical_url_frozen: false,
  return_to_live_restored_head: false,
  layout_non_overlapping: false,
};

const appliedSequence = await page.evaluate(async () => {
  const response = await fetch("/api/v1/events?after_sequence=0&limit=500");
  const payload = await response.json();
  const applied = payload.events.find((event) => event.event_type === "correction_state_committed" && event.payload.state === "APPLIED");
  return applied?.seq ?? null;
});
if (!appliedSequence) throw new Error("applied correction sequence missing");
await page.select('[aria-label="Committed journal sequence"]', String(appliedSequence));
await page.waitForFunction(
  (sequence) => window.location.search === `?at_sequence=${sequence}`
    && document.querySelector('[data-qid="dag:sequence:navigator"]')?.textContent?.includes("HISTORICAL"),
  {},
  appliedSequence,
);
await page.waitForFunction(() => document.querySelector('[data-qid="dag:node:provider-review"]')?.getAttribute("data-admission-state") !== "accepted");
await page.click('[data-qid="dag:inspector:live"]');
await page.waitForFunction(() => document.querySelector('[data-qid="dag:inspector:live"]')?.getAttribute("aria-pressed") === "true");
const historicalBeforeRefresh = await page.$eval('[data-qid="dag:node:provider-review"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
  text: element.textContent || "",
}));
const historicalText = await page.$eval('[data-qid="dag:workspace:inspector-content"]', (element) => element.textContent || "");
const navigatorText = await page.$eval('[data-qid="dag:sequence:navigator"]', (element) => element.textContent || "");
checks.historical_mode_visible = navigatorText.includes("HISTORICAL") && navigatorText.includes(`#${appliedSequence}`);
checks.historical_applied_visible = historicalText.includes('"state": "APPLIED"');
checks.historical_verified_absent = !historicalText.includes('"state": "VERIFIED"') && !historicalText.includes("tau.correction_verification.v1");
checks.historical_not_accepted = historicalBeforeRefresh.admission !== "accepted";
checks.historical_url_frozen = new URL(page.url()).searchParams.get("at_sequence") === String(appliedSequence);

checks.layout_non_overlapping = await page.evaluate(() => {
  const rect = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.getBoundingClientRect();
  const graph = rect("dag:workspace:graph");
  const inspector = rect("dag:workspace:inspector");
  const inspectorContent = rect("dag:workspace:inspector-content");
  const proofBoundary = rect("dag:workspace:proof-boundary");
  const timeline = rect("dag:timeline:events");
  if (!graph || !inspector || !inspectorContent || !proofBoundary || !timeline) return false;
  return graph.right <= inspector.left + 1
    && Math.max(graph.bottom, inspector.bottom) <= timeline.top + 1
    && timeline.bottom <= window.innerHeight + 1
    && inspectorContent.bottom <= proofBoundary.top + 1;
});

await page.screenshot({ path: screenshotPath, fullPage: false });
await page.reload({ waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:provider-review"]', { timeout: 10000 });
const afterRefresh = await page.$eval('[data-qid="dag:node:provider-review"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
  text: element.textContent || "",
}));
checks.refresh_reconstructed_state = JSON.stringify(historicalBeforeRefresh) === JSON.stringify(afterRefresh)
  && new URL(page.url()).searchParams.get("at_sequence") === String(appliedSequence);
await page.click(".sequence-navigator__live");
await page.waitForFunction(() => !new URL(window.location.href).searchParams.has("at_sequence"));
await page.waitForFunction(() => document.querySelector('[data-qid="dag:node:provider-review"]')?.getAttribute("data-admission-state") === "accepted");
const liveAfter = await page.$eval('[data-qid="dag:node:provider-review"]', (element) => ({
  scheduler: element.getAttribute("data-node-state"),
  admission: element.getAttribute("data-admission-state"),
  text: element.textContent || "",
}));
checks.return_to_live_restored_head = JSON.stringify(liveBefore) === JSON.stringify(liveAfter);
checks.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");

await browser.close();
const screenshotSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`;
const receipt = {
  schema: "tau.self_healing_dag_viewer_browser_proof.v1",
  status: Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: true,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: screenshotSha256,
  request_methods: [...new Set(methods)].sort(),
  checks,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
