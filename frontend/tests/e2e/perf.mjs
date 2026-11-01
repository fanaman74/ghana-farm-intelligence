import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.GFI_BASE_URL ?? "http://127.0.0.1:8000";
const OUT = path.resolve(process.cwd(), "../docs/performance.json");
const results = { measured_at: new Date().toISOString(), base_url: BASE };

const browser = await chromium.launch();

// ---------------------------------------------------------------- desktop load
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const started = Date.now();
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".map-canvas canvas");
  const canvasMs = Date.now() - started;
  await page.waitForTimeout(3000);
  const metrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const resources = performance.getEntriesByType("resource");
    const total = resources.reduce((sum, entry) => sum + (entry.transferSize || 0), 0);
    return {
      domContentLoadedMs: Math.round(nav?.domContentLoadedEventEnd ?? 0),
      loadMs: Math.round(nav?.loadEventEnd ?? 0),
      resourceCount: resources.length,
      transferKB: Math.round(total / 1024),
      jsKB: Math.round(
        resources.filter((entry) => entry.name.endsWith(".js")).reduce((sum, entry) => sum + (entry.transferSize || 0), 0) / 1024,
      ),
      cssKB: Math.round(
        resources.filter((entry) => entry.name.endsWith(".css")).reduce((sum, entry) => sum + (entry.transferSize || 0), 0) / 1024,
      ),
      fontKB: Math.round(
        resources.filter((entry) => entry.name.endsWith(".woff2")).reduce((sum, entry) => sum + (entry.transferSize || 0), 0) / 1024,
      ),
    };
  });
  results.desktop = { mapCanvasMs: canvasMs, ...metrics };
  await context.close();
}

// ------------------------------------------------------------- mobile load/fps
{
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  const started = Date.now();
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".map-canvas canvas");
  const canvasMs = Date.now() - started;
  await page.waitForTimeout(2500);

  const canvas = page.locator(".map-canvas canvas").first();
  const box = await canvas.boundingBox();
  await page.evaluate(() => {
    window.__frames = [];
    window.__sampling = true;
    let last = performance.now();
    const tick = (now) => {
      window.__frames.push(now - last);
      last = now;
      if (window.__sampling) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  if (box) {
    const centreX = box.x + box.width / 2;
    const centreY = box.y + box.height * 0.35;
    for (let step = 0; step < 12; step += 1) {
      const offset = Math.sin(step) * 70;
      await page.mouse.move(centreX + offset, centreY);
      await page.mouse.down();
      await page.mouse.move(centreX - offset, centreY + Math.cos(step) * 40, { steps: 12 });
      await page.mouse.up();
    }
  }
  const frame = await page.evaluate(() => {
    window.__sampling = false;
    const usable = window.__frames.slice(2).filter((value) => value > 0 && value < 500);
    usable.sort((a, b) => a - b);
    const median = usable[Math.floor(usable.length / 2)] ?? 0;
    const p95 = usable[Math.floor(usable.length * 0.95)] ?? 0;
    return {
      frames: usable.length,
      medianFrameMs: Math.round(median * 10) / 10,
      p95FrameMs: Math.round(p95 * 10) / 10,
      approxFps: median ? Math.round((1000 / median) * 10) / 10 : null,
    };
  });
  results.mobile = { mapCanvasMs: canvasMs, ...frame };
  await context.close();
}

// -------------------------------------------------------------- analysis timing
{
  const context = await browser.newContext();
  const request = context.request;
  const polygon = {
    type: "Polygon",
    coordinates: [[
      [-1.4702, 6.6851],
      [-1.4652, 6.6848],
      [-1.4639, 6.6877],
      [-1.4664, 6.6901],
      [-1.4700, 6.6894],
      [-1.4702, 6.6851],
    ]],
  };
  const create = await request.post(`${BASE}/api/farms`, {
    data: {
      name: `Performance check ${new Date().toISOString().slice(11, 19)}`,
      geometry: polygon,
      crop_id: "maize",
      irrigation: "rainfed",
      source: "drawn",
    },
  });
  const farm = (await create.json()).farm;
  const startCold = Date.now();
  const coldResponse = await request.post(`${BASE}/api/farms/${farm.id}/analysis`, {
    data: { crop_id: "maize", force: true },
  });
  let job = await coldResponse.json();
  let attempts = 0;
  while (job.job_id && attempts < 300) {
    const poll = await request.get(`${BASE}/api/jobs/${job.job_id}`);
    const payload = await poll.json();
    if (payload.status === "complete" || payload.status === "failed") {
      job = payload;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 700));
    attempts += 1;
  }
  const coldMs = Date.now() - startCold;
  const analysis = job.result ?? null;
  const sectionMs = {};
  if (analysis?.sections) {
    for (const [key, section] of Object.entries(analysis.sections)) {
      sectionMs[key] = {
        status: section.status,
        symbols: JSON.stringify(section.data ?? {}).length,
      };
    }
  }
  const startWarm = Date.now();
  const warmResponse = await request.post(`${BASE}/api/farms/${farm.id}/analysis`, {
    data: { crop_id: "maize", force: false },
  });
  const warm = await warmResponse.json();
  const warmMs = Date.now() - startWarm;
  const latest = await request.get(`${BASE}/api/farms/${farm.id}/analysis/latest`);
  results.analysis = {
    cold_wall_ms: coldMs,
    server_elapsed_ms: analysis?.elapsed_ms ?? null,
    warm_cached_ms: warmMs,
    warm_cached: warm?.cached ?? null,
    section_status: Object.fromEntries(Object.entries(sectionMs).map(([key, value]) => [key, value.status])),
    payload_bytes: analysis ? JSON.stringify(analysis).length : null,
    farm_area_ha: farm?.area_ha ?? null,
  };
  await context.close();
}

// --------------------------------------------------------------- cache summary
{
  const context = await browser.newContext();
  const response = await context.request.get(`${BASE}/api/health`);
  results.health = await response.json();
  await context.close();
}

await browser.close();
fs.writeFileSync(OUT, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
