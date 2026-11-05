import { expect, test } from "@playwright/test";

// Login-page contract: SFU CAS is the
// primary sign-in and the @sfu.ca magic-link form is ALWAYS visible as the
// fallback - both rendered unconditionally, with the CAS button targeting
// the API's redirect route.
test.describe("login page sign-in options", () => {
  test("always shows both the CAS button and the magic-link form", async ({
    page,
  }) => {
    await page.goto("/login");

    const casButton = page.getByTestId("cas-login");
    await expect(casButton).toBeVisible();
    await expect(casButton).toHaveText("Sign in with SFU CAS");
    await expect(casButton).toHaveAttribute("href", "/api/auth/cas/login");

    // The magic-link fallback is rendered alongside, never hidden behind a
    // CAS-failure state.
    await expect(page.getByLabel("SFU email")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Send magic link" }),
    ).toBeVisible();
  });

  test("degraded CAS callback surfaces the magic-link fallback message", async ({
    page,
  }) => {
    // The CAS callback bounces browsers here with ?cas=unavailable when
    // cas.sfu.ca times out or errors (distinct 503 path).
    await page.goto("/login?cas=unavailable");

    await expect(page.getByTestId("cas-unavailable")).toBeVisible();
    await expect(page.getByTestId("cas-unavailable")).toContainText(
      "magic-link",
    );

    // Both sign-in options remain usable in the degraded state.
    await expect(page.getByTestId("cas-login")).toBeVisible();
    await expect(page.getByLabel("SFU email")).toBeVisible();
  });
});
