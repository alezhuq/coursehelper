import { expect, request, test } from "@playwright/test";

// AUTH-04 end-to-end: a student requests the professor role, an admin
// approves it, and the student's role becomes professor on re-fetch.
//
// Uses dedicated, uniquely-named test-auth users (not the shared
// STUDENT_STORAGE_STATE_PATH fixture from global-setup.ts) so this test's
// role promotion can never race or interfere with auth.spec.ts's plain
// "shows the student role" assertion when the suite runs fullyParallel.
test.describe("professor role request and approval", () => {
  test("student requests, admin approves, student's role becomes professor", async ({
    browser,
  }) => {
    const baseURL = process.env.BASE_URL ?? "http://localhost:8080";
    // Base-36, not a raw millisecond timestamp: `computing_id` is a
    // varchar(32) column, and "role-req-e2e-student-<13-digit-ms>" alone
    // overflows that (the login route then 500s, leaving no session
    // cookie, and the page silently redirects to /login).
    const uniqueSuffix = Date.now().toString(36);

    const studentApi = await request.newContext({ baseURL });
    const studentLogin = await studentApi.post("/api/test-auth/login", {
      data: {
        computing_id: `role-req-e2e-student-${uniqueSuffix}`,
        role: "student",
      },
    });
    expect(studentLogin.ok()).toBe(true);
    const studentMe = await studentApi.get("/api/auth/me");
    const studentUser = (await studentMe.json()) as { id: string };
    const studentState = await studentApi.storageState();
    await studentApi.dispose();

    const studentContext = await browser.newContext({
      storageState: studentState,
    });
    const studentPage = await studentContext.newPage();
    await studentPage.goto("/");

    await studentPage
      .getByRole("button", { name: "Request professor role" })
      .click();
    await expect(studentPage.getByTestId("role-request-status")).toHaveText(
      "Professor role request: pending",
    );

    const adminApi = await request.newContext({ baseURL });
    const adminLogin = await adminApi.post("/api/test-auth/login", {
      data: {
        computing_id: `role-req-e2e-admin-${uniqueSuffix}`,
        role: "admin",
      },
    });
    expect(adminLogin.ok()).toBe(true);
    const adminState = await adminApi.storageState();
    await adminApi.dispose();

    const adminContext = await browser.newContext({
      storageState: adminState,
    });
    const adminPage = await adminContext.newPage();
    await adminPage.goto("/admin");

    const requestRow = adminPage
      .getByTestId("pending-request")
      .filter({ hasText: studentUser.id });
    await expect(requestRow).toBeVisible();
    await requestRow.getByRole("button", { name: "Approve" }).click();
    await expect(requestRow).toHaveCount(0);

    await studentPage.reload();
    await expect(studentPage.getByTestId("role")).toHaveText("professor");

    await studentContext.close();
    await adminContext.close();
  });
});
