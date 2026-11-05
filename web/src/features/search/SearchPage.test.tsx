import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SearchPage } from "./SearchPage";
import type { SearchResult } from "./SearchResultCard";

/**
 * An enrolled student submits a query and sees a real
 * result card (book, section path, page range, highlighted snippet) that
 * navigates to the viewer route on click, plus the full results-region
 * state machine: idle, loading
 * (clearing stale results), populated (with the result-count line),
 * no-results (echoing the query), and error (preserving the typed query,
 * offering a Try again retry). apiFetch and useNavigate are mocked so the
 * component renders/navigates from a controlled response with no real
 * network - matches CourseDetailPage.test.tsx's vi.mock convention.
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

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const { apiFetch } = await import("../../api/client");
const mockApiFetch = vi.mocked(apiFetch);

const COURSE = {
  id: "course-1",
  owner_id: "user-1",
  code: "BISC 101",
  title: "Introductory Biology",
  term: "Fall 2026",
  created_at: "2026-01-01T00:00:00Z",
};

const RESULT: SearchResult = {
  book_id: "book-1",
  original_filename: "textbook.pdf",
  section_path: ["Chapter 1", "Photosynthesis"],
  page_start: 12,
  page_end: 13,
  snippet: "leaves absorb \x01light\x02 for energy",
};

function makeResults(n: number): SearchResult[] {
  return Array.from({ length: n }, (_, index) => ({
    ...RESULT,
    book_id: `book-${index}`,
  }));
}

function jsonResponse(body: unknown) {
  return { json: () => Promise.resolve(body) } as Response;
}

function setupFetch(results: SearchResult[]) {
  mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
    if (path === `/courses/${COURSE.id}/search` && init?.method === "POST") {
      return Promise.resolve(jsonResponse(results));
    }
    return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
  });
}

function renderPage(results: SearchResult[]) {
  setupFetch(results);
  return render(
    <MemoryRouter initialEntries={[`/courses/${COURSE.id}/search`]}>
      <Routes>
        <Route path="/courses/:id/search" element={<SearchPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submitQuery(value: string) {
  await screen.findByPlaceholderText("Search this course's materials");
  const input = screen.getByPlaceholderText(
    "Search this course's materials",
  );
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("SearchPage", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockNavigate.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("submits a query and renders the result card's filename, section path, page label, and snippet", async () => {
    renderPage([RESULT]);
    await submitQuery("photosynthesis");

    await screen.findByText("textbook.pdf");

    expect(screen.getByText("Chapter 1 › Photosynthesis")).toBeTruthy();
    expect(screen.getByText("p. 12–13")).toBeTruthy();
    expect(screen.getByText("light")).toBeTruthy();
    expect(screen.getByText("light").tagName).toBe("MARK");
  });

  it("navigates to the viewer route with book_id + page_start on card click", async () => {
    renderPage([RESULT]);
    await submitQuery("photosynthesis");

    const card = await screen.findByText("textbook.pdf");
    fireEvent.click(card);

    expect(mockNavigate).toHaveBeenCalledWith(
      `/courses/${COURSE.id}/books/${RESULT.book_id}?page=${RESULT.page_start}`,
    );
  });

  it("disables the Search button while the trimmed query is empty and while a search is in flight", async () => {
    renderPage([RESULT]);

    await screen.findByPlaceholderText("Search this course's materials");
    const button = screen.getByRole("button", {
      name: "Search",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    const input = screen.getByPlaceholderText(
      "Search this course's materials",
    );
    fireEvent.change(input, { target: { value: "   " } });
    expect(button.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "photosynthesis" } });
    expect(button.disabled).toBe(false);

    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "Searching..." })).toBeTruthy();

    await screen.findByRole("button", { name: "Search" });
  });

  it("shows the idle prompt before any search is submitted this page load", async () => {
    renderPage([]);
    await screen.findByPlaceholderText("Search this course's materials");

    expect(
      screen.getByText(
        "Type a question or keyword above and press Search.",
      ),
    ).toBeTruthy();
  });

  it("shows Searching… while a request is in flight and clears the previous result set", async () => {
    let resolveSecond!: (value: Response) => void;
    const secondResponse = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    let searchCallCount = 0;
    mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
      if (path === `/courses/${COURSE.id}/search` && init?.method === "POST") {
        searchCallCount += 1;
        if (searchCallCount === 1) {
          return Promise.resolve(jsonResponse([RESULT]));
        }
        return secondResponse;
      }
      return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
    });

    render(
      <MemoryRouter initialEntries={[`/courses/${COURSE.id}/search`]}>
        <Routes>
          <Route path="/courses/:id/search" element={<SearchPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await submitQuery("photosynthesis");
    await screen.findByText("textbook.pdf");

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(screen.getByText("Searching…")).toBeTruthy();
    expect(screen.queryByText("textbook.pdf")).toBeNull();

    resolveSecond(jsonResponse([RESULT]));
    await screen.findByText("textbook.pdf");
  });

  it.each([
    [1, "1 result"],
    [5, "5 results"],
    [20, "Showing the top 20 matches"],
  ])(
    "shows the correct result-count line for n=%i",
    async (n, expected) => {
      renderPage(makeResults(n));
      await submitQuery("photosynthesis");
      await screen.findByText(expected);
    },
  );

  it("renders no page-navigation control when the top-20 cap is hit", async () => {
    renderPage(makeResults(20));
    await submitQuery("photosynthesis");
    await screen.findByText("Showing the top 20 matches");

    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByRole("button", { name: /next/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /previous/i })).toBeNull();
  });

  it("shows the grounded no-results message echoing the submitted query", async () => {
    renderPage([]);
    await submitQuery("nonsense query");

    await screen.findByText("No matches in this course's materials");
    expect(
      screen.getByText("No results for “nonsense query”."),
    ).toBeTruthy();
  });

  it("shows a destructive error with Try again, preserves the typed query, and recovers on retry", async () => {
    let searchCallCount = 0;
    mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
      if (path === `/courses/${COURSE.id}/search` && init?.method === "POST") {
        searchCallCount += 1;
        if (searchCallCount === 1) {
          return Promise.reject(new Error("network down"));
        }
        return Promise.resolve(jsonResponse([RESULT]));
      }
      return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
    });

    render(
      <MemoryRouter initialEntries={[`/courses/${COURSE.id}/search`]}>
        <Routes>
          <Route path="/courses/:id/search" element={<SearchPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByPlaceholderText("Search this course's materials");
    const input = screen.getByPlaceholderText(
      "Search this course's materials",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "photosynthesis" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await screen.findByText("Could not load search results. Try again.");
    expect(input.value).toBe("photosynthesis");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("textbook.pdf");
  });

  it("omits the section-path line for a result with an empty section_path while filename and page label still render", async () => {
    const resultNoSection: SearchResult = {
      ...RESULT,
      book_id: "book-2",
      section_path: [],
    };
    renderPage([resultNoSection]);
    await submitQuery("photosynthesis");

    await screen.findByText("textbook.pdf");
    expect(screen.queryByText("Chapter 1 › Photosynthesis")).toBeNull();
    expect(screen.getByText("p. 12–13")).toBeTruthy();
  });
});
