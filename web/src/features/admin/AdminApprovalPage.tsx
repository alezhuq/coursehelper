import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";

interface PendingRoleRequest {
  id: string;
  user_id: string;
  status: string;
  requested_at: string;
}

type PageState =
  | { kind: "loading" }
  | { kind: "ok"; requests: PendingRoleRequest[] }
  | { kind: "error"; message: string };

/**
 * Admin-only approval queue. Lists pending professor-role
 * requests from `GET /auth/role-requests` (admin-gated by
 * `require_role("admin")` server-side) and lets an admin approve or deny
 * each one; the list refreshes after every decision so a stale queue never
 * shows an already-decided request.
 *
 * A non-admin hitting `/admin` gets a 403 from the list call and is
 * redirected to `/` immediately, same as the dashboard's 401 -> /login
 * redirect for a missing session.
 */
export function AdminApprovalPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [actingOn, setActingOn] = useState<string | null>(null);
  const navigate = useNavigate();

  const loadPending = useCallback(async () => {
    try {
      const response = await apiFetch("/auth/role-requests");
      const requests = (await response.json()) as PendingRoleRequest[];
      setState({ kind: "ok", requests });
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.status === 403 || error.status === 401)
      ) {
        navigate("/", { replace: true });
        return;
      }
      const message =
        error instanceof Error ? error.message : "Unknown error";
      setState({ kind: "error", message });
    }
  }, [navigate]);

  useEffect(() => {
    void loadPending();
  }, [loadPending]);

  async function decide(requestId: string, decision: "approve" | "deny") {
    setActingOn(requestId);
    try {
      await apiFetch(`/auth/role-requests/${requestId}/${decision}`, {
        method: "POST",
      });
      await loadPending();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Another admin decided this request first - refresh the queue so
        // the stale entry disappears instead of failing silently.
        await loadPending();
      } else {
        setState({
          kind: "error",
          message:
            error instanceof Error ? error.message : "Action failed",
        });
      }
    } finally {
      setActingOn(null);
    }
  }

  if (state.kind === "loading") {
    return (
      <main>
        <p>Loading...</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main>
        <p role="alert">Could not load pending requests: {state.message}</p>
      </main>
    );
  }

  return (
    <main>
      <h1>Professor role requests</h1>
      {state.requests.length === 0 && <p>No pending requests.</p>}
      <ul>
        {state.requests.map((request) => (
          <li key={request.id} data-testid="pending-request">
            <span>{request.user_id}</span>
            <button
              type="button"
              onClick={() => void decide(request.id, "approve")}
              disabled={actingOn === request.id}
            >
              Approve
            </button>
            <button
              type="button"
              onClick={() => void decide(request.id, "deny")}
              disabled={actingOn === request.id}
            >
              Deny
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
