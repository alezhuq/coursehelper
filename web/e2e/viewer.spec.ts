import { expect, request, test } from "@playwright/test";

import { makeMultiPagePdf } from "./fixtures/makeTestPdf";

// Viewer end-to-end: a real (owner-enrolled) professor deep-links
// straight into the PDF viewer at a specific page and drives the toolbar.
//
// Real ingestion takes 20-45 minutes (measured in a load test) and is
// infeasible inside an E2E run, so this seeds a `ready` book directly via
// the test-only `/test-auth/seed-book` route instead
// of uploading through the real queue/worker path - the file itself is a
// small, real, hand-rolled multi-page PDF (fixtures/makeTestPdf.ts) so
// react-pdf has real bytes to parse and render.
//
// Uses a dedicated, uniquely-named test-auth user (not the shared
// STUDENT_STORAGE_STATE_PATH fixture) so this test's course/book never
// races or interferes with other specs running fullyParallel.
test.describe("PDF viewer deep-link and toolbar", () => {
  test("deep-links to ?page=N, advances via next, and zooms in", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    const uniqueSuffix = Date.now().toString(36);

    const api = await request.newContext({ baseURL });
    const login = await api.post("/api/test-auth/login", {
      data: {
        computing_id: `viewer-e2e-${uniqueSuffix}`,
        role: "professor",
      },
    });
    expect(login.ok()).toBe(true);

    const courseResponse = await api.post("/api/courses", {
      headers: { "X-Requested-With": "fetch" },
      data: {
        code: `VWR ${uniqueSuffix}`,
        title: "Viewer E2E Test Course",
        term: "Fall 2026",
      },
    });
    expect(courseResponse.ok()).toBe(true);
    const course = (await courseResponse.json()) as { id: string };

    const pdfBuffer = makeMultiPagePdf(5);
    const seedResponse = await api.post("/api/test-auth/seed-book", {
      multipart: {
        course_id: course.id,
        file: {
          name: "viewer-e2e.pdf",
          mimeType: "application/pdf",
          buffer: pdfBuffer,
        },
      },
    });
    expect(seedResponse.ok()).toBe(true);
    const book = (await seedResponse.json()) as { id: string };

    const storageState = await api.storageState();
    await api.dispose();

    const context = await browser.newContext({ storageState });
    const page = await context.newPage();

    await page.goto(`/courses/${course.id}/books/${book.id}?page=2`);

    // A rendered PDF page paints a <canvas> inside react-pdf's Page wrapper.
    await expect(page.locator("canvas")).toBeVisible();
    await expect(page.getByRole("spinbutton")).toHaveValue("2");
    await expect(page.getByText("of 5")).toBeVisible();

    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page.getByRole("spinbutton")).toHaveValue("3");

    await expect(page.getByText("100%")).toBeVisible();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(page.getByText("125%")).toBeVisible();

    await context.close();
  });
});
