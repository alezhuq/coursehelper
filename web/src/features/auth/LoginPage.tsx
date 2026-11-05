import { useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { apiFetch } from "../../api/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const SFU_EMAIL_PATTERN = /^[^\s@]+@sfu\.ca$/i;

type RequestState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "sent" }
  | { kind: "error"; message: string };

/**
 * Sign-in page. SFU CAS is the prominent primary path;
 * the @sfu.ca magic-link form below it is the ALWAYS-VISIBLE fallback -
 * never hidden behind a "CAS failed" state, so degraded CAS never
 * blocks sign-in. When the CAS callback bounced back with
 * ?cas=unavailable (CAS timed out or errored), a clear message directs the
 * user to the magic-link option instead of a generic error.
 *
 * Styled with the shadcn/Tailwind design system (centered Card, no AppHeader
 * since this page is pre-auth), matching CourseCreatePage's form patterns.
 */
export function LoginPage() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<RequestState>({ kind: "idle" });
  const [searchParams] = useSearchParams();
  const casUnavailable = searchParams.get("cas") === "unavailable";

  const isValidSfuEmail = SFU_EMAIL_PATTERN.test(email.trim());

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isValidSfuEmail) {
      setState({
        kind: "error",
        message: "Enter a valid @sfu.ca email address.",
      });
      return;
    }

    setState({ kind: "submitting" });
    try {
      await apiFetch("/auth/magic-link/request", {
        method: "POST",
        body: JSON.stringify({ email: email.trim() }),
      });
      setState({ kind: "sent" });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Something went wrong.";
      setState({ kind: "error", message });
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-8 py-16">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 text-center">
          <h1 className="text-[28px] font-semibold leading-[1.2] text-slate-900">
            Student Course Library
          </h1>
          <p className="mt-2 text-base text-slate-500">
            Sign in with your SFU account
          </p>
        </div>

        <Card className="gap-6 p-6">
          {/* Primary path: full-page navigation (not fetch) so the browser
              follows the 302 to cas.sfu.ca and back through the callback. */}
          <Button asChild className="w-full">
            <a href="/api/auth/cas/login" data-testid="cas-login">
              Sign in with SFU CAS
            </a>
          </Button>

          {casUnavailable && (
            <Alert variant="destructive" data-testid="cas-unavailable">
              <AlertDescription>
                SFU CAS sign-in is currently unavailable. Use the magic-link
                sign-in below instead - it works independently of CAS.
              </AlertDescription>
            </Alert>
          )}

          <div className="flex items-center gap-3">
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-medium tracking-wide text-slate-400 uppercase">
              or
            </span>
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          {state.kind === "sent" ? (
            <p
              data-testid="magic-link-sent"
              className="text-center text-sm leading-[1.5] text-slate-500"
            >
              Check your @sfu.ca inbox for a sign-in link. It expires in 15
              minutes.
            </p>
          ) : (
            <form
              className="flex flex-col gap-4"
              onSubmit={(event) => void handleSubmit(event)}
            >
              <p className="text-sm leading-[1.5] text-slate-500">
                If CAS is unavailable, sign in with a magic link sent to your
                @sfu.ca email:
              </p>
              <div className="flex flex-col gap-2">
                <Label htmlFor="email">SFU email</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  placeholder="you@sfu.ca"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={state.kind === "submitting"}
              >
                {state.kind === "submitting" ? "Sending..." : "Send magic link"}
              </Button>
              {state.kind === "error" && (
                <Alert variant="destructive">
                  <AlertDescription role="alert">
                    {state.message}
                  </AlertDescription>
                </Alert>
              )}
            </form>
          )}
        </Card>
      </div>
    </main>
  );
}
