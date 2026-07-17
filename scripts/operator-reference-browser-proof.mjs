import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const puppeteer = require(`${process.env.NODE_PATH}/puppeteer`);

const [
  urlPath,
  readyPath,
  scenario,
  desktopScreenshotPath,
  mobileScreenshotPath,
  outputPath,
] = process.argv.slice(2);
if (
  !urlPath
  || !readyPath
  || !["positive", "negative"].includes(scenario)
  || !desktopScreenshotPath
  || !mobileScreenshotPath
  || !outputPath
) {
  throw new Error("operator-reference browser-proof arguments missing or invalid");
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
  throw new Error("operator-reference viewer URL was not published");
}
const url = fs.readFileSync(urlPath, "utf8").trim();
if (!url.startsWith("http://127.0.0.1:")) {
  await browser.close();
  throw new Error("operator-reference viewer URL is not loopback HTTP");
}

const nodeIds = [
  "collect-operator-sources",
  "capture-operator-cli",
  "compose-operator-reference",
  "validate-operator-reference",
];
const requests = [];
let mainFrameNavigations = 0;
page.on("request", (request) => requests.push({ method: request.method(), url: request.url() }));
page.on("framenavigated", (frame) => {
  if (frame === page.mainFrame()) mainFrameNavigations += 1;
});

await page.goto(url, { waitUntil: "networkidle0", timeout: 15000 });
await page.waitForSelector('[data-qid="dag:overview"]', { timeout: 10000 });
await page.waitForSelector('[data-qid="dag:node:collect-operator-sources"]', { timeout: 10000 });

const observations = [];
const seen = new Set();
const checks = {
  workflow_title_visible: false,
  goal_summary_visible: false,
  collect_running_observed: false,
  collect_accepted_observed: false,
  capture_running_observed: false,
  capture_accepted_observed: false,
  compose_running_observed: false,
  compose_accepted_observed: false,
  validate_running_observed: false,
  validate_accepted_observed: false,
  final_result_visible: false,
  result_artifact_refs_visible: false,
  validate_blocked_observed: false,
  required_workflow_missing_exact: false,
  first_three_accepted: false,
  final_result_absent: false,
  no_manual_reload: false,
  read_only_requests: false,
  desktop_layout_non_overlapping: false,
  mobile_layout_non_overlapping: false,
};

const snapshot = async () => page.evaluate((ids) => {
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
    nodes: Object.fromEntries(ids.map((id) => [id, node(id)])),
  };
}, nodeIds);

const hasExactCode = (text, code) => text.split(/[^a-zA-Z0-9_-]+/).includes(code);
const startedAt = Date.now();
const deadline = startedAt + 30000;
let latest = null;
while (Date.now() < deadline) {
  latest = await snapshot();
  const signature = JSON.stringify(latest);
  if (!seen.has(signature)) {
    seen.add(signature);
    observations.push({ elapsed_ms: Date.now() - startedAt, ...latest });
  }

  const collect = latest.nodes["collect-operator-sources"];
  const capture = latest.nodes["capture-operator-cli"];
  const compose = latest.nodes["compose-operator-reference"];
  const validate = latest.nodes["validate-operator-reference"];
  checks.workflow_title_visible ||= latest.workflow.includes("Tau Operator Reference");
  checks.goal_summary_visible ||=
    latest.goal.includes("Produce a validated operator reference for this Tau installation.");
  checks.collect_running_observed ||= collect.state === "running";
  checks.collect_accepted_observed ||=
    collect.state === "settled" && collect.admission === "accepted";
  checks.capture_running_observed ||= capture.state === "running";
  checks.capture_accepted_observed ||=
    capture.state === "settled" && capture.admission === "accepted";
  checks.compose_running_observed ||= compose.state === "running";
  checks.compose_accepted_observed ||=
    compose.state === "settled" && compose.admission === "accepted";
  checks.validate_running_observed ||= validate.state === "running";
  checks.validate_accepted_observed ||=
    validate.state === "settled" && validate.admission === "accepted";
  checks.final_result_visible ||= latest.result.length > 0;
  checks.result_artifact_refs_visible ||=
    latest.result.includes("tau-operator-reference.json")
    && latest.result.includes("tau-operator-reference.md")
    && latest.result.includes("sha256:");
  checks.validate_blocked_observed ||= validate.state === "blocked";
  checks.required_workflow_missing_exact ||=
    hasExactCode(latest.blocker, "required_workflow_missing")
    || hasExactCode(validate.text, "required_workflow_missing");
  checks.first_three_accepted ||=
    [collect, capture, compose].every(
      (node) => node.state === "settled" && node.admission === "accepted",
    );

  const positiveDone = [
    checks.workflow_title_visible,
    checks.goal_summary_visible,
    checks.collect_running_observed,
    checks.collect_accepted_observed,
    checks.capture_running_observed,
    checks.capture_accepted_observed,
    checks.compose_running_observed,
    checks.compose_accepted_observed,
    checks.validate_running_observed,
    checks.validate_accepted_observed,
    checks.final_result_visible,
    checks.result_artifact_refs_visible,
  ].every(Boolean);
  const negativeDone = [
    checks.workflow_title_visible,
    checks.goal_summary_visible,
    checks.first_three_accepted,
    checks.validate_blocked_observed,
    checks.required_workflow_missing_exact,
  ].every(Boolean);
  if ((scenario === "positive" && positiveDone) || (scenario === "negative" && negativeDone)) break;
  await new Promise((resolve) => setTimeout(resolve, 50));
}

