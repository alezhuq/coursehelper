import { expect, request, test } from "@playwright/test";

import { makeMultiPagePdf } from "./fixtures/makeTestPdf";

// Search end-to-end: a real (owner-enrolled) professor submits a
// query on the course search page, clicks the first result, and lands on
// the exact cited page of the viewer.
//
// Real ingestion takes 20-45 minutes (measured in a load test) and is
// infeasible inside an E2E run, so this seeds a `ready` book plus a couple
// of real bge-m3-embedded chunks directly via the test-only
// `/test-auth/seed-book` + `/test-auth/seed-chunks` routes, rather than uploading through the real queue/worker path.
//
// Uses a dedicated, uniquely-named test-auth user (not the shared
// STUDENT_STORAGE_STATE_PATH fixture) so this test's course/book/chunks
// never race or interfere with other specs running fullyParallel.
test.describe("hybrid search -> viewer citation", () => {
  test("submits a query, clicks the first result, and lands on the exact cited page", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    const uniqueSuffix = Date.now().toString(36);
    // A deliberately distinctive term (not a real English word) so the
    // keyword arm's match is unambiguous regardless of any other seeded
    // data in the shared dev database.
    const uniqueTerm = `zzyzxquokka${uniqueSuffix}`;

    const api = await request.newContext({ baseURL });
    const login = await api.post("/api/test-auth/login", {
      data: {
        computing_id: `search-e2e-${uniqueSuffix}`,
        role: "professor",
      },
    });
    expect(login.ok()).toBe(true);

    const courseResponse = await api.post("/api/courses", {
      headers: { "X-Requested-With": "fetch" },
      data: {
        code: `SRCH ${uniqueSuffix}`,
        title: "Search E2E Test Course",
        term: "Fall 2026",
      },
    });
    expect(courseResponse.ok()).toBe(true);
    const course = (await courseResponse.json()) as { id: string };

    const seedResponse = await api.post("/api/test-auth/seed-book", {
      multipart: {
        course_id: course.id,
        file: {
          name: "search-e2e.pdf",
          mimeType: "application/pdf",
          buffer: makeMultiPagePdf(6),
        },
      },
    });
    expect(seedResponse.ok()).toBe(true);
    const book = (await seedResponse.json()) as { id: string };

    const chunksResponse = await api.post("/api/test-auth/seed-chunks", {
      data: {
        book_id: book.id,
        chunks: [
          {
            text: `The chlorophyll pigment absorbs sunlight during photosynthesis in a distinctive ecosystem researchers call the ${uniqueTerm} forest.`,
            page_start: 4,
            page_end: 4,
            section_path: ["Chapter 2", "Photosynthesis"],
          },
        ],
      },
    });
    expect(chunksResponse.ok()).toBe(true);

    const storageState = await api.storageState();
    await api.dispose();

    const context = await browser.newContext({ storageState });
    const page = await context.newPage();

    await page.goto(`/courses/${course.id}/search`);
    await page
      .getByPlaceholder("Search this course's materials")
      .fill(uniqueTerm);
    await page.getByRole("button", { name: "Search" }).click();

    const resultCard = page.getByText(uniqueTerm, { exact: false });
    await expect(resultCard).toBeVisible();

    await page.getByText("search-e2e.pdf").click();

    await page.waitForURL(
      new RegExp(`/courses/${course.id}/books/${book.id}\\?page=4$`),
    );
    await expect(page.locator("canvas")).toBeVisible();

    await context.close();
  });
});
