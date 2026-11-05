import { defineConfig, devices } from "@playwright/test";

// BASE_URL defaults to the nginx-published port so tests exercise the real
// Docker Compose stack, not the Vite dev server. e2e/ lives under web/ (not
// at the repo root) so global-setup.ts/auth.spec.ts can resolve
// "@playwright/test" via web/node_modules - there is no repo-root
// node_modules for a bare top-level e2e/ directory to resolve against.
export default defineConfig({
  testDir: "./e2e",
  // Runs once before the suite: mints a test-auth session cookie and saves
  // it as storageState so auth.spec.ts's
  // bypass-based test doesn't need to drive a real CAS/magic-link flow.
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8080",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
