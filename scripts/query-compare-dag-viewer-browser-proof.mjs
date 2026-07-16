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
await page.setViewport({ width: 1720, height: 1200, deviceScaleFactor: 1 });
const methods = [];
const responses = [];
page.on("request", (request) => methods.push(request.method()));
page.on("response", (response) => {
  if (response.url().includes("/api/v1/compare")) responses.push({ url: response.url(), status: response.status() });
});
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:filters"]');

const setValue = async (selector, value) => {
  await page.$eval(selector, (element, next) => {
    const descriptor = Object.getOwnPropertyDescriptor(
      element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
      "value",
    );
    descriptor.set.call(element, next);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
};
const clickButton = async (text) => {
  const clicked = await page.evaluate((label) => {
    const button = [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === label);
    button?.click();
    return Boolean(button);
  }, text);
  if (!clicked) throw new Error(`button missing: ${text}`);
};
const waitForText = async (selector, text) => page.waitForFunction(
  (target, expected) => document.querySelector(target)?.textContent?.includes(expected),
  {}, selector, text,
);

const checks = {
  corrected_node_filter: false,
  correction_event_filter: false,
  open_attention_filter: false,
  filter_url_refresh_parity: false,
  attempt_pair_comparison: false,
  correction_before_after_comparison: false,
  sequence_pair_comparison: false,
  clear_filter_preserves_authoritative_state: false,
  read_only_requests: false,
  top_controls_full_width: false,
  layout_non_overlapping: false,
};

await setValue('[aria-label="Filter IDs, codes, schemas, states, and previews"]', "provider-review");
await setValue('[aria-label="Entity kind"]', "NODE");
await clickButton("Apply");
await waitForText('[data-qid="dag:filter:results"]', "provider-review");
checks.corrected_node_filter = true;
const filteredUrl = page.url();
await page.reload({ waitUntil: "networkidle0", timeout: 15000 });
await waitForText('[data-qid="dag:filter:results"]', "provider-review");
checks.filter_url_refresh_parity = page.url() === filteredUrl;

await setValue('[aria-label="Filter IDs, codes, schemas, states, and previews"]', "correction_state_committed");
await setValue('[aria-label="Entity kind"]', "EVENT");
await setValue('[aria-label="Projected state"]', "");
await clickButton("Apply");
await waitForText('[data-qid="dag:filter:results"]', "EVENT");
checks.correction_event_filter = await page.$eval(
  '[data-qid="dag:filter:results"]',
  (element) => (element.textContent || "").includes("correction_state_committed"),
);

await setValue('[aria-label="Filter IDs, codes, schemas, states, and previews"]', "");
await setValue('[aria-label="Entity kind"]', "ATTENTION");
await setValue('[aria-label="Projected state"]', "OPEN");
await clickButton("Apply");
await waitForText('[data-qid="dag:filter:results"]', "0 matches");
checks.open_attention_filter = true;
await page.click('[aria-label="Clear filters"]');
await page.waitForFunction(() => !window.location.search.includes("filter_"));
checks.clear_filter_preserves_authoritative_state = await page.$eval(
  '[data-qid="dag:status:banner"]',
  (element) => (element.textContent || "").includes("PASS"),
);

await setValue('[aria-label="Comparison kind"]', "ATTEMPT_PAIR");
await setValue('[aria-label="Comparison node"]', "provider-review");
await setValue('[aria-label="Left attempt"]', "1");
await setValue('[aria-label="Right attempt"]', "2");
await clickButton("Compare");
await page.waitForNetworkIdle({ idleTime: 250, timeout: 5000 });
checks.attempt_pair_comparison = await page.$eval(
  '[data-qid="dag:comparison:result"]',
  (element) => (element.textContent || "").includes('"attempt":1')
    && (element.textContent || "").includes('"attempt":2'),
);

await setValue('[aria-label="Comparison kind"]', "CORRECTION_BEFORE_AFTER");
await setValue('[aria-label="Correction incident"]', "incident-61c5df4cb2c1834eb01933fbf87f7dee");
await clickButton("Compare");
await page.waitForNetworkIdle({ idleTime: 250, timeout: 5000 });
checks.correction_before_after_comparison = await page.$eval(
  '[data-qid="dag:comparison:result"]',
  (element) => {
    const text = element.textContent || "";
    return text.includes("REQUESTED") && text.includes("VERIFIED");
  },
);

await setValue('[aria-label="Comparison kind"]', "SEQUENCE_PAIR");
await setValue('[aria-label="Left sequence"]', "9");
await setValue('[aria-label="Right sequence"]', "26");
await clickButton("Compare");
await page.waitForNetworkIdle({ idleTime: 250, timeout: 5000 });
checks.sequence_pair_comparison = await page.$eval(
  '[data-qid="dag:comparison:result"]',
  (element) => {
    const text = element.textContent || "";
    return text.includes('"sequence":9') && text.includes('"sequence":26');
  },
);

await page.evaluate(() => window.scrollTo(0, 0));
await new Promise((resolve) => setTimeout(resolve, 100));
checks.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
checks.top_controls_full_width = await page.evaluate(() => {
  const status = document.querySelector('[data-qid="dag:status:banner"]')?.getBoundingClientRect();
  const sequence = document.querySelector('[data-qid="dag:sequence:navigator"]')?.getBoundingClientRect();
  const filters = document.querySelector('[data-qid="dag:filters"]')?.getBoundingClientRect();
  return Boolean(status && sequence && filters
    && Math.abs(status.top) <= 1
    && Math.abs(sequence.top - status.bottom) <= 1
    && Math.abs(filters.top - sequence.bottom) <= 1
    && status.width >= window.innerWidth - 2
    && sequence.width >= window.innerWidth - 2
    && filters.width >= window.innerWidth - 2);
});
checks.layout_non_overlapping = await page.evaluate(() => {
  const rect = (selector) => document.querySelector(selector)?.getBoundingClientRect();
  const filters = rect('[data-qid="dag:filters"]');
  const graph = rect('[data-qid="dag:workspace:graph"]');
  const inspector = rect('[data-qid="dag:workspace:inspector"]');
  const comparison = rect('[data-qid="dag:comparison"]');
  const timeline = rect('[data-qid="dag:timeline:events"]');
  if (!filters || !graph || !inspector || !comparison || !timeline) return false;
  return filters.bottom <= graph.top + 1
    && graph.right <= inspector.left + 1
    && Math.max(graph.bottom, inspector.bottom) <= comparison.top + 1
    && comparison.bottom <= timeline.top + 1
    && timeline.bottom <= window.innerHeight + 1;
});

await page.screenshot({ path: screenshotPath, fullPage: false });
await browser.close();
const receipt = {
  schema: "tau.dag_viewer_query_compare_browser_proof.v1",
  status: Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: true,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`,
  request_methods: [...new Set(methods)].sort(),
  comparison_responses: responses,
  checks,
};
fs.writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`);
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
