/**
 * Shared fetch helper for the API.
 *
 * Always sends `credentials: 'include'` so the HttpOnly session cookie set
 * by /auth/magic-link/verify (or /auth/cas/callback) rides
 * along with every request; parses the single JSON error shape the backend
 * returns (FastAPI's `{"detail": "..."}`) into a thrown Error the UI can
 * toast/render.
 *
 * Mutating requests (POST/PUT/PATCH/DELETE) also get the custom CSRF header
 * (`app/auth/csrf.py`) - a cross-site `<form>`
 * post has no way to set an arbitrary header, so this is the frontend half
 * of that defense-in-depth check on role-request/approval routes.
 */

const CSRF_HEADER_NAME = "X-Requested-With";
const CSRF_HEADER_VALUE = "fetch";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (MUTATING_METHODS.has(method)) {
    headers[CSRF_HEADER_NAME] = CSRF_HEADER_VALUE;
  }

  const response = await fetch(`/api${path}`, {
    ...init,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      // `detail` is not always a string: the CAS degraded 503 returns
      // {code, message} and FastAPI 422 validation errors return an array.
      // Only adopt shapes we can render as text; anything else keeps the
      // generic status-based message instead of "[object Object]".
      const body = (await response.clone().json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (
        body.detail &&
        typeof (body.detail as { message?: unknown }).message === "string"
      ) {
        message = (body.detail as { message: string }).message;
      }
    } catch {
      // Response body wasn't JSON (or was empty) - fall back to the
      // generic status-based message above.
    }
    throw new ApiError(response.status, message);
  }

  return response;
}
