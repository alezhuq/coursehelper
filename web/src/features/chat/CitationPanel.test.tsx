import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CitationBadge } from "./CitationBadge";
import { CitationPanel } from "./CitationPanel";
import type { CitationSpan } from "./useChatStream";

/**
 * CHAT-01: mirrors ViewerPage.test.tsx's react-pdf mocking convention (a
 * hoisted mutable mock-state object) so this test drives the panel's
 * loading/populated/error states without a real PDF download or pdf.js
 * worker. Unlike ViewerPage.test.tsx's mock, this one also honors the
 * `loading` render prop and a "pending" mode, since the panel's Loading
 * PDF… state (unlike ViewerPage's, which is never explicitly asserted
 * upstream) is one of this test's required states.
 */

const { mockDocState } = vi.hoisted(() => ({
  mockDocState: {
    mode: "success" as "success" | "error" | "pending",
    loadError: null as Error | null,
  },
}));

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} as { workerSrc?: string } },
  Document: ({
    children,
    loading,
    onLoadSuccess,
    onLoadError,
  }: {
    children?: React.ReactNode;
    loading?: React.ReactNode;
    onLoadSuccess?: (doc: { numPages: number }) => void;
    onLoadError?: (error: Error) => void;
  }) => {
    useEffect(() => {
      if (mockDocState.mode === "pending") return;
      if (mockDocState.mode === "error") {
        onLoadError?.(mockDocState.loadError ?? new Error("boom"));
      } else {
        onLoadSuccess?.({ numPages: 10 });
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps -- fire once per mount, mirroring a real load
    }, []);
    if (mockDocState.mode === "pending") {
      return <div data-testid="mock-document">{loading}</div>;
    }
    return <div data-testid="mock-document">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid="mock-page">{pageNumber}</div>
  ),
}));

describe("CitationPanel", () => {
  beforeEach(() => {
    mockDocState.mode = "success";
    mockDocState.loadError = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the Loading PDF… state before the page resolves", () => {
    mockDocState.mode = "pending";

    render(
      <CitationPanel
        courseId="course-1"
        bookId="book-1"
        filename="intro.pdf"
        page={4}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Loading PDF…")).toBeTruthy();
    expect(screen.queryByTestId("mock-page")).toBeNull();
  });

  it("shows the populated header (filename + page label) and Open full viewer link once loaded", async () => {
    render(
      <CitationPanel
        courseId="course-1"
        bookId="book-1"
        filename="intro.pdf"
        page={4}
        onClose={vi.fn()}
      />,
    );

    expect((await screen.findByTestId("mock-page")).textContent).toBe("4");
    expect(screen.getByText("intro.pdf")).toBeTruthy();
    expect(screen.getByText("p. 4")).toBeTruthy();

    const openFullViewer = screen.getByRole("link", { name: /Open full viewer/ });
    expect(openFullViewer.getAttribute("href")).toBe(
      "/courses/course-1/books/book-1?page=4",
    );
    expect(openFullViewer.getAttribute("target")).toBe("_blank");
  });

  it("shows the destructive error state with a Try again re-fetch and never redirects on 403", async () => {
    mockDocState.mode = "error";
    mockDocState.loadError = Object.assign(new Error("forbidden"), {
      status: 403,
    });

    render(
      <CitationPanel
        courseId="course-1"
        bookId="book-1"
        filename="intro.pdf"
        page={4}
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("Could not load this PDF. Try again."),
    ).toBeTruthy();
    const tryAgain = screen.getByRole("button", { name: "Try again" });
    expect(tryAgain).toBeTruthy();

    mockDocState.mode = "success";
    fireEvent.click(tryAgain);

    expect((await screen.findByTestId("mock-page")).textContent).toBe("4");
  });

  it("invokes the close handler when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <CitationPanel
        courseId="course-1"
        bookId="book-1"
        filename="intro.pdf"
        page={4}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Close source panel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("CitationBadge", () => {
  afterEach(() => {
    cleanup();
  });

  const citation: CitationSpan = {
    marker: 2,
    book_id: "book-42",
    original_filename: "calculus.pdf",
    page_start: 17,
    page_end: 18,
    section_path: ["Chapter 3"],
  };

  it("renders as a focusable button with the exact aria-label and calls onOpen with the span's book_id + page_start", () => {
    const onOpen = vi.fn();
    render(<CitationBadge citation={citation} onOpen={onOpen} />);

    const badge = screen.getByRole("button", {
      name: "Open citation 2: calculus.pdf, page 17",
    });
    expect(badge.textContent).toBe("[2]");

    fireEvent.click(badge);
    expect(onOpen).toHaveBeenCalledWith("book-42", 17, "calculus.pdf");
  });
});
