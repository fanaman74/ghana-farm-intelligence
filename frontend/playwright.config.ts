import { defineConfig, devices } from "@playwright/test";

/**
 * The app must be running before the suite starts. Either:
 *   docker compose up --build         (serves http://localhost:8000)
 *   or a local uvicorn + built frontend (see README)
 *
 * Override the target with GFI_BASE_URL.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 5 * 60 * 1000,
  expect: { timeout: 25_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.GFI_BASE_URL ?? "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    locale: "en-GB",
    timezoneId: "Africa/Accra",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "android",
      use: { ...devices["Pixel 5"] },
    },
  ],
});
