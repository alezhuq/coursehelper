import { useCallback, useRef, useState } from "react";

import { ApiError } from "../../api/client";

/**
 * `useFileUpload`: PDF upload with progress and cancel.
 *
 * `apiFetch` (web/src/api/client.ts) is `fetch`-based, and `fetch` has no
 * way to report upload progress events - that's why this hook uses raw
 * `XMLHttpRequest` instead. It manually replicates `apiFetch`'s CSRF header
 * (`X-Requested-With: fetch`) and `credentials: include` (`withCredentials`)
 * behavior so the upload passes the same same-origin CSRF guard
 * (`app/auth/csrf.py`) as every other mutating request.
 */

const CSRF_HEADER_NAME = "X-Requested-With";
const CSRF_HEADER_VALUE = "fetch";

export type UploadStatus = "idle" | "uploading" | "error";

export type BookStatus = "queued" | "processing" | "ready" | "failed";

export interface Book {
  id: string;
  course_id: string;
  uploaded_by: string;
  original_filename: string;
  size_bytes: number;
  status: BookStatus;
  status_error: string | null;
  created_at: string;
}

/**
 * Maps an XHR's HTTP status to the exact user-facing copy string.
 * The backend's own `{detail}` message already matches
 * these strings for the known validation branches; mapping by status here
 * keeps the frontend correct even if the backend wording ever drifts, and
 * gives every unmapped/network failure the same generic fallback.
 */
function mapErrorMessage(status: number): string {
  switch (status) {
    case 422:
      return "This file is not a PDF. Only PDF files can be uploaded.";
    case 413:
      return "This file is larger than the 300 MB limit. Compress or split it and try again.";
    case 409:
      return "This PDF has already been uploaded to this course.";
    default:
      return "The upload failed. Check your connection and try again.";
  }
}

export interface UseFileUploadResult {
  /**
   * Uploads `file` to `/api/courses/{courseId}/books`. Resolves with the
   * parsed `Book` on a 2xx response. Resolves with `null` if the upload was
   * cancelled (via `cancel()`) or ignored because another upload is already
   * in flight (single-flight enforcement) - neither is an error condition.
   * Rejects with an `ApiError` (carrying the XHR status and the mapped
   * user-facing copy) on a non-2xx response or network failure,
   * mirroring how `apiFetch` consumers already branch on `ApiError.status`.
   */
  upload: (courseId: string, file: File) => Promise<Book | null>;
  progress: number;
  status: UploadStatus;
  error: string | null;
  cancel: () => void;
}

export function useFileUpload(): UseFileUploadResult {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);

  const cancel = useCallback(() => {
    xhrRef.current?.abort();
  }, []);

  const upload = useCallback(
    (courseId: string, file: File): Promise<Book | null> => {
      // Single-flight: a second upload() call while one is already running
      // is ignored outright rather than queued or erroring.
      if (xhrRef.current) {
        return Promise.resolve(null);
      }

      setStatus("uploading");
      setProgress(0);
      setError(null);

      return new Promise<Book | null>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhrRef.current = xhr;

        const formData = new FormData();
        formData.append("file", file);

        xhr.open("POST", `/api/courses/${courseId}/books`);
        xhr.withCredentials = true;
        xhr.setRequestHeader(CSRF_HEADER_NAME, CSRF_HEADER_VALUE);

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setProgress(Math.round((event.loaded / event.total) * 100));
          }
        };

        xhr.onload = () => {
          xhrRef.current = null;
          if (xhr.status >= 200 && xhr.status < 300) {
            setStatus("idle");
            setProgress(100);
            try {
              resolve(JSON.parse(xhr.responseText) as Book);
            } catch {
              const message = mapErrorMessage(xhr.status);
              setStatus("error");
              setError(message);
              reject(new ApiError(xhr.status, message));
            }
            return;
          }
          const message = mapErrorMessage(xhr.status);
          setStatus("error");
          setError(message);
          reject(new ApiError(xhr.status, message));
        };

        xhr.onerror = () => {
          xhrRef.current = null;
          const message = mapErrorMessage(0);
          setStatus("error");
          setError(message);
          reject(new ApiError(0, message));
        };

        // Cancellation is not an error: the dropzone simply resets to idle
        // with no alert.
        xhr.onabort = () => {
          xhrRef.current = null;
          setStatus("idle");
          setProgress(0);
          resolve(null);
        };

        xhr.send(formData);
      });
    },
    [],
  );

  return { upload, progress, status, error, cancel };
}
