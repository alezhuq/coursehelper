import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ViewerToolbar } from "./ViewerToolbar";

// Must be set in the SAME module that renders <Document>/<Page> - react-pdf's
// own README warns that setting it elsewhere can be overwritten by import
// order.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export const MIN_ZOOM = 0.5;
export const MAX_ZOOM = 2.0;
export const ZOOM_STEP = 0.25;
const DEFAULT_ZOOM = 1.0;

function parsePageParam(raw: string | null): number {
  if (!raw) return 1;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return 1;
  return parsed;
}

function clampPage(page: number, numPages: number | null): number {
  if (numPages === null) return page;
  return Math.min(Math.max(page, 1), numPages);
}

/**
 * `/courses/:id/books/:bookId`. Dedicated full-page surface - no `AppHeader`, its own
 * minimal sticky toolbar instead. Renders the requested `?page=N` via
 * react-pdf; the page is only clamped to `[1, numPages]` once
 * `onLoadSuccess` reports `numPages` - before
 * that the toolbar stays disabled and shows the raw URL value.
 *
 * pdf.js issues its own internal fetches for the PDF bytes - this is the
 * one place in the frontend that bypasses `apiFetch` entirely.
 * `withCredentials: true` carries the session cookie on those fetches
 * (same-origin, no CORS) so `GET /courses/:id/books/:bookId/file`
 * (`require_course_enrollment`-gated) authorizes normally. A 403
 * response surfaces to react-pdf as a `ResponseException` with a `status`
 * field (not part of react-pdf's own `Error` type, hence the runtime
 * cast in `handleLoadError`) - that redirects to `/` immediately (SC4),
 * matching every other course-scoped page's enrollment-denial convention.
 * No passage-level highlight is ever drawn on the page - page-level
 * precision only.
 */
export function ViewerPage() {
  const { id: courseId, bookId } = useParams<{ id: string; bookId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(() => parsePageParam(searchParams.get("page")));
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Deep-link changes (e.g. clicking a different search result while this
  // book's viewer route is already mounted - same path, new ?page=N) re-
  // anchor the requested page; the [1, numPages] clamp itself only applies
  // once numPages is known (derived value below), never here.
  useEffect(() => {
    setPage(parsePageParam(searchParams.get("page")));
  }, [searchParams]);

  const displayedPage = useMemo(() => clampPage(page, numPages), [page, numPages]);

  const fileSource = useMemo(
    () => ({
      url: `/api/courses/${courseId}/books/${bookId}/file`,
      withCredentials: true,
    }),
    [courseId, bookId],
  );

  const handleLoadSuccess = useCallback((document: { numPages: number }) => {
    setNumPages(document.numPages);
  }, []);

  const handleLoadError = useCallback(
    (error: Error) => {
      const status = (error as Error & { status?: number }).status;
      if (status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setLoadError(true);
    },
    [navigate],
  );

  function handleRetry() {
    setLoadError(false);
    setNumPages(null);
    setReloadKey((key) => key + 1);
  }

  function handlePrev() {
    setPage(Math.max(1, displayedPage - 1));
  }

  function handleNext() {
    setPage(numPages === null ? displayedPage : Math.min(numPages, displayedPage + 1));
  }

  function handleJump(next: number) {
    if (numPages === null || !Number.isFinite(next)) return;
    setPage(Math.min(Math.max(Math.trunc(next), 1), numPages));
  }

  function handleZoomIn() {
    setZoom((current) => Math.min(MAX_ZOOM, Number((current + ZOOM_STEP).toFixed(2))));
  }

  function handleZoomOut() {
    setZoom((current) => Math.max(MIN_ZOOM, Number((current - ZOOM_STEP).toFixed(2))));
  }

  return (
    <main className="flex h-screen flex-col">
      <ViewerToolbar
        courseId={courseId ?? ""}
        page={displayedPage}
        numPages={numPages}
        zoom={zoom}
        onPrev={handlePrev}
        onNext={handleNext}
        onJump={handleJump}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
      />
      <div className="flex flex-1 justify-center overflow-y-auto bg-slate-50 py-8">
        {loadError ? (
          <div className="flex flex-col items-center gap-4 py-16">
            <Alert variant="destructive" className="max-w-md">
              <AlertDescription>Could not load this PDF. Try again.</AlertDescription>
            </Alert>
            <Button type="button" variant="outline" onClick={handleRetry}>
              Try again
            </Button>
          </div>
        ) : (
          <Document
            key={reloadKey}
            file={fileSource}
            onLoadSuccess={handleLoadSuccess}
            onLoadError={handleLoadError}
            loading={
              <p className="py-16 text-center text-sm text-slate-500">Loading PDF…</p>
            }
            error={null}
          >
            {numPages !== null && (
              <div className="border border-slate-200 bg-white shadow-sm">
                <Page pageNumber={displayedPage} scale={zoom} />
              </div>
            )}
          </Document>
        )}
      </div>
    </main>
  );
}
