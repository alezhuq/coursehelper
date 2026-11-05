import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { AppHeader } from "../shared/AppHeader";
import { type Book, useFileUpload } from "../uploads/useFileUpload";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  auth_method: string;
}

interface Course {
  id: string;
  owner_id: string;
  code: string;
  title: string;
  term: string;
  created_at: string;
}

type PageState =
  | { kind: "loading" }
  | { kind: "ok"; user: CurrentUser; course: Course; books: Book[] }
  | { kind: "error"; message: string };

// Status badge palette - color is
// never the only signal, the badge text always contains the status word.
const STATUS_BADGES: Record<
  Book["status"],
  { label: string; className: string }
> = {
  queued: { label: "Queued", className: "bg-slate-100 text-slate-600" },
  processing: { label: "Processing", className: "bg-blue-100 text-blue-700" },
  ready: { label: "Ready", className: "bg-green-100 text-green-700" },
  failed: { label: "Failed", className: "bg-red-100 text-red-700" },
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${Math.round(value)} ${units[unitIndex]}`;
}

/**
 * `/courses/:id`. Mirrors
 * AdminApprovalPage's loading|ok|error state machine and CourseListPage's
 * 401->/login / 403->/ redirect convention. There is no single-course GET,
 * so the course header comes from GET /courses
 * filtered by :id, fetched alongside GET /courses/:id/books.
 *
 * Owner/admin (course.owner_id === me.id || me.role === "admin") gets the
 * upload dropzone and the Actions column; any other enrolled user sees the
 * books table read-only. While any book is queued/processing the page
 * polls the book list every 3000ms.
 */
export function CourseDetailPage() {
  const { id: courseId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [pageError, setPageError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Book | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(
    null,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    upload,
    progress,
    status: uploadStatus,
    error: uploadError,
    cancel,
  } = useFileUpload();

  const loadAll = useCallback(async () => {
    if (!courseId) return;
    setState({ kind: "loading" });
    try {
      const [meResponse, coursesResponse, booksResponse] = await Promise.all([
        apiFetch("/auth/me"),
        apiFetch("/courses"),
        apiFetch(`/courses/${courseId}/books`),
      ]);
      const user = (await meResponse.json()) as CurrentUser;
      const courses = (await coursesResponse.json()) as Course[];
      const books = (await booksResponse.json()) as Book[];
      const course = courses.find((candidate) => candidate.id === courseId);
      if (!course) {
        setState({ kind: "error", message: "Could not load this page." });
        return;
      }
      setState({ kind: "ok", user, course, books });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError && error.status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setState({ kind: "error", message: "Could not load this page." });
    }
  }, [courseId, navigate]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const refreshBooks = useCallback(async () => {
    if (!courseId) return;
    try {
      const response = await apiFetch(`/courses/${courseId}/books`);
      const books = (await response.json()) as Book[];
      setState((previous) =>
        previous.kind === "ok" ? { ...previous, books } : previous,
      );
    } catch {
      // Transient polling failures keep the last known state on screen
      // rather than flashing an error banner.
    }
  }, [courseId]);

  // Status freshness: poll every 3000ms while any
  // book is queued/processing; stop otherwise, resume after upload/retry
  // since both flow back through this same `state` re-render.
  useEffect(() => {
    if (state.kind !== "ok") return;
    const hasActive = state.books.some(
      (book) => book.status === "queued" || book.status === "processing",
    );
    if (!hasActive) return;
    const interval = setInterval(() => void refreshBooks(), 3000);
    return () => clearInterval(interval);
  }, [state, refreshBooks]);

  async function handleFileSelected(file: File) {
    if (!courseId || uploadStatus === "uploading") return;
    setPageError(null);
    setUploadingFilename(file.name);
    try {
      const book = await upload(courseId, file);
      if (book) {
        await refreshBooks();
      }
    } catch {
      // The hook's own `error`/`status` state already reflects the
      // failure; the dropzone re-renders from that (Alert above it).
    } finally {
      setUploadingFilename(null);
    }
  }

  async function handleRetry(book: Book) {
    if (!courseId) return;
    setRetryingId(book.id);
    try {
      const response = await apiFetch(
        `/courses/${courseId}/books/${book.id}/retry`,
        { method: "POST" },
      );
      const updated = (await response.json()) as Book;
      setState((previous) =>
        previous.kind === "ok"
          ? {
              ...previous,
              books: previous.books.map((candidate) =>
                candidate.id === updated.id ? updated : candidate,
              ),
            }
          : previous,
      );
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Action failed");
    } finally {
      setRetryingId(null);
    }
  }

  async function handleDeleteConfirm() {
    if (!courseId || !deleteTarget) return;
    setDeleting(true);
    const targetId = deleteTarget.id;
    try {
      await apiFetch(`/courses/${courseId}/books/${targetId}`, {
        method: "DELETE",
      });
      setState((previous) =>
        previous.kind === "ok"
          ? {
              ...previous,
              books: previous.books.filter(
                (candidate) => candidate.id !== targetId,
              ),
            }
          : previous,
      );
      setDeleteTarget(null);
    } catch (error) {
      setDeleteTarget(null);
      setPageError(error instanceof Error ? error.message : "Action failed");
    } finally {
      setDeleting(false);
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
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => void loadAll()}
          >
            Try again
          </Button>
        </div>
      </main>
    );
  }

  const { user, course, books } = state;
  const isOwner = course.owner_id === user.id || user.role === "admin";
  const isUploading = uploadStatus === "uploading";

  return (
    <main>
      <AppHeader />
      <div className="mx-auto max-w-[720px] px-8 py-8">
        <h1 className="text-[28px] font-semibold leading-[1.2] text-slate-900">
          {course.code} {course.title}
        </h1>
        <div className="mt-1 flex items-center justify-between">
          <p className="text-sm leading-[1.5] text-slate-500">
            {course.term}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => navigate(`/courses/${courseId}/search`)}
            >
              Search
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => navigate(`/courses/${courseId}/chat`)}
            >
              Chat
            </Button>
          </div>
        </div>

        {pageError && (
          <Alert variant="destructive" className="mt-6">
            <AlertDescription>{pageError}</AlertDescription>
          </Alert>
        )}

        {isOwner && (
          <div className="mt-8">
            {uploadError && (
              <Alert variant="destructive" className="mb-4">
                <AlertDescription>{uploadError}</AlertDescription>
              </Alert>
            )}
            <div
              className="rounded-lg border-2 border-dashed border-slate-300 bg-slate-100 p-6"
              onDragOver={(event) => {
                event.preventDefault();
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (isUploading) return;
                const file = event.dataTransfer.files[0];
                if (file) void handleFileSelected(file);
              }}
            >
              {isUploading ? (
                <div className="flex flex-col gap-3">
                  <p className="text-base leading-[1.5] text-slate-900">
                    {uploadingFilename}
                  </p>
                  <Progress value={progress} />
                  <div className="flex items-center justify-between">
                    <p className="text-sm leading-[1.5] text-slate-500">
                      {progress}%
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={cancel}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3 text-center">
                  <p className="text-sm leading-[1.5] text-slate-500">
                    Drag a PDF here, or
                  </p>
                  <Button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    Upload PDF
                  </Button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void handleFileSelected(file);
                    }}
                  />
                </div>
              )}
            </div>
          </div>
        )}

        <div className="mt-8">
          {books.length === 0 ? (
            <div className="py-16 text-center">
              <p className="text-xl font-semibold leading-[1.2] text-slate-900">
                No books uploaded yet
              </p>
              <p className="mt-2 text-base leading-[1.5] text-slate-500">
                {isOwner
                  ? "Upload a PDF to start building this course's library."
                  : "Your professor has not uploaded any materials yet."}
              </p>
            </div>
          ) : (
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100">
                  <th className="px-2 py-2 text-sm font-medium text-slate-500">
                    Filename
                  </th>
                  <th className="px-2 py-2 text-sm font-medium text-slate-500">
                    Size
                  </th>
                  <th className="px-2 py-2 text-sm font-medium text-slate-500">
                    Status
                  </th>
                  {isOwner && (
                    <th className="px-2 py-2 text-sm font-medium text-slate-500">
                      Actions
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {books.map((book) => {
                  const badge = STATUS_BADGES[book.status];
                  return (
                    <tr key={book.id} className="border-b border-slate-100">
                      <td className="px-2 py-3 text-base text-slate-900">
                        {book.original_filename}
                      </td>
                      <td className="px-2 py-3 text-sm text-slate-500">
                        {formatBytes(book.size_bytes)}
                      </td>
                      <td className="px-2 py-3">
                        <Badge className={badge.className}>
                          {badge.label}
                        </Badge>
                        {book.status === "processing" && (
                          <p className="mt-1 text-sm text-slate-500">
                            This can take 20-45 minutes for large textbooks. You
                            can leave this page and come back - the status
                            updates automatically.
                          </p>
                        )}
                        {book.status === "failed" && book.status_error && (
                          <p className="mt-1 break-words text-sm text-red-700">
                            Error: {book.status_error}
                          </p>
                        )}
                      </td>
                      {isOwner && (
                        <td className="px-2 py-3">
                          <div className="flex gap-2">
                            {book.status === "failed" && (
                              <Button
                                type="button"
                                size="sm"
                                disabled={retryingId === book.id}
                                onClick={() => void handleRetry(book)}
                              >
                                Retry ingestion
                              </Button>
                            )}
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="border-red-600 text-red-600 hover:bg-red-50"
                              onClick={() => setDeleteTarget(book)}
                            >
                              Delete book
                            </Button>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {deleteTarget?.original_filename}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Students will no longer be able to access this book. This
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleting}
              onClick={(event) => {
                event.preventDefault();
                void handleDeleteConfirm();
              }}
            >
              {deleting ? "Deleting..." : "Delete book"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
