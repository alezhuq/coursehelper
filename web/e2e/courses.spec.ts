import { expect, request, test } from "@playwright/test";

// Courses end-to-end: a professor creates a course through the form
// and lands on its detail route, then a student browses the course list
// and joins the new course with a single click.
//
// Uses dedicated, uniquely-named test-auth users (not the shared
// STUDENT_STORAGE_STATE_PATH fixture) so this test's course creation can
// never race or interfere with other specs running fullyParallel.
test.describe("courses browse, create, and join", () => {
  test("professor creates a course, student browses and joins it", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    // Base-36, not a raw millisecond timestamp: `computing_id` is a
    // varchar(32) column (see role_request.spec.ts for the same guard).
    const uniqueSuffix = Date.now().toString(36);

    const professorApi = await request.newContext({ baseURL });
    const professorLogin = await professorApi.post("/api/test-auth/login", {
      data: {
        computing_id: `courses-e2e-professor-${uniqueSuffix}`,
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
    await professorPage.goto("/courses/new");

    const courseCode = `E2E ${uniqueSuffix}`;
    await professorPage.getByLabel("Course code").fill(courseCode);
    await professorPage.getByLabel("Title").fill("Courses E2E Test Course");
    await professorPage.getByLabel("Term").fill("Fall 2026");
    await professorPage.getByRole("button", { name: "Create course" }).click();

    await professorPage.waitForURL(/\/courses\/[^/]+$/);
    await professorContext.close();

    const studentApi = await request.newContext({ baseURL });
    const studentLogin = await studentApi.post("/api/test-auth/login", {
      data: {
        computing_id: `courses-e2e-student-${uniqueSuffix}`,
        role: "student",
      },
    });
    expect(studentLogin.ok()).toBe(true);
    const studentState = await studentApi.storageState();
    await studentApi.dispose();

    const studentContext = await browser.newContext({
      storageState: studentState,
    });
    const studentPage = await studentContext.newPage();
    await studentPage.goto("/courses");

    const courseCard = studentPage
      .locator("li")
      .filter({ hasText: courseCode });
    await expect(courseCard).toBeVisible();
    await courseCard.getByRole("button", { name: "Join course" }).click();
    await expect(
      courseCard.getByRole("button", { name: "Joined" }),
    ).toBeVisible();

    await studentContext.close();
  });
});
