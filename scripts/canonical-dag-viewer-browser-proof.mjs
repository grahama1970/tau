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
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
const methods = [];
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:plan"]', { timeout: 10000 });

const observed = {
  react_flow_rendered: false,
  progressed_without_reload: false,
  concurrent_branches_running: false,
  bounded_retry_visible: false,
  approval_gated_release_accepted: false,
  final_pass_visible: false,
  read_only_requests: false,
  layout_non_overlapping: false,
};
const states = new Set();
const deadline = Date.now() + 45000;
while (Date.now() < deadline) {
  const state = await page.evaluate(() => {
    const node = (id) => document.querySelector(`[data-qid="dag:node:${id}"]`);
    const banner = document.querySelector('[data-qid="dag:status:banner"]');
    const value = (id) => ({
      state: node(id)?.getAttribute("data-node-state"),
      admission: node(id)?.getAttribute("data-admission-state"),
      text: node(id)?.textContent || "",
    });
    return {
      graph: Boolean(document.querySelector(".react-flow__viewport")),
      banner: banner?.textContent || "",
      plan: value("plan"),
      implement: value("implement"),
      test: value("test"),
      review: value("review"),
      release: value("release"),
    };
  });
  observed.react_flow_rendered ||= state.graph;
  states.add(JSON.stringify([
    state.plan.state,
    state.implement.state,
    state.test.state,
    state.review.state,
    state.release.state,
  ]));
  observed.progressed_without_reload ||= states.size >= 3;
  observed.concurrent_branches_running ||=
    state.implement.state === "running" && state.test.state === "running";
  observed.bounded_retry_visible ||=
    state.review.text.includes("attempt 2/2") || state.review.text.includes("attempt 2 / 2");
  observed.approval_gated_release_accepted ||=
    state.release.state === "settled" && state.release.admission === "accepted";
  observed.final_pass_visible ||= state.banner.includes("COMPLETE") && state.banner.includes("PASS");
  if (Object.values(observed).slice(0, 6).every(Boolean)) break;
  await new Promise((resolve) => setTimeout(resolve, 100));
}

observed.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
observed.layout_non_overlapping = await page.evaluate(() => {
  const ids = ["plan", "implement", "test", "review", "release"];
  const rects = ids.map((id) =>
    document.querySelector(`[data-qid="dag:node:${id}"]`)?.getBoundingClientRect(),
  );
  if (rects.some((rect) => !rect)) return false;
  const overlaps = (a, b) =>
    a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  return rects.every((rect, index) =>
    rect.width > 0 && rect.height > 0
      && rect.left >= 0 && rect.right <= window.innerWidth
      && rects.slice(index + 1).every((other) => !overlaps(rect, other)),
  );
});

await page.screenshot({ path: screenshotPath, fullPage: false });
await browser.close();
const screenshotSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`;
const receipt = {
  schema: "tau.canonical_dag_viewer_browser_proof.v1",
  status: Object.values(observed).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: screenshotSha256,
  request_methods: [...new Set(methods)].sort(),
  observed_state_count: states.size,
  checks: observed,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
