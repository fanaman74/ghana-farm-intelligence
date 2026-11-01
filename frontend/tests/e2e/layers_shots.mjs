import { chromium } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(process.cwd(), "../docs/screenshots");
const browser = await chromium.launch();

async function openApp(contextOptions) {
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("http://127.0.0.1:8000/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".map-canvas canvas");
  await page.waitForSelector(".farm-summary__area", { timeout: 60000 });
  await page.waitForSelector(".rec", { timeout: 180000 });
  await page.waitForTimeout(1200);
  return { context, page, errors };
}

async function rowFor(page, name) {
  const row = page.locator(".layer", { hasText: name }).first();
  await row.scrollIntoViewIfNeeded();
  return row;
}

async function setChecked(page, name, wanted) {
  const row = await rowFor(page, name);
  const checkbox = row.locator('input[type="checkbox"]');
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if ((await checkbox.isChecked()) === wanted) return row;
    await row.locator(".switch").click();
    await page.waitForTimeout(900);
  }
  throw new Error(`Could not set ${name} to ${wanted}`);
}

async function enableOnly(page, name) {
  await setChecked(page, "NDVI", false);
  await setChecked(page, "Sentinel-2 true colour", false);
  await setChecked(page, "NDMI", false);
  await setChecked(page, "Land cover (2021 baseline)", false);
  await setChecked(page, "Slope (30 m)", false);
  await setChecked(page, "Soil pH (250 m)", false);
  const row = await setChecked(page, name, true);
  await page.waitForFunction(
    (text) => {
      const rows = Array.from(document.querySelectorAll(".layer"));
      const target = rows.find((element) => element.textContent?.includes(text));
      return Boolean(target && target.querySelector(".layer__meta")?.textContent?.includes("grid"));
    },
    name,
    { timeout: 60000 },
  );
  await page.waitForTimeout(1200);
  await row.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(400);
  return row;
}

const desktop = { viewport: { width: 1440, height: 900 }, locale: "en-GB", timezoneId: "Africa/Accra" };
const shots = [
  ["Soil pH (250 m)", "16-layers-soil-ph.png"],
  ["Land cover (2021 baseline)", "17-layers-cropland.png"],
  ["Slope (30 m)", "18-layers-slope.png"],
];

for (const [name, file] of shots) {
  const { context, page, errors } = await openApp(desktop);
  await enableOnly(page, name);
  await page.screenshot({ path: path.join(OUT, file) });
  if (errors.length) console.log(file, "errors:", errors.slice(0, 3));
  await context.close();
}

// Mobile layers panel with a real rendered layer.
const mobile = {
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  locale: "en-GB",
  timezoneId: "Africa/Accra",
};
{
  const { context, page, errors } = await openApp(mobile);
  await page.getByLabel("Toggle layers panel").tap();
  await page.waitForSelector("[aria-label='Map layers']");
  await setChecked(page, "NDVI", false);
  await setChecked(page, "Land cover (2021 baseline)", true);
  await page.waitForFunction(
    () => document.querySelector(".layer__meta")?.textContent?.includes("grid"),
    undefined,
    { timeout: 60000 },
  );
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, "14-mobile-layers.png") });
  if (errors.length) console.log("mobile errors:", errors.slice(0, 3));
  await context.close();
}

console.log("layer screenshots written");
await browser.close();
