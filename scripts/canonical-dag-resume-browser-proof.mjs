import fs from "node:fs";
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
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:node:discover"]', { timeout: 10000 });

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
};
const states = new Set();
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
  observed.goal_visible ||= state.goal.includes("Tau lets a human launch");
  states.add(JSON.stringify([
    state.discover.state,
    state.build.state,
    state.test.state,
    state.document.state,
    state.reconcile.state,
    state.release.state,
  ]));
  observed.progressed_without_reload ||= states.size >= 4;
  observed.concurrent_branches_running ||=
    state.build.state === "running"
    && state.test.state === "running"
    && state.document.state === "running";
  const blocked = state.banner.includes("BLOCKED") && state.reconcile.state === "blocked";
  observed.targeted_repair_block_visible ||= blocked;
  observed.unaffected_work_remained_accepted ||=
    blocked
    && [state.discover, state.build, state.test, state.document]
      .every((node) => node.admission === "accepted");
  observed.resumed_to_completion ||=
    observed.targeted_repair_block_visible
    && state.reconcile.state === "settled"
    && state.reconcile.admission === "accepted";
  observed.human_release_accepted ||=
    state.release.state === "settled" && state.release.admission === "accepted";
  observed.final_pass_visible ||= state.banner.includes("COMPLETE") && state.banner.includes("PASS");
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

observed.read_only_requests = methods.length > 0 && methods.every((method) => method === "GET");
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
  request_methods: [...new Set(methods)].sort(),
  observed_state_count: states.size,
  checks: observed,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
