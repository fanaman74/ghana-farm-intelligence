import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "../../../docs/screenshots");
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: "en-GB",
  timezoneId: "Africa/Accra",
});
const page = await context.newPage();
const errors = [];
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
page.on("requestfailed", (request) => {
  if (!request.url().includes("favicon")) errors.push(`requestfailed: ${request.url()} ${request.failure()?.errorText}`);
});

await page.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
await page.waitForSelector(".map-canvas canvas", { timeout: 60000 });
await page.waitForTimeout(3000);
await page.screenshot({ path: path.join(OUT, "01-onboarding-dark.png") });

const sampleButton = page.getByRole("button", { name: /Try the sample farm/i });
if (await sampleButton.isVisible().catch(() => false)) {
  await sampleButton.click();
  await page.waitForSelector(".farm-summary__area", { timeout: 60000 });
} else {
  const farmsResponse = await page.request.get("http://127.0.0.1:8000/api/farms");
  const farmsPayload = await farmsResponse.json();
  if (!farmsPayload.farms?.length) {
    await page.request.post("http://127.0.0.1:8000/api/farms/sample");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".map-canvas canvas", { timeout: 60000 });
  }
  await page.waitForSelector(".farm-summary__area", { timeout: 60000 });
}
const started = Date.now();
await page.waitForSelector(".rec, .empty--warn", { timeout: 300000 });
await page.waitForTimeout(2500);
console.log("analysis UI ready in", Math.round((Date.now() - started) / 1000), "s");
await page.screenshot({ path: path.join(OUT, "02-overview-desktop.png") });

const tabs = [
  ["Soil", "03-soil-desktop"],
  ["Weather", "04-weather-desktop"],
  ["Crops", "05-crops-desktop"],
  ["Health", "06-health-desktop"],
  ["Water", "07-water-desktop"],
  ["Calendar", "08-calendar-desktop"],
];
for (const [label, file] of tabs) {
  const item = page.locator(".tabs__item", { hasText: label }).first();
  await item.click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, `${file}.png`) });
}

await page.locator(".tabs__item", { hasText: "Overview" }).first().click();
await page.waitForTimeout(600);
await page.getByLabel("Switch daylight mode").click();
await page.waitForTimeout(1600);
await page.screenshot({ path: path.join(OUT, "09-overview-daylight.png") });
await page.getByLabel("Switch daylight mode").click();
await page.waitForTimeout(800);

await page.keyboard.press("/");
await page.waitForSelector(".search-modal", { timeout: 10000 });
await page.locator(".search-modal input").fill("Kumasi");
await page.waitForTimeout(1800);
await page.screenshot({ path: path.join(OUT, "10-search.png") });
await page.keyboard.press("Escape");

// Drawing flow on the map.
await page.getByRole("button", { name: /Draw farm/i }).click();
await page.waitForSelector(".draw-bar", { timeout: 10000 });
const canvas = page.locator(".map-canvas canvas").first();
const box = await canvas.boundingBox();
if (box) {
  const points = [
    [0.46, 0.42],
    [0.56, 0.4],
    [0.58, 0.5],
    [0.47, 0.52],
  ];
  for (const [px, py] of points) {
    await page.mouse.click(box.x + box.width * px, box.y + box.height * py);
    await page.waitForTimeout(260);
  }
}
await page.waitForTimeout(600);
await page.screenshot({ path: path.join(OUT, "11-drawing.png") });
await page.locator(".draw-bar .button--primary", { hasText: "Finish" }).click();
await page.waitForSelector(".modal", { timeout: 30000 });
await page.waitForTimeout(1200);
await page.screenshot({ path: path.join(OUT, "12-save-farm.png") });
await page.locator(".modal").getByRole("button", { name: "Cancel" }).click();
await page.waitForTimeout(600);

const metrics = await page.evaluate(() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const resources = performance.getEntriesByType("resource");
  return {
    domContentLoadedMs: Math.round(nav?.domContentLoadedEventEnd ?? 0),
    loadMs: Math.round(nav?.loadEventEnd ?? 0),
    transferKB: Math.round(resources.reduce((total, entry) => total + (entry.transferSize || 0), 0) / 1024),
    resources: resources.length,
  };
});

// Mobile viewport.
const mobileContext = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  locale: "en-GB",
  timezoneId: "Africa/Accra",
});
const mobile = await mobileContext.newPage();
await mobile.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
await mobile.waitForSelector(".map-canvas canvas", { timeout: 60000 });
const farmsResponse = await mobile.request.get("http://127.0.0.1:8000/api/farms");
const farmsPayload = await farmsResponse.json();
if (farmsPayload.farms?.length) {
  await mobile.selectOption(".farm-select", farmsPayload.farms[0].id);
  await mobile.waitForSelector(".rec, .empty--warn", { timeout: 120000 });
  await mobile.waitForTimeout(2000);
}
await mobile.screenshot({ path: path.join(OUT, "13-mobile-overview.png") });
await mobile.getByLabel("Toggle layers panel").click();
await mobile.waitForTimeout(800);
await mobile.screenshot({ path: path.join(OUT, "14-mobile-layers.png") });
await mobile.getByLabel("Close layers panel").click();
await mobile.waitForTimeout(500);
await mobile.getByLabel("Toggle farm panel").click();
await mobile.waitForTimeout(600);
const mobileTab = mobile.locator(".tabs__item", { hasText: "Soil" }).first();
if (await mobileTab.isVisible().catch(() => false)) {
  await mobileTab.click();
  await mobile.waitForTimeout(1200);
  await mobile.screenshot({ path: path.join(OUT, "15-mobile-soil.png") });
}

console.log("metrics:", JSON.stringify(metrics));
console.log("errors:", errors.length ? JSON.stringify(errors.slice(0, 12), null, 1) : "none");
await browser.close();
