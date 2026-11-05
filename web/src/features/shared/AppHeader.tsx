import { Link, useNavigate } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { Button } from "@/components/ui/button";

/**
 * Minimal shared app header: a slate-100
 * band containing only the app name (links to "/") and a "Log out" control.
 * Kept to exactly these two elements, no menu system.
 *
 * Logout logic mirrors DashboardPage.handleLogout - a 401 just means the
 * session is already gone, so /login is the right destination either way.
 */
export function AppHeader() {
  const navigate = useNavigate();

  async function handleLogout() {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        // Logout failures other than "already logged out" still navigate
        // to /login - there is no header-level error surface, and a failed
        // logout should never strand the user on an authenticated page.
      }
    }
    navigate("/login", { replace: true });
  }

  return (
    <header className="border-b border-slate-200 bg-slate-100 px-8 py-4">
      <div className="mx-auto flex max-w-[720px] items-center justify-between">
        <Link
          to="/"
          className="text-xl font-semibold leading-[1.2] text-slate-900"
        >
          Student Course Library
        </Link>
        <Button
          type="button"
          variant="ghost"
          className="text-slate-900 hover:bg-slate-200"
          onClick={() => void handleLogout()}
        >
          Log out
        </Button>
      </div>
    </header>
  );
}
