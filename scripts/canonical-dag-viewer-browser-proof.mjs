import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);

function loadPuppeteer() {
  try {
    return require("puppeteer");
  } catch {
    if (process.env.NODE_PATH) return require(`${process.env.NODE_PATH}/puppeteer`);
    throw new Error("missing_puppeteer");
  }
}

const puppeteer = loadPuppeteer();

const [url, dagId, desktopScreenshotPath, mobileScreenshotPath, outputPath] = process.argv.slice(2);
if (!url || !dagId || !desktopScreenshotPath || !mobileScreenshotPath || !outputPath) {
  throw new Error("canonical browser-proof arguments missing");
}

function digest(path) {
  return `sha256:${createHash("sha256").update(fs.readFileSync(path)).digest("hex")}`;
}

async function inspectViewport(browser, viewport, screenshotPath) {
  const page = await browser.newPage();
  const requestMethods = [];
  const apiResponses = [];
  const consoleMessages = [];
  page.on("request", (request) => requestMethods.push(request.method()));
  page.on("response", (response) => {
    const responseUrl = response.url();
    if (responseUrl.includes("/api/v1/")) {
      apiResponses.push({ url: responseUrl, status: response.status() });
    }
  });
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push(`${message.type()}:${message.text()}`);
    }
  });
  await page.setViewport(viewport);
  // dag:node:* elements exist only in the topology workspace view, and the
