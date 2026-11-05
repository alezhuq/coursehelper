import { expect, request, test } from "@playwright/test";

// CHAT-03 deterministic refusal-path E2E: a real (owner-enrolled) professor
// opens the course chat for a freshly created course with zero ready
// chunks - the hybrid retrieval arms both return empty for a course with no
// chunks at all, so the empty-retrieval gate (app/chat/gate.py::
// is_empty_retrieval) short-circuits to the exact refusal string with NO
// LLM call, entirely independent of whichever model is configured. This
// proves the full browser -> POST-SSE -> condense(skipped, first turn)
// -> retrieve -> gate -> refusal -> persist path without any live LLM
// dependency (a populated streaming answer requires the llm profile and is
// verified manually, not automated here).
//
// Uses a dedicated, uniquely-named test-auth user (not the shared
// STUDENT_STORAGE_STATE_PATH fixture) so this test's course/conversation
// never race or interfere with other specs running fullyParallel.
test.describe("chat empty-retrieval refusal path", () => {
  test("submits a question against a chunk-less course and shows the exact refusal string with no citations or error styling", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    const uniqueSuffix = Date.now().toString(36);

    const api = await request.newContext({ baseURL });
    const login = await api.post("/api/test-auth/login", {
      data: {
        computing_id: `chat-e2e-${uniqueSuffix}`,
        role: "professor",
      },
    });
    expect(login.ok()).toBe(true);

    const courseResponse = await api.post("/api/courses", {
      headers: { "X-Requested-With": "fetch" },
      data: {
        code: `CHAT ${uniqueSuffix}`,
        title: "Chat E2E Test Course",
        term: "Fall 2026",
      },
    });
    expect(courseResponse.ok()).toBe(true);
    const course = (await courseResponse.json()) as { id: string };

    const storageState = await api.storageState();
    await api.dispose();

    const context = await browser.newContext({ storageState });
    const page = await context.newPage();

    await page.goto(`/courses/${course.id}/chat`);
    await page.getByText("Ask a question about this course's materials.").waitFor();

    const composer = page.getByPlaceholder(
      "Ask a question about this course's materials",
    );
    await composer.fill("What is the syllabus policy on late assignments?");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(
      page.getByText("The course materials don't cover this question."),
    ).toBeVisible();

    // No citation badges - this refusal has zero sources by construction.
    await expect(page.getByRole("button", { name: /Open citation/ })).toHaveCount(0);
    // No destructive/error styling anywhere in the thread - this is a
    // correct, calm answer, not a failure state.
    await expect(page.getByText("Could not get a response. Try again.")).toHaveCount(0);

    await context.close();
  });
});
