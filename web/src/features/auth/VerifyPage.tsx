import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { apiFetch } from "../../api/client";

type VerifyState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

/**
 * Magic-link landing page. The emailed link points here - an SPA
 * route - instead of at the API, so opening the link never redeems the
 * single-use token by itself. Email link scanners (O365/Outlook prefetch)
 * follow GETs in inbound mail; only the explicit "Sign in" click below
 * POSTs the token to /api/auth/magic-link/verify, which is what actually
 * redeems it and sets the session cookie.
 */
export function VerifyPage() {
  const [state, setState] = useState<VerifyState>({ kind: "idle" });
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token");

  if (!token) {
    return (
      <main>
        <h1>Student Course Library</h1>
        <p role="alert">
          This sign-in link is missing its token. Request a new one from the{" "}
          <Link to="/login">sign-in page</Link>.
        </p>
      </main>
    );
  }

  async function handleSignIn() {
    setState({ kind: "submitting" });
    try {
      await apiFetch("/auth/magic-link/verify", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      navigate("/", { replace: true });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Sign-in failed.";
      setState({ kind: "error", message });
    }
  }

  return (
    <main>
      <h1>Student Course Library</h1>
      <p>Click the button below to finish signing in.</p>
      <button
        type="button"
        data-testid="verify-sign-in"
        onClick={() => void handleSignIn()}
        disabled={state.kind === "submitting"}
      >
        {state.kind === "submitting" ? "Signing in..." : "Sign in"}
      </button>
      {state.kind === "error" && (
        <p role="alert">
          {state.message} You can request a new link from the{" "}
          <Link to="/login">sign-in page</Link>.
        </p>
      )}
    </main>
  );
}
