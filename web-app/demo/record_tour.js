/*
 * Record a deterministic guided tour of the dashboard to video (Node Playwright).
 *
 * Why Node and not Python? Playwright's Python *sync driver* segfaults on some Linux boxes
 * (a driver-transport crash unrelated to Chromium). The bundled Node driver + Chromium work
 * reliably, so `make record` runs this with the same browser you already installed via
 * `playwright install chromium` — no extra downloads.
 *
 * Reads output/storyboard.json (from beats.py via export_storyboard.py) for the actions/config,
 * and output/timings.json (from prepare_audio.py) to pace each beat to the real narration length
 * (falls back to the `seconds` in beats.py when no audio is prepared yet).
 *
 * Env: PW_DRIVER = path to the bundled Playwright JS package (Makefile passes it).
 *      HEADED=1   = show the browser window instead of headless.
 */
"use strict";
const fs = require("fs");
const path = require("path");

const { chromium } = require(process.env.PW_DRIVER || "playwright");
const OUT = path.join(__dirname, "output");

function readJSON(p) { return JSON.parse(fs.readFileSync(p, "utf-8")); }

async function doAction(page, action, store, chatQ) {
  if (action === "intro") return;
  if (action === "map") {
    try { await page.click(".leaflet-control-zoom-in", { timeout: 2000 }); } catch (_) {}
    return;
  }
  if (action === "forecast") {
    await page.selectOption("#store-select", store);
    await page.waitForSelector("#forecast .fc-row", { timeout: 15000 });
    return;
  }
  if (action === "shap") {
    await page.click("#forecast .fc-row:nth-child(1)");   // highlight a day
    await page.waitForTimeout(600);
    await page.click('button[data-action="shap"]');       // chart + AI explanation
    return;
  }
  if (action === "chat_weather") {
    await page.fill("#chat-input", chatQ);
    await page.click('#chat-form button[type="submit"]');
    return;
  }
}

(async () => {
  const sb = readJSON(path.join(OUT, "storyboard.json"));
  const timings = {};
  const tf = path.join(OUT, "timings.json");
  if (fs.existsSync(tf)) for (const b of readJSON(tf).beats) timings[b.id] = b.dur;

  const browser = await chromium.launch({
    headless: process.env.HEADED !== "1",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: sb.viewport,
    recordVideo: { dir: OUT, size: sb.viewport },
  });
  const videoT0 = Date.now();        // video capture starts about here
  const page = await context.newPage();
  await page.goto(sb.url, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);   // let map tiles settle

  let store = sb.store;
  if (!store) {
    const stores = await (await page.request.get(sb.url + "/api/stores")).json();
    store = stores.reduce((a, c) => (c.mean_calibrated > a.mean_calibrated ? c : a)).store_name;
  }
  console.log("Featuring store:", store);

  // Everything above (load + settle) is pre-roll; record it so the mux can trim it and
  // align beat 1 (and the narration) with the first real frame.
  const preroll = (Date.now() - videoT0) / 1000;
  fs.writeFileSync(path.join(OUT, "meta.json"), JSON.stringify({ preroll }));

  for (const beat of sb.beats) {
    const dur = timings[beat.id] || beat.seconds;
    console.log(`  beat ${beat.id} (${beat.action}) — ${dur}s`);
    const start = Date.now();
    await doAction(page, beat.action, store, sb.chat_question);
    const remaining = dur * 1000 - (Date.now() - start);
    if (remaining > 0) await page.waitForTimeout(remaining);
  }

  const video = page.video();
  await context.close();   // finalizes the .webm
  await browser.close();

  const src = await video.path();
  const target = path.join(OUT, "tour.webm");
  if (fs.existsSync(target)) fs.unlinkSync(target);
  fs.renameSync(src, target);
  console.log("\nWrote " + target);
})().catch((e) => { console.error(e); process.exit(1); });
