import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { AppHeader } from "../shared/AppHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  auth_method: string;
}

interface RoleRequestSummary {
  id: string;
  status: "pending" | "approved" | "denied";
}

type DashboardState =
  | { kind: "loading" }
  | { kind: "ok"; user: CurrentUser; roleRequest: RoleRequestSummary | null }
  | { kind: "error"; message: string };

/**
 * Lands the user here after a successful CAS or magic-link sign-in.
 * Fetches /auth/me (re-derived from the DB, never trusts a cached role) and
 * redirects to /login on a 401 - the session cookie was missing, expired,
 * or the user no longer exists.
 *
 * Students also see a "Request professor role" control and the
 * current status of their most recent request, fetched from
 * `GET /auth/role-requests/me` - a read-only lookup that never itself
 * submits a request, unlike the idempotent POST.
 */
export function DashboardPage() {
  const [state, setState] = useState<DashboardState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const loadDashboard = useCallback(async () => {
    try {
      const [userResponse, requestResponse] = await Promise.all([
        apiFetch("/auth/me"),
        apiFetch("/auth/role-requests/me"),
      ]);
      const user = (await userResponse.json()) as CurrentUser;
      const roleRequest =
        (await requestResponse.json()) as RoleRequestSummary | null;
      setState({ kind: "ok", user, roleRequest });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      const message =
        error instanceof Error ? error.message : "Unknown error";
      setState({ kind: "error", message });
    }
  }, [navigate]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  async function handleRequestProfessorRole() {
    setSubmitting(true);
    try {
      await apiFetch("/auth/role-requests", { method: "POST" });
      await loadDashboard();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Action failed",
      });
    } finally {
      setSubmitting(false);
    }
  }

  if (state.kind === "loading") {
    return (
      <main>
        <AppHeader />
        <p className="py-16 text-center text-sm text-slate-500">Loading...</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main>
        <AppHeader />
        <div className="mx-auto max-w-[720px] px-8 py-8">
          <Alert variant="destructive">
            <AlertDescription>
              Could not load your account: {state.message}
            </AlertDescription>
          </Alert>
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => void loadDashboard()}
          >
            Try again
          </Button>
        </div>
      </main>
    );
  }

  const { user, roleRequest } = state;
  const canRequestProfessorRole =
    user.role === "student" &&
    (roleRequest === null || roleRequest.status === "denied");

  return (
    <main>
      <AppHeader />
      <div className="mx-auto max-w-[720px] px-8 py-8">
        <div className="mb-8">
          <h1 className="text-[28px] font-semibold leading-[1.2] text-slate-900">
            Welcome back
          </h1>
          <p className="mt-1 text-base text-slate-500">
            Your Student Course Library account
          </p>
        </div>

        <Card className="gap-4 px-6 py-6">
          <div className="flex items-center justify-between">
            <div>
              <p
                data-testid="display-name"
                className="text-xl font-semibold leading-[1.2] text-slate-900"
              >
                {user.display_name}
              </p>
              <p className="mt-1 text-sm leading-[1.5] text-slate-500">
                {user.email}
              </p>
            </div>
            <Badge data-testid="role" variant="secondary">
              {user.role}
            </Badge>
          </div>

          {user.role === "student" && (
            <div className="border-t border-slate-200 pt-4">
              {roleRequest !== null && (
                <p
                  data-testid="role-request-status"
                  className="text-sm text-slate-500"
                >
                  Professor role request: {roleRequest.status}
                </p>
              )}
              {canRequestProfessorRole && (
                <Button
                  type="button"
                  className={roleRequest !== null ? "mt-3" : undefined}
                  onClick={() => void handleRequestProfessorRole()}
                  disabled={submitting}
                >
                  {submitting ? "Requesting..." : "Request professor role"}
                </Button>
              )}
            </div>
          )}
        </Card>

        <div className="mt-6">
          <Button type="button" onClick={() => navigate("/courses")}>
            Browse courses
          </Button>
        </div>
      </div>
    </main>
  );
}