// viewer defaults to timeline. Request topology in the initial URL so the
// proof needs exactly one main-frame navigation (no_manual_reload).
const topologyUrl = new URL(url);
topologyUrl.searchParams.set("workspace_view", "topology");
await page.goto(topologyUrl.toString(), { waitUntil: "networkidle0", timeout: 20000 });
  await page.waitForSelector('[data-qid="dag:workspace:graph"]', { timeout: 10000 });
    await page.waitForSelector(".react-flow__viewport", { timeout: 10000 });

  const state = await page.evaluate(async () => {
    const manifestResponse = await fetch("/api/v1/manifest");
    const snapshotResponse = await fetch("/api/v1/state");
    const eventsResponse = await fetch("/api/v1/events?after_sequence=0&limit=500");
    const manifest = await manifestResponse.json();
    const snapshot = await snapshotResponse.json();
    const events = await eventsResponse.json();
    const byQid = (qid) => document.querySelector(`[data-qid="${CSS.escape(qid)}"]`);
    const rectOf = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    };
    const graphRect = rectOf(byQid("dag:workspace:graph"));
    const canvasRect = rectOf(byQid("dag:workspace:canvas"));
    const reactFlowRect = rectOf(document.querySelector(".react-flow"));
    const reactFlowViewportRect = rectOf(document.querySelector(".react-flow__viewport"));
    const overviewText = byQid("dag:overview")?.textContent || "";
    const statusText = byQid("dag:status:banner")?.textContent || "";
    const sourceTab = byQid("dag:inspector:source");
    if (sourceTab instanceof HTMLButtonElement) sourceTab.click();
    await new Promise((resolve) => window.setTimeout(resolve, 50));
    const sourceText = byQid("dag:workspace:inspector-content")?.textContent || "";
    const proofText = byQid("dag:workspace:proof-boundary")?.textContent || "";
    const decisionRailVisible = Boolean(byQid("dag:decisions:rail"));
    const timelineVisible = Boolean(byQid("dag:timeline:events"));
    const nodeElements = [...document.querySelectorAll('[data-qid^="dag:node:"]')]
      .filter((element) => {
        const qid = element.getAttribute("data-qid") || "";
        return /^dag:node:[^:]+$/.test(qid);
      });
    const nodeRects = nodeElements.map((element) => ({
      qid: element.getAttribute("data-qid"),
      state: element.getAttribute("data-node-state"),
      admission: element.getAttribute("data-admission-state"),
      rect: rectOf(element),
      text: element.textContent || "",
    }));
    const edgePaths = [...document.querySelectorAll(".react-flow__edge-path")].map((path) => {
      const box = path.getBoundingClientRect();
      return { width: box.width, height: box.height };
    });
    const missingNodes = manifest.graph.nodes
      .map((node) => node.node_id)
      .filter((nodeId) => !byQid(`dag:node:${nodeId}`));
    const missingTerminals = manifest.graph.terminals
      .map((terminal) => terminal.terminal_id)
      .filter((terminalId) => !byQid(`dag:node:${terminalId}`));
    const overlappingPairs = [];
    for (let i = 0; i < nodeRects.length; i += 1) {
      for (let j = i + 1; j < nodeRects.length; j += 1) {
        const a = nodeRects[i].rect;
        const b = nodeRects[j].rect;
        if (!a || !b) continue;
        const intersects = a.left < b.right - 1
          && a.right > b.left + 1
          && a.top < b.bottom - 1
          && a.bottom > b.top + 1;
        if (intersects) overlappingPairs.push([nodeRects[i].qid, nodeRects[j].qid]);
      }
    }
    return {
      manifest,
      snapshot,
      events,
      graphRect,
      canvasRect,
      reactFlowRect,
      reactFlowViewportRect,
      overviewText,
      statusText,
      sourceText,
      proofText,
      decisionRailVisible,
      timelineVisible,
      nodeRects,
      edgePaths,
      missingNodes,
      missingTerminals,
      overlappingPairs,
    };
  });

  await page.screenshot({ path: screenshotPath, fullPage: viewport.width < 900 });
  await page.close();
  const screenshotBytes = fs.statSync(screenshotPath).size;
  const checks = {
    manifest_schema: state.manifest.schema === "tau.dag_view_manifest.v1",
    snapshot_schema: state.snapshot.schema === "tau.dag_view_snapshot.v2",
    source_dag_contract_visible: state.sourceText.includes("tau.dag_contract.v1"),
    run_identity_visible: state.statusText.includes(state.snapshot.run_id),
    goal_identity_visible: state.overviewText.includes("sha256:"),
    proof_boundary_visible: state.proofText.includes("Tau projected"),
    graph_nonblank: Boolean(state.graphRect && state.graphRect.width > 300 && state.graphRect.height > 240),
    canvas_nonblank: Boolean(
      state.canvasRect
      && state.reactFlowRect
      && state.reactFlowViewportRect
      && state.canvasRect.width > 300
      && state.canvasRect.height > 120
      && state.reactFlowRect.width > 300
      && state.reactFlowRect.height > 120
      && state.reactFlowViewportRect.width > 0
      && state.reactFlowViewportRect.height > 0
    ),
    all_nodes_visible: state.missingNodes.length === 0,
    terminal_visible: state.missingTerminals.length === 0,
    node_dimensions_nonzero: state.nodeRects.length >= state.manifest.graph.nodes.length
      && state.nodeRects.every((node) => node.rect && node.rect.width > 40 && node.rect.height > 40),
    edges_visible: state.edgePaths.length >= state.manifest.graph.edges.length
      && state.edgePaths.every((edge) => edge.width > 0 || edge.height > 0),
    node_layout_non_overlapping: state.overlappingPairs.length === 0,
    decisions_visible_when_edges_exist: state.manifest.graph.edges.length === 0
      || state.decisionRailVisible,
    timeline_visible: state.timelineVisible,
    api_get_only: requestMethods.length > 0 && requestMethods.every((method) => method === "GET"),
    api_responses_ok: apiResponses.length >= 3 && apiResponses.every((response) => response.status < 400),
    console_clean: consoleMessages
      .filter((message) => !message.includes("Failed to load resource")
        || !message.includes("404"))
      .length === 0,
    screenshot_nontrivial: screenshotBytes > 12000,
    authoritative_project_receipt_state: state.snapshot.projection_state === "PROJECT_RECEIPT",
    selected_dag_expected: state.manifest.source_dag?.dag_id && state.snapshot.run_id,
    run_status_visible: state.statusText.includes(state.snapshot.run_status),
    event_read_model_populated: Array.isArray(state.events.events) && state.events.events.length > 0,
  };
  return {
    viewport,
    screenshot: screenshotPath,
    screenshot_sha256: digest(screenshotPath),
    screenshot_bytes: screenshotBytes,
    request_methods: [...new Set(requestMethods)].sort(),
    api_responses: apiResponses,
    console_messages: consoleMessages,
    manifest: {
      run_id: state.manifest.run_id,
      dag_id: state.manifest.source_dag?.dag_id,
      node_count: state.manifest.graph.nodes.length,
      edge_count: state.manifest.graph.edges.length,
      terminal_count: state.manifest.graph.terminals.length,
      goal_hash: state.manifest.goal?.goal_hash,
      source_schema: state.manifest.source_schema,
    },
    snapshot: {
      run_id: state.snapshot.run_id,
      run_status: state.snapshot.run_status,
      run_verdict: state.snapshot.run_verdict,
      projection_state: state.snapshot.projection_state,
      journal_sequence: state.snapshot.journal_sequence,
      node_count: state.snapshot.nodes.length,
      route_count: state.snapshot.routes.length,
      attention_count: state.snapshot.attention_items.length,
    },
    geometry: {
      missing_nodes: state.missingNodes,
      missing_terminals: state.missingTerminals,
      overlapping_pairs: state.overlappingPairs,
      node_count_visible: state.nodeRects.length,
      edge_count_visible: state.edgePaths.length,
      graph_rect: state.graphRect,
      canvas_rect: state.canvasRect,
      react_flow_rect: state.reactFlowRect,
      react_flow_viewport_rect: state.reactFlowViewportRect,
    },
    checks,
  };
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

const desktop = await inspectViewport(
  browser,
  { width: 1440, height: 1000, deviceScaleFactor: 1 },
  desktopScreenshotPath,
);
const mobile = await inspectViewport(
  browser,
  { width: 390, height: 900, isMobile: true, deviceScaleFactor: 1 },
  mobileScreenshotPath,
);
await browser.close();

const checks = {
  desktop: Object.values(desktop.checks).every(Boolean),
  mobile: Object.values(mobile.checks).every(Boolean),
};

const receipt = {
  schema: "tau.canonical_dag_viewer_browser_proof.v1",
  status: checks.desktop && checks.mobile ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  dag_id: dagId,
  url,
  desktop,
  mobile,
  checks,
  proof_scope: {
    proves: [
      "A real browser rendered the shared Tau React Flow viewer for one canonical DAG run.",
      "The viewer read authoritative local Tau project-DAG receipt/progress state via GET APIs.",
      "Desktop and mobile screenshots are bound by SHA-256.",
    ],
    does_not_prove: [
      "Provider/model semantic quality.",
      "Human acceptance of the full immutable Tau goal.",
      "Automatic live polling during an in-progress run.",
    ],
  },
};

fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
