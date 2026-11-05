import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { Book } from "../uploads/useFileUpload";
import { CourseDetailPage } from "./CourseDetailPage";

/**
 * Guards the ingestion-status interaction contract:
 * (1) the processing-wait caption (honest 20-45 min wording, revised from
 * the original "several minutes" copy after a load test) renders
 * only while a book is processing, and (2) a failed book's status_error
 * renders in full,
 * wrapped (break-words), never truncated.
 *
 * apiFetch and useFileUpload are mocked so the component renders from a
 * controlled book list with no real network - CourseDetailPage.tsx's own
 * loadAll() fires three apiFetch calls in Promise.all (/auth/me, /courses,
 * /courses/:id/books); the mock below answers each by path.
 */

vi.mock("../../api/client", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

vi.mock("../uploads/useFileUpload", async () => {
  const actual = await vi.importActual<
    typeof import("../uploads/useFileUpload")
  >("../uploads/useFileUpload");
  return {
    ...actual,
    useFileUpload: () => ({
      upload: vi.fn(),
      progress: 0,
      status: "idle" as const,
      error: null,
      cancel: vi.fn(),
    }),
  };
});

const { apiFetch } = await import("../../api/client");
const mockApiFetch = vi.mocked(apiFetch);

const USER = {
  id: "user-1",
  email: "prof@sfu.ca",
  display_name: "Prof Test",
  role: "professor",
  auth_method: "cas",
};

const COURSE = {
  id: "course-1",
  owner_id: "user-1",
  code: "CMPT 120",
  title: "Intro to Computing Science",
  term: "Fall 2026",
  created_at: "2026-01-01T00:00:00Z",
};

function makeBook(overrides: Partial<Book>): Book {
  return {
    id: "book-default",
    course_id: COURSE.id,
    uploaded_by: USER.id,
    original_filename: "textbook.pdf",
    size_bytes: 1024,
    status: "ready",
    status_error: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown) {
  return { json: () => Promise.resolve(body) } as Response;
}

function setupFetch(books: Book[]) {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/auth/me") return Promise.resolve(jsonResponse(USER));
    if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
    if (path === `/courses/${COURSE.id}/books`) {
      return Promise.resolve(jsonResponse(books));
    }
    return Promise.reject(new Error(`Unexpected apiFetch path: ${path}`));
  });
}

function renderPage(books: Book[]) {
  setupFetch(books);
  return render(
    <MemoryRouter initialEntries={[`/courses/${COURSE.id}`]}>
      <Routes>
        <Route path="/courses/:id" element={<CourseDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CourseDetailPage status cell (ingestion-status contract)", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the processing caption only for the processing row, not queued/ready/failed", async () => {
    const books = [
      makeBook({ id: "b-queued", status: "queued", original_filename: "queued.pdf" }),
      makeBook({
        id: "b-processing",
        status: "processing",
        original_filename: "processing.pdf",
      }),
      makeBook({ id: "b-ready", status: "ready", original_filename: "ready.pdf" }),
      makeBook({
        id: "b-failed",
        status: "failed",
        status_error: "[parse] short failure",
        original_filename: "failed.pdf",
      }),
    ];

    renderPage(books);

    // Wait for the real page render (past the "Loading..." state) before
    // asserting on caption presence/absence.
    await screen.findByText("processing.pdf");

    const captions = screen.queryAllByText(
      "This can take 20-45 minutes for large textbooks. You can leave this " +
        "page and come back - the status updates automatically.",
    );
    expect(captions).toHaveLength(1);
  });

  it("renders a long failed status_error verbatim, prefixed and wrapped, never truncated", async () => {
    const longError =
      "[embed] " +
      "chunk embedding request failed repeatedly for page 42: ".repeat(10) +
      "giving up after 3 retries.";

    const books = [
      makeBook({
        id: "b-failed",
        status: "failed",
        status_error: longError,
        original_filename: "big-textbook.pdf",
      }),
    ];

    renderPage(books);

    await screen.findByText("big-textbook.pdf");

    const errorNode = screen.getByText(
      (content, element) =>
        element?.tagName === "P" && content.startsWith("Error: [embed]"),
    );

    expect(errorNode.textContent).toBe(`Error: ${longError}`);
    expect(errorNode.className).toContain("break-words");
  });
});
