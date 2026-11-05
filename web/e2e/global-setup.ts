import { request } from "@playwright/test";

import { STUDENT_STORAGE_STATE_PATH } from "./storage-state-path";

/**
 * Playwright E2E login bypass.
 *
 * Mints a session cookie for a test student directly via the test-only
 * `/api/test-auth/login` route (only registered by the API when
 * `ENABLE_TEST_AUTH=true`) instead of driving a real CAS/magic-link flow in
 * every test, then saves the resulting cookie as Playwright storageState
 * for reuse across the whole suite. The output path is an absolute path
 * shared with auth.spec.ts (see storage-state-path.ts) rather than a bare
 * relative string, so it resolves identically regardless of the process's
 * current working directory when `playwright test` is invoked.
 */
export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
  const context = await request.newContext({ baseURL });

  await context.post("/api/test-auth/login", {
    data: { computing_id: "test-student-1", role: "student" },
  });

  await context.storageState({ path: STUDENT_STORAGE_STATE_PATH });
  await context.dispose();
}
