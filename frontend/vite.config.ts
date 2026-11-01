import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/**
 * The frontend is served by the FastAPI backend in production (`/api` on the same
 * origin). In development, Vite proxies `/api` to the local backend.
 *
 * The service worker precaches the app shell and caches the self-hosted basemap
 * and catalogue responses. It never tries to prefetch OSM tiles from a public
 * tile server, and it does not cache POST requests or analysis jobs.
 */
export default defineConfig({
  define: {
    __BUILD_ID__: JSON.stringify(new Date().toISOString()),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "icons/icon-192.png", "icons/icon-512.png"],
      manifest: {
        name: "Ghana Farm Intelligence",
        short_name: "Farm Intel",
        description:
          "Polygon-based soil, rainfall, satellite and crop guidance for farms in Ghana, with sources and uncertainty.",
        theme_color: "#0f1512",
        background_color: "#0f1512",
        display: "standalone",
        orientation: "portrait-primary",
        start_url: "/",
        scope: "/",
        categories: ["agriculture", "productivity", "utilities"],
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/basemap/"),
            handler: "CacheFirst",
            options: {
              cacheName: "gfi-basemap-v1",
              expiration: { maxEntries: 12, maxAgeSeconds: 7 * 24 * 3600 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/geo/"),
            handler: "CacheFirst",
            options: {
              cacheName: "gfi-geo-v1",
              expiration: { maxEntries: 12, maxAgeSeconds: 30 * 24 * 3600 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/catalog/"),
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "gfi-catalog-v1",
              expiration: { maxEntries: 30, maxAgeSeconds: 7 * 24 * 3600 },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/chips/"),
            handler: "CacheFirst",
            options: {
              cacheName: "gfi-chips-v1",
              expiration: { maxEntries: 40, maxAgeSeconds: 14 * 24 * 3600 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.GFI_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2020",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
});
