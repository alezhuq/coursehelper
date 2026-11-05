import { expect, request, test } from "@playwright/test";

// Book upload end-to-end: a professor uploads a PDF to their own
// course, watches its status poll queued -> ready (the stub worker
// finishes quickly), then deletes it and confirms the row disappears.
//
// Uses a dedicated, uniquely-named test-auth user (not the shared
// STUDENT_STORAGE_STATE_PATH fixture) so this test's course/book never
// races or interferes with other specs running fullyParallel.
test.describe("book upload, status polling, and delete", () => {
  test("professor uploads a PDF, sees it reach Ready, then deletes it", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    // Base-36, not a raw millisecond timestamp: `computing_id` is a
    // varchar(32) column (see role_request.spec.ts for the same guard).
    const uniqueSuffix = Date.now().toString(36);

    const professorApi = await request.newContext({ baseURL });
    const professorLogin = await professorApi.post("/api/test-auth/login", {
      data: {
        computing_id: `book-upload-e2e-${uniqueSuffix}`,
        role: "professor",
      },
    });
    expect(professorLogin.ok()).toBe(true);
    const professorState = await professorApi.storageState();
    await professorApi.dispose();

    const professorContext = await browser.newContext({
      storageState: professorState,
    });
    const professorPage = await professorContext.newPage();

    // Create the course through the UI (reuses CourseCreatePage's already
    // CSRF-header-attaching apiFetch instead of hand-rolling a raw API
    // POST that would need to replicate that header itself).
    await professorPage.goto("/courses/new");
    const courseCode = `BUE ${uniqueSuffix}`;
    await professorPage.getByLabel("Course code").fill(courseCode);
    await professorPage
      .getByLabel("Title")
      .fill("Book Upload E2E Test Course");
    await professorPage.getByLabel("Term").fill("Fall 2026");
    await professorPage.getByRole("button", { name: "Create course" }).click();
    await professorPage.waitForURL(/\/courses\/[^/]+$/);

    const fileInput = professorPage.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: "sample.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 test"),
    });

    const row = professorPage.locator("tr").filter({ hasText: "sample.pdf" });
    await expect(row).toBeVisible();
    await expect(row.getByText("Ready", { exact: true })).toBeVisible({
      timeout: 15000,
    });

    await row.getByRole("button", { name: "Delete book" }).click();
    const dialog = professorPage.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Delete book" }).click();
    await expect(row).toHaveCount(0);

    await professorContext.close();
  });
});
