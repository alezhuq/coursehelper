import { expect, test } from "@playwright/test";

import { STUDENT_STORAGE_STATE_PATH } from "./storage-state-path";

test.describe("authenticated dashboard (login bypass)", () => {
  test.use({ storageState: STUDENT_STORAGE_STATE_PATH });

  test("shows the student role via the test-auth bypass session", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(page.getByTestId("role")).toHaveText("student");
    await expect(page.getByTestId("display-name")).toBeVisible();
  });
});

test.describe("magic-link request form", () => {
  test("submitting an @sfu.ca email renders the success state", async ({
    page,
  }) => {
    await page.goto("/login");

    await page.getByLabel("SFU email").fill("student@sfu.ca");
    await page.getByRole("button", { name: "Send magic link" }).click();

    await expect(page.getByTestId("magic-link-sent")).toBeVisible();
  });
});
