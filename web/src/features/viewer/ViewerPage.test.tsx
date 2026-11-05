import { useEffect } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ViewerPage } from "./ViewerPage";

/**
 * react-pdf's `Document`/`Page` are mocked so this
 * test drives the ?page=N clamp/error/403 logic without a real PDF
 * download or pdf.js worker - matches SearchPage.test.tsx's
 * vi.mock(react-router-dom's useNavigate) convention, plus a hoisted
 * mutable mock-state object (`vi.hoisted`) so each test can control
 * whether the mocked `Document` calls `onLoadSuccess`/`onLoadError`.
 */

const { mockDocState } = vi.hoisted(() => ({
  mockDocState: { numPages: 10, loadError: null as (Error & { status?: number }) | null },
}));

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} as { workerSrc?: string } },
  Document: ({
    children,
    onLoadSuccess,
    onLoadError,
  }: {
    children?: React.ReactNode;
    onLoadSuccess?: (doc: { numPages: number }) => void;
    onLoadError?: (error: Error) => void;
  }) => {
    useEffect(() => {
      if (mockDocState.loadError) {
        onLoadError?.(mockDocState.loadError);
      } else {
        onLoadSuccess?.({ numPages: mockDocState.numPages });
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps -- fire once per mount, mirroring a real load
    }, []);
    return <div data-testid="mock-document">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid="mock-page">{pageNumber}</div>
  ),
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function renderViewer(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/courses/:id/books/:bookId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ViewerPage", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockDocState.numPages = 10;
    mockDocState.loadError = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the requested ?page=N", async () => {
    renderViewer("/courses/course-1/books/book-1?page=3");

    const page = await screen.findByTestId("mock-page");
    expect(page.textContent).toBe("3");
  });

  it("clamps an out-of-range ?page=N to numPages after load", async () => {
    mockDocState.numPages = 10;
    renderViewer("/courses/course-1/books/book-1?page=999");

    const page = await screen.findByTestId("mock-page");
    expect(page.textContent).toBe("10");
  });

  it("Prev navigates from the clamped page after an out-of-range ?page=N deep link", async () => {
    mockDocState.numPages = 10;
    renderViewer("/courses/course-1/books/book-1?page=999");

    const page = await screen.findByTestId("mock-page");
    expect(page.textContent).toBe("10");

    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));

    expect((await screen.findByTestId("mock-page")).textContent).toBe("9");
  });

  it("defaults to page 1 when ?page is missing or invalid", async () => {
    renderViewer("/courses/course-1/books/book-1?page=not-a-number");

    const page = await screen.findByTestId("mock-page");
    expect(page.textContent).toBe("1");
  });

  it("shows the destructive load-error alert on a non-403 load failure", async () => {
    mockDocState.loadError = Object.assign(new Error("boom"), { status: 500 });
    renderViewer("/courses/course-1/books/book-1?page=1");

    expect(
      await screen.findByText("Could not load this PDF. Try again."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("redirects to / on a 403 load failure without rendering an error alert", async () => {
    mockDocState.loadError = Object.assign(new Error("forbidden"), { status: 403 });
    renderViewer("/courses/course-1/books/book-1?page=1");

    await vi.waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/", { replace: true });
    });
    expect(
      screen.queryByText("Could not load this PDF. Try again."),
    ).toBeNull();
  });
});
