import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";

const OUT = path.resolve(process.cwd(), "../docs/screenshots");
fs.mkdirSync(OUT, { recursive: true });

async function openApp(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".map-canvas canvas", { timeout: 60_000 });
  await page.waitForTimeout(1200);
}

async function ensureFarmSelected(page: Page) {
  const sample = page.getByRole("button", { name: /Try the sample farm/i });
  if (await sample.isVisible().catch(() => false)) {
    await sample.click();
  } else {
    // Re-runs against an existing database: pick the sample farm explicitly
    // instead of whichever farm happens to be selected. The suite used to fail
    // its second run because it inherited the previous run's farms.
    const select = page.locator(".farm-select");
    if (await select.isVisible().catch(() => false)) {
      const option = select.locator("option", { hasText: /Sample farm/i }).first();
      if (await option.count()) {
        const value = await option.getAttribute("value");
        if (value) await select.selectOption(value);
      }
    }
  }
  await page.waitForSelector(".farm-summary__area", { timeout: 60_000 });
}

async function waitForAnalysis(page: Page, timeout = 300_000) {
  await expect(page.locator(".rec, .empty--warn").first()).toBeVisible({ timeout });
  await page.waitForTimeout(1200);
}

/** The layers panel starts closed on narrow viewports, so open it first. */
async function openLayers(page: Page) {
  if (await page.locator(".layer").first().isVisible().catch(() => false)) return;
  await page.getByRole("button", { name: /Toggle layers panel/i }).click();
  await expect(page.locator(".layer").first()).toBeVisible({ timeout: 15_000 });
}

