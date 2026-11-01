// Measure the Overview with a seeded fleet on a "mid phone" profile (CUT-192).
//
//   BASE=http://127.0.0.1:8816 TOKEN=<token> [RUNS=5] [BUDGET_MS=2000] \
//     PW_DIR=<a directory with playwright installed> node scripts/measure_dashboard.mjs
//
// Seed and serve first (agent_docs/scale-testing.md). The profile is Lighthouse's mobile default:
// 390x844, 4x CPU slowdown, 1.6 Mbps down, 150 ms round trip. A returning visit is timed (the
// token is remembered): reload to the first project card, and to all 100. The idle cost is the main
// thread's busy seconds per 10 s of polling, sprites and timers included. Exits 1 when the median
// first card is over BUDGET_MS. Needs Playwright and a Chromium; it is not part of `make ci`.
import { createRequire } from "node:module";

const { BASE, TOKEN, PW_DIR, RUNS = "5", BUDGET_MS = "2000" } = process.env;
if (!BASE || !TOKEN || !PW_DIR) {
  console.error("set BASE, TOKEN and PW_DIR");
  process.exit(2);
}
// Playwright lives in a scratch directory, not in this repo (agent_docs/exploratory-testing.md).
const { chromium } = createRequire(PW_DIR + "/")("playwright");
const median = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
const metric = async (cdp) =>
  Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));

const browser = await chromium.launch();
const runs = [];
for (let i = 0; i < Number(RUNS); i++) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable");
  await page.goto(BASE + "/", { waitUntil: "load" });
  await page.locator("input").last().fill(TOKEN);
  await page.getByRole("button", { name: /Connect/ }).click();
  await page.waitForSelector("article");

  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 150,
    downloadThroughput: (1.6 * 1024 * 1024) / 8,
    uploadThroughput: (750 * 1024) / 8,
  });
  const start = Date.now();
  await page.reload({ waitUntil: "commit" });
  await page.waitForSelector("article");
  const first = Date.now() - start;
  await page.waitForFunction(() => document.querySelectorAll("article").length >= 100, null, { timeout: 60000 });
  const all = Date.now() - start;

  const before = await metric(cdp);
  await page.waitForTimeout(12500);
  const after = await metric(cdp);
  runs.push({
    first,
    all,
    idleBusySecPer10s: ((after.TaskDuration - before.TaskDuration) / 12.5) * 10,
    domNodes: await page.evaluate(() => document.getElementsByTagName("*").length),
  });
  await ctx.close();
}
await browser.close();

for (const key of Object.keys(runs[0])) {
  console.log(key.padEnd(18), runs.map((r) => r[key].toFixed(1)).join("  "), "  median", median(runs.map((r) => r[key])).toFixed(1));
}
const first = median(runs.map((r) => r.first));
if (first > Number(BUDGET_MS)) {
  console.error(`first project card ${first} ms is over the ${BUDGET_MS} ms budget`);
  process.exit(1);
}
