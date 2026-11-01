import { chromium } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(process.cwd(), "../docs/screenshots");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: "en-GB", timezoneId: "Africa/Accra" });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});

await page.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
await page.waitForSelector(".farm-summary__area", { timeout: 60_000 });
await page.waitForSelector(".rec", { timeout: 180_000 });
await page.waitForTimeout(1200);

// Turn off the analysis imagery so the basemap itself is visible.
for (const name of ["NDVI", "Sentinel-2 true colour", "NDMI"]) {
  const row = page.locator(".layer", { hasText: name }).first();
  const checkbox = row.locator('input[type="checkbox"]');
  if (await checkbox.isChecked()) {
    await row.locator(".switch").click();
    await page.waitForTimeout(500);
  }
}

const button = page.getByLabel("Toggle satellite view");
const tilePromise = page.waitForResponse(
  (response) => response.url().includes("tiles.maps.eox.at") && response.status() === 200,
  { timeout: 45_000 },
);
await button.click();
const tile = await tilePromise;
console.log("tile:", tile.url().split("/").slice(-5).join("/"));
await page.waitForSelector(".map-satellite-note", { timeout: 30_000 });
await page.waitForTimeout(2500);
console.log("note:", (await page.locator(".map-satellite-note").innerText()).replace(/\n/g, " "));
await page.screenshot({ path: path.join(OUT, "19-satellite-view.png") });

await button.click();
await page.waitForTimeout(800);
console.log("after off: note count", await page.locator(".map-satellite-note").count(), "pressed", await button.getAttribute("aria-pressed"));
console.log("errors:", errors.length ? errors.slice(0, 4) : "none");
await browser.close();
