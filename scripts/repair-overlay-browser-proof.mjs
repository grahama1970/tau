import fs from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
let puppeteer;
try {
  puppeteer = process.env.NODE_PATH ? require(`${process.env.NODE_PATH}/puppeteer`) : require("puppeteer");
} catch {
  puppeteer = process.env.NODE_PATH ? require(`${process.env.NODE_PATH}/puppeteer-core`) : require("puppeteer-core");
}

const [url, screenshotPath, outputPath] = process.argv.slice(2);
if (!url || !screenshotPath || !outputPath) throw new Error("repair overlay browser proof arguments missing");

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 1 });
const methods = [];
page.on("request", (request) => methods.push(request.method()));
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForSelector('[data-qid="dag:overview:repair"]', { timeout: 20000 });

const observed = await page.evaluate(() => {
  const repair = document.querySelector('[data-qid="dag:overview:repair"]');
  const text = repair?.textContent || "";
  const links = Array.from(document.querySelectorAll('a')).map((link) => link.href);
  const messageUrlInText = /https:\/\/discord\.com\/channels\//.test(text);
  const messageUrlInLink = links.some((href) => href.startsWith("https://discord.com/channels/"));
  return {
    repair_overlay_visible: Boolean(repair),
    repair_overlay_text: text,
    blocking_category_visible: /blocking repair category|Repair categories closed/.test(text),
    ops_discord_visible: text.includes("ops-discord"),
    ops_discord_sent_or_dry_run_visible: /ops-discord · (SENT|DRY_RUN|DEDUPED)/.test(text),
    human_question_visible: text.includes("human question"),
    message_url_visible: messageUrlInText || messageUrlInLink,
    no_mutation_controls: Array.from(repair?.querySelectorAll("button") || []).length === 0,
  };
});
await page.screenshot({ path: screenshotPath, fullPage: true });
await browser.close();
const screenshotSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(screenshotPath)).digest("hex")}`;
const checks = {
  repair_overlay_visible: observed.repair_overlay_visible,
  blocking_category_visible: observed.blocking_category_visible,
  ops_discord_visible: observed.ops_discord_visible,
  ops_discord_sent_or_dry_run_visible: observed.ops_discord_sent_or_dry_run_visible,
  human_question_visible: observed.human_question_visible,
  message_url_visible: observed.message_url_visible,
  read_only_requests: methods.length > 0 && methods.every((method) => method === "GET"),
  no_mutation_controls: observed.no_mutation_controls,
};
const receipt = {
  schema: "tau.repair_overlay_browser_proof.v1",
  status: Object.values(checks).every(Boolean) ? "PASS" : "BLOCKED",
  mocked: false,
  live: true,
  provider_live: false,
  url,
  screenshot: screenshotPath,
  screenshot_sha256: screenshotSha256,
  repair_overlay_text: observed.repair_overlay_text,
  request_methods: [...new Set(methods)].sort(),
  checks,
};
fs.writeFileSync(outputPath, JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify(receipt, null, 2));
process.exit(receipt.status === "PASS" ? 0 : 1);