checks.final_result_absent = scenario === "negative"
  && observations.every((item) => !item.result.includes("tau-operator-reference."));
checks.no_manual_reload = mainFrameNavigations === 1;
checks.read_only_requests = requests.length > 0 && requests.every(({ method }) => method === "GET");

const layoutNonOverlapping = async () => page.evaluate((ids) => {
  const rectangle = (qid) => document.querySelector(`[data-qid="${qid}"]`)?.getBoundingClientRect();
  const overview = rectangle("dag:overview");
  const graph = rectangle("dag:workspace:graph");
  const nodes = ids.map((id) => rectangle(`dag:node:${id}`));
  if (!overview || !graph || nodes.some((node) => !node)) return false;
  const overlaps = (left, right) =>
    left.left < right.right && left.right > right.left
    && left.top < right.bottom && left.bottom > right.top;
  return overview.bottom <= graph.top + 1
    && nodes.every((node, index) => node.width > 0 && node.height > 0
      && nodes.slice(index + 1).every((other) => !overlaps(node, other)));
}, nodeIds);

checks.desktop_layout_non_overlapping = await layoutNonOverlapping();
await page.screenshot({ path: desktopScreenshotPath, fullPage: false });
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
await new Promise((resolve) => setTimeout(resolve, 250));
checks.mobile_layout_non_overlapping = await layoutNonOverlapping();
await page.screenshot({ path: mobileScreenshotPath, fullPage: true });
await browser.close();

const required = scenario === "positive"
  ? [
      "workflow_title_visible",
      "goal_summary_visible",
      "collect_running_observed",
      "collect_accepted_observed",
      "capture_running_observed",
      "capture_accepted_observed",
      "compose_running_observed",
      "compose_accepted_observed",
      "validate_running_observed",
      "validate_accepted_observed",
      "final_result_visible",
      "result_artifact_refs_visible",
      "no_manual_reload",
      "read_only_requests",
      "desktop_layout_non_overlapping",
      "mobile_layout_non_overlapping",
    ]
  : [
      "workflow_title_visible",
      "goal_summary_visible",
      "first_three_accepted",
      "validate_blocked_observed",
      "required_workflow_missing_exact",
      "final_result_absent",
      "no_manual_reload",
      "read_only_requests",
      "desktop_layout_non_overlapping",
      "mobile_layout_non_overlapping",
    ];
const status = required.every((key) => checks[key]) ? "PASS" : "BLOCKED";
const digest = (path) => `sha256:${createHash("sha256").update(fs.readFileSync(path)).digest("hex")}`;
const receipt = {
  schema: "tau.operator_reference_browser_proof.v1",
  scenario,
  status,
  mocked: false,
  live: true,
  provider_live: false,
  url,
  desktop_screenshot: desktopScreenshotPath,
  desktop_screenshot_sha256: digest(desktopScreenshotPath),
  mobile_screenshot: mobileScreenshotPath,
  mobile_screenshot_sha256: digest(mobileScreenshotPath),
  request_methods: [...new Set(requests.map(({ method }) => method))].sort(),
  requests,
  main_frame_navigation_count: mainFrameNavigations,
  observations,
  final_observation: latest,
  checks,
  proof_scope: {
    proves: [
      "The local browser observed persisted workflow transitions through the read-only viewer.",
      "Desktop and mobile rendered states were captured without overlapping workflow nodes.",
    ],
    does_not_prove: [
      "Provider or model execution quality.",
      "Network-backed documentation freshness.",
      "Production deployment readiness.",
    ],
  },
};
fs.writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`);
console.log(JSON.stringify(receipt, null, 2));
process.exit(status === "PASS" ? 0 : 1);
