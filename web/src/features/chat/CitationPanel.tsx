import { useCallback, useMemo, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { ExternalLink, X } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

// Must be set in the SAME module that renders <Document>/<Page> - mirrors
// ViewerPage.tsx's own workerSrc setup.
// pdfjs.GlobalWorkerOptions is a shared singleton so setting it again here
// is safe/idempotent; this module still owns its own assignment since it
// can mount without ViewerPage ever having mounted first.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface CitationPanelProps {
  courseId: string;
  bookId: string;
  filename: string;
  page: number;
  onClose: () => void;
}

/**
 * The split-right citation panel. Reuses the existing `GET /courses/:id/books/:bookId/file`
 * endpoint (`require_course_enrollment`-gated) via
 * react-pdf exactly as `ViewerPage` does - a single page at fixed 100%
 * zoom, no prev/next/zoom controls, deliberately simpler than the full
 * viewer. A student who wants to navigate further uses "Open full viewer".
 *
 * No redirect-on-403 here (unlike ViewerPage's 403 convention): a citation
 * only ever references the current course's own retrieved chunks
 * (course-boundary isolation enforced server-side), so a
 * 403 should not be reachable in practice - showing the generic error
 * keeps the student's conversation intact rather than yanking them out of
 * an active chat session on an edge case.
 */
export function CitationPanel({
  courseId,
  bookId,
  filename,
  page,
  onClose,
}: CitationPanelProps) {
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const fileSource = useMemo(
    () => ({
      url: `/api/courses/${courseId}/books/${bookId}/file`,
      withCredentials: true,
    }),
    [courseId, bookId],
  );

  const handleLoadSuccess = useCallback(() => {
    setLoaded(true);
  }, []);

  const handleLoadError = useCallback(() => {
    setLoadError(true);
  }, []);

  function handleRetry() {
    setLoadError(false);
    setLoaded(false);
    setReloadKey((key) => key + 1);
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-slate-50">
      <div className="flex items-center justify-between border-b border-slate-200 bg-slate-100 px-4 py-2">
        <div>
          <p className="break-words text-xl font-semibold leading-[1.2] text-slate-900">
            {filename}
          </p>
          <p className="text-sm text-slate-500">p. {page}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild type="button" variant="ghost">
            <a
              href={`/courses/${courseId}/books/${bookId}?page=${page}`}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink />
              Open full viewer
            </a>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close source panel"
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
      </div>

      <div className="flex flex-1 justify-center overflow-y-auto py-8">
        {loadError ? (
          <div className="flex flex-col items-center gap-4 py-16">
            <Alert variant="destructive" className="max-w-md">
              <AlertDescription>
                Could not load this PDF. Try again.
              </AlertDescription>
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
              <p className="py-16 text-center text-sm text-slate-500">
                Loading PDF…
              </p>
            }
            error={null}
          >
            {loaded && (
              <div className="border border-slate-200 bg-white shadow-sm">
                <Page pageNumber={page} scale={1.0} />
              </div>
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