test.describe("Ghana Farm Intelligence", () => {
  test("onboarding explains the flow without pretending to have data", async ({ page }) => {
    await openApp(page);
    const onboarding = page.locator(".onboarding");
    if (await onboarding.isVisible().catch(() => false)) {
      await expect(onboarding).toContainText("Find your farm");
      await expect(onboarding).toContainText("Sources, dates, resolution and uncertainty");
      await expect(onboarding.getByRole("button", { name: /Try the sample farm/i })).toBeVisible();
    }
    await expect(page.locator(".topbar")).toContainText("Ghana Farm Intelligence");
  });

  test("sample farm runs a real polygon analysis with sources and tabs", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page);

    // Geodesic area is displayed in hectares and the farm is stored.
    const areaText = await page.locator(".farm-summary__area").innerText();
    expect(areaText).toMatch(/\d+\.\d+ ha/);

    // Every analysis section must have a status chip rather than a silent value.
    const coverage = page.locator(".coverage-strip");
    await expect(coverage).toContainText("Soil");
    await expect(coverage).toContainText("Weather");
    await expect(coverage).toContainText("Rainfall");

    // Recommendations carry evidence and a source.
    const firstRec = page.locator(".rec").first();
    await expect(firstRec).toContainText("Why");
    await expect(firstRec.locator(".evidence-chip").first()).toBeVisible();

    for (const [tab, expectation] of [
      ["Soil", /Soil profile/],
      ["Weather", /Weather and rainfall/],
      ["Crops", /Crops and planting/],
      ["Health", /Satellite vegetation/],
      ["Water", /Water balance/],
      ["Calendar", /Crop calendar/],
    ] as const) {
      await page.locator(".tabs__item", { hasText: tab }).first().click();
      await expect(page.locator(".panel--right")).toContainText(expectation, { timeout: 20_000 });
      await page.waitForTimeout(500);
    }

    // The timeline range control changes the displayed window.
    await page.locator(".tabs__item", { hasText: "Overview" }).first().click();
    if ((page.viewportSize()?.width ?? 1440) <= 980) {
      // On a phone the farm sheet covers the floating timeline, and the app hides
      // it while the sheet is open. Collapse the sheet first.
      await page.getByRole("button", { name: /Toggle farm panel/i }).click();
      await expect(page.locator(".timeline")).toBeVisible({ timeout: 10_000 });
    }
    await page.locator(".timeline .segmented__item", { hasText: "90 d" }).click();
    await expect(page.locator(".timeline .segmented__item", { hasText: "90 d" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Tiles are self-hosted and no public OSM tile server is contacted.
    const tileRequests = await page.evaluate(() =>
      performance
        .getEntriesByType("resource")
        .map((entry) => entry.name)
        .filter((name) => name.includes("tile.openstreetmap.org")),
    );
    expect(tileRequests).toEqual([]);

    expect(consoleErrors).toEqual([]);
  });

  test("draw a field, validate its area, save it and export GeoJSON", async ({ page, context }) => {
    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page);
    await page.locator(".tabs__item", { hasText: "Overview" }).first().click();

    await page.getByRole("button", { name: /Draw farm boundary/i }).click();
    await expect(page.locator(".draw-bar")).toBeVisible();

    const canvas = page.locator(".map-canvas canvas").first();
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    if (!box) return;
    for (const [px, py] of [
      [0.42, 0.4],
      [0.54, 0.38],
      [0.56, 0.5],
      [0.43, 0.52],
    ]) {
      await page.mouse.click(box.x + box.width * px, box.y + box.height * py);
      await page.waitForTimeout(200);
    }
    await expect(page.locator(".draw-bar__stat")).toContainText("vertices");
    await page.locator(".draw-bar .button--primary", { hasText: "Finish" }).click();

    const modal = page.locator(".modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Geodesic area");
    const areaText = await modal.locator(".save-summary").innerText();
    expect(areaText).toMatch(/\d+\.\d+ ha/);

    await modal.getByLabel("Farm name").fill("Playwright field");
    await modal.getByRole("button", { name: /Save and analyse/i }).click();
    await expect(modal).toBeHidden({ timeout: 30_000 });
    await expect(page.locator(".farm-select")).toHaveValue(/.+/);

    await waitForAnalysis(page);

    const farmId = await page.locator(".farm-select").inputValue();
    const response = await context.request.get(`/api/farms/${farmId}/export?format=geojson`);
    expect(response.ok()).toBeTruthy();
    const payload = await response.json();
    expect(payload.type).toBe("FeatureCollection");
    expect(payload.features[0].geometry.type).toMatch(/Polygon/);
    expect(payload.features[0].properties.area_ha).toBeGreaterThan(0);

    // The PDF report includes the analysis and sources.
    const pdf = await context.request.get(`/api/farms/${farmId}/export?format=pdf`);
    expect(pdf.status()).toBe(200);
    expect(pdf.headers()["content-type"]).toContain("application/pdf");
    const pdfBytes = await pdf.body();
    expect(pdfBytes.length).toBeGreaterThan(3000);
    expect(pdfBytes.subarray(0, 4).toString()).toBe("%PDF");
  });

  test("evidence layers render on demand for the selected boundary", async ({ page }) => {
    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page, 180_000);
    await openLayers(page);

    // Turn off the analysis imagery so the evidence layer is unambiguous.
    const ndviRow = page.locator(".layer", { hasText: "NDVI" }).first();
    if (await ndviRow.locator('input[type="checkbox"]').isChecked()) {
      await ndviRow.locator(".switch").click();
    }

    const soilRow = page.locator(".layer", { hasText: "Soil pH (250 m)" }).first();
    await soilRow.scrollIntoViewIfNeeded();
    const responsePromise = page.waitForResponse(
      (response) => response.url().includes("/layers/soil_ph") && response.status() === 200,
      { timeout: 60_000 },
    );
    await soilRow.locator(".switch").click();
    await responsePromise;

    // The panel must show a real legend and the true grid resolution, not "No data".
    await expect(soilRow.locator(".legend__item").first()).toBeVisible({ timeout: 20_000 });
    await expect(soilRow.locator(".layer__meta")).toContainText("250 m grid");
    await expect(soilRow).not.toContainText("No data");

    await page.locator(".map-area").screenshot({ path: path.join(OUT, "evidence-layer-soil-ph.png") });

    const cropland = page.locator(".layer", { hasText: "Land cover (2021 baseline)" }).first();
    await cropland.scrollIntoViewIfNeeded();
    const cropResponse = page.waitForResponse(
      (response) => response.url().includes("/layers/cropland") && response.status() === 200,
      { timeout: 60_000 },
    );
    await cropland.locator(".switch").click();
    await cropResponse;
    await expect(cropland.locator(".legend__item").first()).toBeVisible({ timeout: 20_000 });
    await expect(cropland.locator(".layer__meta")).toContainText("10 m grid");
  });

  test("satellite toggle switches the whole basemap to Sentinel-2 tiles", async ({ page }) => {
    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page, 180_000);

    const button = page.getByLabel("Toggle satellite view");
    const tilePromise = page.waitForResponse(
      (response) => response.url().includes("tiles.maps.eox.at") && response.status() === 200,
      { timeout: 45_000 },
    );
    await button.click();
    await tilePromise;

    await expect(page.locator(".map-satellite-note")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".map-satellite-note")).toContainText("Sentinel-2 cloudless 2025");
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await page.waitForTimeout(1500);

    await button.click();
    await expect(page.locator(".map-satellite-note")).toHaveCount(0);
    await expect(button).toHaveAttribute("aria-pressed", "false");
  });

  test("search a Ghana locality and fly the map to it", async ({ page }) => {
    await openApp(page);
    await page.keyboard.press("/");
    const search = page.locator(".search-modal");
    await expect(search).toBeVisible();
    await search.locator("input").fill("Tamale");
    await expect(search.locator(".search-result").first()).toBeVisible({ timeout: 15_000 });
    await expect(search.locator(".search-result").first()).toContainText(/Tamale/i);
    await search.locator(".search-result").first().click();
    await expect(page.locator(".map-notice")).toContainText(/Tamale/i);
  });

  test("offline reload keeps saved farms and labels the data as saved", async ({ page, context }) => {
    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page);

    // Give IndexedDB a moment to persist the analysis and let the service
    // worker take control before simulating a dropped connection.
    await page.waitForTimeout(1500);
    await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) {
        await new Promise<void>((resolve) => {
          navigator.serviceWorker.addEventListener("controllerchange", () => resolve(), { once: true });
          setTimeout(resolve, 5000);
        });
      }
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    await context.setOffline(true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(4000);

    await expect(page.locator(".offline-banner")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".offline-banner")).toContainText(/Offline/i);
    const body = await page.locator("body").innerText();
    expect(body).toMatch(/ha/);
    await context.setOffline(false);
  });

  test("mobile viewport keeps controls reachable and panels swipeable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openApp(page);
    await ensureFarmSelected(page);
    await waitForAnalysis(page, 120_000);

    await expect(page.locator(".panel--right")).toBeVisible();
    const tabs = page.locator(".tabs__item", { hasText: "Soil" }).first();
    await tabs.click();
    await expect(page.locator(".panel--right")).toContainText(/Soil profile/);
    await page.screenshot({ path: path.join(OUT, "mobile-overview.png") });

    await page.getByLabel("Toggle layers panel").click();
    await expect(page.locator(".panel--left")).toBeVisible();
    await page.getByLabel("Close layers panel").click();
    await expect(page.locator(".panel--left")).toBeHidden();

    // Touch targets are large enough for one-handed use.
    const drawButton = page.getByRole("button", { name: /Draw farm boundary/i });
    const bounds = await drawButton.boundingBox();
    expect(bounds?.height ?? 0).toBeGreaterThanOrEqual(36);
  });
});
