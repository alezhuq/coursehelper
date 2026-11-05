import { fileURLToPath } from "node:url";
import path from "node:path";

/**
 * Absolute path to the saved student storageState, shared by
 * global-setup.ts (writer) and auth.spec.ts (reader). Deriving it from this
 * module's own file location (rather than a bare relative string like
 * "e2e/.auth/student.json") means it resolves identically no matter what
 * the process's current working directory is when `playwright test` runs -
 * Playwright's own relative-path resolution rules differ between
 * `globalSetup` scripts and `test.use({ storageState })`, so a shared
 * absolute constant avoids relying on either.
 */
export const STUDENT_STORAGE_STATE_PATH = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  ".auth",
  "student.json",
);
