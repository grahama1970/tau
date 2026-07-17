import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);

const [urlPath, readyPath, scenario, screenshotPath, outputPath] = process.argv.slice(2);
if (
  !urlPath
  || !readyPath
  || !["positive", "negative"].includes(scenario)
  || !screenshotPath
  || !outputPath
) {
  throw new Error("repository-readiness browser-proof arguments missing or invalid");
}

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
if (!fs.existsSync(urlPath)) {
  await browser.close();
  throw new Error("repository-readiness viewer URL was not published");
}
const url = fs.readFileSync(urlPath, "utf8").trim();
if (!url.startsWith("http://127.0.0.1:")) {
  await browser.close();
  throw new Error("repository-readiness viewer URL is not loopback HTTP");
}

const requests = [];
let mainFrameNavigations = 0;
page.on("request", (request) => requests.push({ method: request.method(), url: request.url() }));
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) mainFrameNavigations += 1;
});

await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:overview"]', { timeout: 10000 });
await page.waitForSelector('[data-qid="dag:node:inspect-repository"]', { timeout: 10000 });

const observations = [];
const seen = new Set();
const checks = {
  workflow_title_visible: false,
  goal_summary_visible: false,
  inspect_running_observed: false,
  inspect_accepted_observed: false,
  validate_running_observed: false,
  validate_accepted_observed: false,
  publish_running_observed: false,
  publish_accepted_observed: false,
  final_result_visible: false,
  result_artifact_refs_visible: false,
  validate_blocked_observed: false,
  dirty_repository_visible: false,
  publish_not_executed: false,
  final_result_absent: false,
  no_manual_reload: false,
  read_only_requests: false,
  layout_non_overlapping: false,
};

const startedAt = Date.now();
const deadline = startedAt + 30000;
let latest = null;
while (Date.now() < deadline) {
  latest = await page.evaluate(() => {
    const text = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.textContent?.trim() || "";
    const node = (nodeId) => {
      const element = document.querySelector(`[data-qid="dag:node:${nodeId}"]`);
      return {
        state: element?.getAttribute("data-node-state") || null,
        admission: element?.getAttribute("data-admission-state") || null,
        text: element?.textContent?.trim() || "",
      };
    };
    return {
      workflow: text("dag:overview:workflow"),
      goal: text("dag:overview:goal"),
      current: text("dag:overview:current"),
      result: text("dag:overview:result"),
      blocker: text("dag:overview:blocker"),
      nodes: {
        inspect: node("inspect-repository"),
        validate: node("validate-readiness"),
        publish: node("publish-readiness"),
      },
    };
  });

  const signature = JSON.stringify(latest);
  if (!seen.has(signature)) {
    seen.add(signature);
    observations.push({ elapsed_ms: Date.now() - startedAt, ...latest });
  }

  checks.workflow_title_visible ||= latest.workflow.includes("Repository Readiness");
  checks.goal_summary_visible ||=
    latest.goal.includes("Determine whether this checkout is ready for focused work.");
  checks.inspect_running_observed ||= latest.nodes.inspect.state === "running";
  checks.inspect_accepted_observed ||=
    latest.nodes.inspect.state === "settled" && latest.nodes.inspect.admission === "accepted";
  checks.validate_running_observed ||= latest.nodes.validate.state === "running";
  checks.validate_accepted_observed ||=
    latest.nodes.validate.state === "settled" && latest.nodes.validate.admission === "accepted";
  checks.publish_running_observed ||= latest.nodes.publish.state === "running";
  checks.publish_accepted_observed ||=
    latest.nodes.publish.state === "settled" && latest.nodes.publish.admission === "accepted";
  checks.final_result_visible ||=
    latest.result.includes("Repository is ready for focused work.") && latest.result.includes("READY");
  checks.result_artifact_refs_visible ||=
    latest.result.includes("repository-readiness.json")
    && latest.result.includes("repository-readiness.md")
    && latest.result.includes("sha256:");
  checks.validate_blocked_observed ||= latest.nodes.validate.state === "blocked";
  checks.dirty_repository_visible ||=
    latest.blocker.includes("dirty_repository") || latest.nodes.validate.text.includes("dirty_repository");

  const positiveDone = [
    checks.workflow_title_visible,
    checks.goal_summary_visible,
    checks.inspect_running_observed,
    checks.inspect_accepted_observed,
    checks.validate_running_observed,
    checks.validate_accepted_observed,
    checks.publish_running_observed,
    checks.publish_accepted_observed,
    checks.final_result_visible,
    checks.result_artifact_refs_visible,
  ].every(Boolean);
  const negativeDone = [
    checks.workflow_title_visible,
    checks.goal_summary_visible,
    checks.inspect_accepted_observed,
    checks.validate_running_observed,
    checks.validate_blocked_observed,
    checks.dirty_repository_visible,
  ].every(Boolean);
  if ((scenario === "positive" && positiveDone) || (scenario === "negative" && negativeDone)) break;
  await new Promise((resolve) => setTimeout(resolve, 50));
}

checks.publish_not_executed = scenario === "negative"
  && !observations.some((item) => ["running", "settled"].includes(item.nodes.publish.state));
checks.final_result_absent = scenario === "negative"
  && observations.every((item) => !item.result.includes("Repository is ready for focused work."));
checks.no_manual_reload = mainFrameNavigations === 1;
checks.read_only_requests = requests.length > 0 && requests.every(({ method }) => method === "GET");
checks.layout_non_overlapping = await page.evaluate(() => {
  const element = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.getBoundingClientRect();
  const overview = element("dag:overview");
  const graph = element("dag:workspace:graph");
  const nodes = [
    element("dag:node:inspect-repository"),
    element("dag:node:validate-readiness"),
    element("dag:node:publish-readiness"),
  ];
  if (!overview || !graph || nodes.some((node) => !node)) return false;
  const overlaps = (left, right) =>
    left.left < right.right && left.right > right.left
    && left.top < right.bottom && left.bottom > right.top;
  return overview.bottom <= graph.top + 1
    && nodes.every((node, index) => node.width > 0 && node.height > 0
      && nodes.slice(index + 1).every((other) => !overlaps(node, other)));
});

await page.screenshot({ path: screenshotPath, fullPage: false });
await browser.close();

const required = scenario === "positive"
  ? [
      "workflow_title_visible",
      "goal_summary_visible",
      "inspect_running_observed",
      "inspect_accepted_observed",
      "validate_running_observed",
      "validate_accepted_observed",
      "publish_running_observed",
      "publish_accepted_observed",
      "final_result_visible",
      "result_artifact_refs_visible",
      "no_manual_reload",
      "read_only_requests",
      "layout_non_overlapping",
    ]
  : [
      "workflow_title_visible",
      "goal_summary_visible",
      "inspect_accepted_observed",
      "validate_running_observed",
      "validate_blocked_observed",
      "dirty_repository_visible",
      "publish_not_executed",
      "final_result_absent",
      "no_manual_reload",
      "read_only_requests",
      "layout_non_overlapping",
    ];
const status = required.every((key) => checks[key]) ? "PASS" : "BLOCKED";
const screenshotSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`;
const receipt = {
  schema: "tau.repository_readiness_browser_proof.v1",
  scenario,
  status,
  mocked: false,
  live: true,
  provider_live: false,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: screenshotSha256,
  request_methods: [...new Set(requests.map(({ method }) => method))].sort(),
  requests,
  main_frame_navigation_count: mainFrameNavigations,
  observations,
  final_observation: latest,
  checks,
};
fs.writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`);
console.log(JSON.stringify(receipt, null, 2));
process.exit(status === "PASS" ? 0 : 1);
