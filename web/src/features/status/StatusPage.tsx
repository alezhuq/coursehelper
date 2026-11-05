import { useEffect, useState } from "react";

interface HealthResponse {
  status: string;
  db?: string;
}

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

/**
 * Walking-skeleton UI: fetches live backend health through the nginx /api
 * proxy and renders it, proving nginx -> api -> db end to end in a browser.
 */
export function StatusPage() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function loadHealth() {
      try {
        const response = await fetch("/api/health");
        if (!response.ok) {
          throw new Error(`/api/health responded with ${response.status}`);
        }
        const data = (await response.json()) as HealthResponse;
        if (!cancelled) {
          setState({ kind: "ok", data });
        }
      } catch (error) {
        if (!cancelled) {
          const message =
            error instanceof Error ? error.message : "Unknown error";
          setState({ kind: "error", message });
        }
      }
    }

    void loadHealth();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1>Student Course Library</h1>
      {state.kind === "loading" && <p>Checking backend status...</p>}
      {state.kind === "ok" && state.data.status === "ok" && (
        <p>Backend status: ok</p>
      )}
      {state.kind === "ok" && state.data.status !== "ok" && (
        <p>Backend status: {state.data.status}</p>
      )}
      {state.kind === "error" && <p>Backend status: error ({state.message})</p>}
    </main>
  );
}
