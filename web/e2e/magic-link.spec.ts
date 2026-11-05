import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

/**
 * nginx-in-the-loop magic-link regression test.
 *
 * Unlike auth.spec.ts's request-form-only assertion and the ASGI-transport
 * integration test (api/tests/integration/test_magic_link.py, which never
 * touches nginx), this test drives the LITERAL emailed URL through the real
 * nginx-proxied stack: request the link -> read it out of Mailpit's inbox ->
 * follow the exact `/auth/verify?token=...` URL through nginx's SPA
 * fallback -> POST-redeem from the verify page -> land authenticated on the
 * dashboard with a real `session` cookie. It deliberately starts from a
 * logged-out browser context (no storageState) instead of the test-auth
 * bypass storageState the rest of this suite reuses, since the whole point
 * here is exercising the real sign-in flow end to end.
 *
 * Requires the nginx + Mailpit E2E overlay - NOT the default dev-override
 * stack, which replaces `web` with the Vite dev server and therefore cannot
 * reproduce an nginx SPA-routing regression even if the link is followed:
 *
 *   docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --wait
 *   cd web && BASE_URL=http://localhost:8080 npx playwright test e2e/magic-link.spec.ts
 */
test.describe("magic-link sign-in through real nginx + Mailpit", () => {
  // Explicit empty storage state: never inherit auth.spec.ts's
  // STUDENT_STORAGE_STATE_PATH bypass session. This spec must prove the
  // real request -> email -> verify flow, not the test-auth shortcut.
  test.use({ storageState: { cookies: [], origins: [] } });

  test("follows the emailed link through nginx and lands authenticated", async ({
    page,
    request,
    baseURL,
  }) => {
    // Unique recipient per run: dodges both the 60s per-email resend
    // throttle (app/auth/magic_link.py MAGIC_LINK_RESEND_INTERVAL_SECONDS)
    // and nginx's 6r/m limit_req zone on the request endpoint across
    // repeated suite runs against the same long-lived stack.
    const email = `e2e-magic-link-${Date.now()}@sfu.ca`;

    await page.goto("/login");
    await page.getByLabel("SFU email").fill(email);
    await page.getByRole("button", { name: "Send magic link" }).click();
    await expect(page.getByTestId("magic-link-sent")).toBeVisible();

    // Read the emitted link out of Mailpit's HTTP API - not
    // magic_link.LAST_SENT_LINK (a test-only in-process hook the API's own
    // integration suite uses). The point of this test is proving the mail
    // that actually left the api container contains a link real nginx can
    // serve, so it must come from the inbox, not app memory.
    const link = await readMagicLinkFromMailpit(request, email);

    // Locks WR-06's shape: the emailed link must land on the SPA verify
    // page, never a directly GET-redeemable API URL - a mail scanner's
    // prefetch (O365/Outlook) would otherwise burn the single-use token
    // before the user ever clicked. Do NOT "fix" this back to an
    // `/api`-prefixed link if this assertion ever fails.
    expect(link.startsWith(`${baseURL}/auth/verify?token=`)).toBe(true);
    expect(link).not.toContain("/api/");

    // Navigate the SAME browser context - the `login_state` cookie set
    // when the link was requested above travels with it - to the literal
    // emailed URL, through real nginx (not a mocked route, not Vite).
    await page.goto(link);
    await page.getByTestId("verify-sign-in").click();

    await expect(page).toHaveURL(`${baseURL}/`);
    await expect(page.getByTestId("role")).toHaveText("student");
    await expect(page.getByTestId("display-name")).toBeVisible();

    // A real session, not just a redirect: assert the `session` cookie
    // nginx forwarded from the api's Set-Cookie is actually present on the
    // context (WR-06/WR-07 both culminate in this cookie being issued).
    const cookies = await page.context().cookies();
    const sessionCookie = cookies.find((cookie) => cookie.name === "session");
    expect(sessionCookie).toBeDefined();
    expect(sessionCookie?.value.length ?? 0).toBeGreaterThan(0);
  });
});

/**
 * Polls Mailpit's HTTP API (http://localhost:8025 by default) for the
 * magic-link email sent to `email` and extracts the `/auth/verify?token=...`
 * URL from its body. The api container sends the email synchronously as
 * part of handling POST /auth/magic-link/request (magic_link.py awaits
 * send_magic_link_email before returning 202), but Mailpit's own indexing
 * carries no documented consistency guarantee, so this retries briefly
 * rather than asserting on the very first read.
 */
async function readMagicLinkFromMailpit(
  request: APIRequestContext,
  email: string,
): Promise<string> {
  const mailpitBaseUrl = process.env.MAILPIT_BASE_URL ?? "http://localhost:8025";
  const deadline = Date.now() + 10_000;

  while (Date.now() < deadline) {
    const searchResponse = await request.get(
      `${mailpitBaseUrl}/api/v1/search?query=${encodeURIComponent(email)}`,
    );
    expect(searchResponse.ok()).toBeTruthy();
    const searchBody = (await searchResponse.json()) as {
      messages: { ID: string }[];
    };

    if (searchBody.messages.length > 0) {
      const messageResponse = await request.get(
        `${mailpitBaseUrl}/api/v1/message/${searchBody.messages[0].ID}`,
      );
      expect(messageResponse.ok()).toBeTruthy();
      const message = (await messageResponse.json()) as { Text: string };
      const match = message.Text.match(/https?:\/\/\S+\/auth\/verify\?token=\S+/);
      if (match) {
        return match[0].trim();
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(`No magic-link email found in Mailpit for ${email} within 10s`);
}
