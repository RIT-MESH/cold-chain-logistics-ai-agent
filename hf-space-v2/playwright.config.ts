import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./playwright",
  use: { baseURL: "http://localhost:4173" },
  webServer: { command: "npm run preview", port: 4173, reuseExistingServer: true },
  timeout: 90_000,
  retries: 2,
});
