import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ChatPage } from "./ChatPage";
import type { ChatMessageData } from "./ChatMessage";

/**
 * Mirrors SearchPage.test.tsx's vi.mock(apiFetch)/
 * MemoryRouter convention for the two plain GET fetches (course header,
 * conversation history), and additionally stubs the global `fetch` used
 * directly by useChatStream.ts (the POST-SSE reader deliberately bypasses
 * apiFetch to read `response.body` as a stream rather than `.json()`).
 *
 * The SSE wire format is built with a controlled `ReadableStream` so
 * individual frames can be pushed one at a time across awaited assertions
 * (Thinking… -> tokens append -> cursor removed on done), mirroring
 * `fastapi.sse.EventSourceResponse`'s real wire behavior: every `data`
 * value is JSON-serialized, including plain strings.
 *
 * react-pdf mock: ChatPage transitively imports `CitationPanel`, which
 * imports `react-pdf` - importing the real module in jsdom throws
 * `DOMMatrix is not defined` at import time (no real canvas/DOMMatrix in
 * jsdom). Mocked here exactly like `ViewerPage.test.tsx` already does;
 * this file doesn't exercise the panel's internals (that's
 * `CitationPanel.test.tsx`'s job), so the mock only needs to prevent the
 * import crash.
 */

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} as { workerSrc?: string } },
  Document: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="mock-document">{children}</div>
  ),
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid="mock-page">{pageNumber}</div>
  ),
}));

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
  code: "CMPT 120",
  title: "Intro to Programming",
  term: "Fall 2026",
  created_at: "2026-01-01T00:00:00Z",
};

function jsonResponse(body: unknown) {
  return { json: () => Promise.resolve(body) } as Response;
}

function setupCourseAndHistory(messages: ChatMessageData[], conversationId = "conv-1") {
  mockApiFetch.mockImplementation((path: string) => {
    if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
    if (path === `/courses/${COURSE.id}/chat/conversation`) {
      return Promise.resolve(
        jsonResponse({ conversation_id: conversationId, messages }),
      );
    }
    return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
  });
}

/** A manually-driven SSE stream so the test can push frames one at a time
 * and observe intermediate render states, instead of enqueueing everything
 * synchronously before the reader ever starts pulling. */
function makeControlledSseStream() {
  let controllerRef!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller;
    },
  });
  const encoder = new TextEncoder();
  return {
    stream,
    push(event: string, data?: unknown) {
      let text = `event: ${event}\n`;
      if (data !== undefined) text += `data: ${JSON.stringify(data)}\n`;
      text += "\n";
      controllerRef.enqueue(encoder.encode(text));
    },
    close() {
      controllerRef.close();
    },
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/courses/${COURSE.id}/chat`]}>
      <Routes>
        <Route path="/courses/:id/chat" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ChatPage", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockApiFetch.mockReset();
    mockNavigate.mockReset();
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the neutral empty prompt before the first message", async () => {
    setupCourseAndHistory([]);
    renderPage();

    await screen.findByText("Ask a question about this course's materials.");
  });

  it("renders prior conversation history on load, alternating user/assistant rows, with the [1] marker upgraded to a clickable citation badge", async () => {
    setupCourseAndHistory([
      { role: "user", content: "What is a variable?", citations: [] },
      {
        role: "assistant",
        content: "A variable stores a value that can change. [1]",
        citations: [
          {
            marker: 1,
            book_id: "book-1",
            original_filename: "intro.pdf",
            page_start: 4,
            page_end: 4,
            section_path: ["Chapter 1"],
          },
        ],
      },
    ]);
    renderPage();

    await screen.findByText("What is a variable?");
    expect(
      screen.getByText("A variable stores a value that can change.", {
        exact: false,
      }),
    ).toBeTruthy();

    const badge = screen.getByRole("button", {
      name: "Open citation 1: intro.pdf, page 4",
    });
    expect(badge.textContent).toBe("[1]");

    fireEvent.click(badge);
    expect(
      await screen.findByText("intro.pdf"),
    ).toBeTruthy();
    expect(screen.getByText("p. 4")).toBeTruthy();
  });

  it("shows Loading conversation… while the initial history fetch is in flight", async () => {
    let resolveConversation!: (value: Response) => void;
    const pendingConversation = new Promise<Response>((resolve) => {
      resolveConversation = resolve;
    });
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
      if (path === `/courses/${COURSE.id}/chat/conversation`) {
        return pendingConversation;
      }
      return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
    });
    renderPage();

    await screen.findByText("Loading conversation…");

    resolveConversation(jsonResponse({ conversation_id: "conv-1", messages: [] }));
    await screen.findByText("Ask a question about this course's materials.");
  });

  it("streams Thinking… -> tokens appended -> cursor removed on done, then persists the final answer", async () => {
    setupCourseAndHistory([]);
    const sse = makeControlledSseStream();
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/messages")) {
        return Promise.resolve({ ok: true, body: sse.stream } as unknown as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
    });

    renderPage();
    await screen.findByPlaceholderText("Ask a question about this course's materials");

    const textarea = screen.getByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, { target: { value: "What is a loop?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Thinking…");
    expect(screen.getByRole("button", { name: "Sending..." })).toBeTruthy();

    sse.push("token", "A loop ");
    await screen.findByText(/A loop/);
    expect(document.querySelector(".animate-pulse")).toBeTruthy();

    sse.push("token", "repeats code.");
    sse.push("citations", []);
    sse.push("done");
    sse.close();

    await waitFor(() => expect(screen.queryByText("Thinking…")).toBeNull());
    await screen.findByText("A loop repeats code.");
    expect(document.querySelector(".animate-pulse")).toBeNull();
    await screen.findByRole("button", { name: "Send" });
  });

  it("shows a half-arrived $$ expression as literal source mid-stream, then snaps to typeset math once the closing delimiter arrives - no visible rewrite on done", async () => {
    setupCourseAndHistory([]);
    const sse = makeControlledSseStream();
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/messages")) {
        return Promise.resolve({ ok: true, body: sse.stream } as unknown as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
    });

    renderPage();
    await screen.findByPlaceholderText("Ask a question about this course's materials");

    const textarea = screen.getByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, {
      target: { value: "State the quadratic formula" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Thinking…");

    // Frame ends mid-delimiter: the streaming row must show literal source,
    // never throw, and never blank the row.
    sse.push("token", "The formula is $$\\frac{a}{");
    await screen.findByText("The formula is $$\\frac{a}{");
    expect(document.querySelector(".katex")).toBeNull();

    // Completing frame arrives - the same still-streaming row snaps to
    // typeset math while the turn is still in flight (cursor visible).
    sse.push("token", "b}$$");
    await waitFor(() =>
      expect(document.querySelectorAll(".katex-display")).toHaveLength(1),
    );
    expect(document.querySelector(".animate-pulse")).toBeTruthy();

    sse.push("citations", []);
    sse.push("done");
    sse.close();

    // On done, the persisted row (same renderer) keeps showing exactly one
    // typeset node - nothing visibly rewrites when the turn finishes.
    await waitFor(() => expect(screen.queryByText("Thinking…")).toBeNull());
    expect(document.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(document.querySelector(".animate-pulse")).toBeNull();
  });

  it("shows an inline turn-error Alert after a failed turn, preserves the user message, and resends on Try again", async () => {
    setupCourseAndHistory([]);
    const failingStream = makeControlledSseStream();
    let callCount = 0;
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/messages")) {
        callCount += 1;
        if (callCount === 1) {
          return Promise.resolve({
            ok: true,
            body: failingStream.stream,
          } as unknown as Response);
        }
        const successStream = makeControlledSseStream();
        successStream.push("token", "The answer.");
        successStream.push("citations", []);
        successStream.push("done");
        successStream.close();
        return Promise.resolve({
          ok: true,
          body: successStream.stream,
        } as unknown as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
    });

    renderPage();
    await screen.findByPlaceholderText("Ask a question about this course's materials");

    const textarea = screen.getByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, { target: { value: "What is recursion?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Thinking…");
    failingStream.push("error");
    failingStream.close();

    await screen.findByText("Could not get a response. Try again.");
    expect(screen.getByText("What is recursion?")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("The answer.");
    expect(screen.queryByText("Could not get a response. Try again.")).toBeNull();
  });

  it("a 401 from the chat POST redirects to /login and a 403 redirects to /", async () => {
    for (const [status, target] of [
      [401, "/login"],
      [403, "/"],
    ] as const) {
      mockNavigate.mockReset();
      mockFetch.mockReset();
      setupCourseAndHistory([]);
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/messages")) {
          return Promise.resolve({ ok: false, status, body: null } as unknown as Response);
        }
        return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
      });

      const { unmount } = renderPage();
      const textarea = await screen.findByPlaceholderText(
        "Ask a question about this course's materials",
      );
      fireEvent.change(textarea, { target: { value: "What is a loop?" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await waitFor(() =>
        expect(mockNavigate).toHaveBeenCalledWith(target, { replace: true }),
      );
      unmount();
    }
  });

  it("a stream that closes after a token without done surfaces the retryable error Alert and unlocks the composer", async () => {
    setupCourseAndHistory([]);
    const sse = makeControlledSseStream();
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/messages")) {
        return Promise.resolve({ ok: true, body: sse.stream } as unknown as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
    });

    renderPage();
    const textarea = await screen.findByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, { target: { value: "What is a loop?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Thinking…");
    sse.push("token", "A loop ");
    await screen.findByText(/A loop/);
    sse.close();

    await screen.findByText("Could not get a response. Try again.");
    expect(
      (screen.getByPlaceholderText(
        "Ask a question about this course's materials",
      ) as HTMLTextAreaElement).disabled,
    ).toBe(false);
  });

  it("re-submitting the identical text after a failed turn does not duplicate the user row", async () => {
    setupCourseAndHistory([]);
    const failingStream = makeControlledSseStream();
    let callCount = 0;
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/messages")) {
        callCount += 1;
        if (callCount === 1) {
          return Promise.resolve({
            ok: true,
            body: failingStream.stream,
          } as unknown as Response);
        }
        const successStream = makeControlledSseStream();
        successStream.push("token", "The answer.");
        successStream.push("citations", []);
        successStream.push("done");
        successStream.close();
        return Promise.resolve({
          ok: true,
          body: successStream.stream,
        } as unknown as Response);
      }
      return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
    });

    renderPage();
    const textarea = await screen.findByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, { target: { value: "What is recursion?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Thinking…");
    failingStream.push("error");
    failingStream.close();
    await screen.findByText("Could not get a response. Try again.");

    fireEvent.change(textarea, { target: { value: "What is recursion?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("The answer.");
    expect(screen.getAllByText("What is recursion?")).toHaveLength(1);
  });

  it("shows a whole-region destructive Alert on a conversation-load failure and recovers via Try again", async () => {
    let callCount = 0;
    mockApiFetch.mockImplementation((path: string) => {
      if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
      if (path === `/courses/${COURSE.id}/chat/conversation`) {
        callCount += 1;
        if (callCount === 1) return Promise.reject(new Error("network down"));
        return Promise.resolve(
          jsonResponse({ conversation_id: "conv-1", messages: [] }),
        );
      }
      return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
    });

    renderPage();

    await screen.findByText("Could not load this conversation. Try again.");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("Ask a question about this course's materials.");
  });

  it("renders the refusal message with calm assistant styling and no citation-badge row", async () => {
    setupCourseAndHistory([
      { role: "user", content: "What is quantum gravity?", citations: [] },
      {
        role: "assistant",
        content: "The course materials don't cover this question.",
        citations: [],
      },
    ]);
    renderPage();

    const refusal = await screen.findByText(
      "The course materials don't cover this question.",
    );
    expect(refusal.className).not.toMatch(/red|destructive/);
    expect(
      screen.queryByRole("button", { name: /Open citation/ }),
    ).toBeNull();
  });

  describe("Clear conversation", () => {
    it("shows an enabled Clear conversation control with a non-empty conversation", async () => {
      setupCourseAndHistory([
        { role: "user", content: "What is a variable?", citations: [] },
      ]);
      renderPage();

      const button = await screen.findByRole("button", {
        name: /Clear conversation/,
      });
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });

    it("opening the control shows a confirm dialog and issues no request until confirmed", async () => {
      setupCourseAndHistory([
        { role: "user", content: "What is a variable?", citations: [] },
      ]);
      renderPage();

      const button = await screen.findByRole("button", {
        name: /Clear conversation/,
      });
      const callsBefore = mockApiFetch.mock.calls.length;
      fireEvent.click(button);

      await screen.findByText("Clear this conversation?");
      expect(
        screen.getByText(
          "Every message in this thread will be permanently deleted. This cannot be undone.",
        ),
      ).toBeTruthy();
      expect(mockApiFetch.mock.calls.length).toBe(callsBefore);
    });

    it("cancelling the dialog issues no request and leaves every message in place", async () => {
      setupCourseAndHistory([
        { role: "user", content: "What is a variable?", citations: [] },
      ]);
      renderPage();

      const button = await screen.findByRole("button", {
        name: /Clear conversation/,
      });
      fireEvent.click(button);
      await screen.findByText("Clear this conversation?");
      const callsBefore = mockApiFetch.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(screen.queryByText("Clear this conversation?")).toBeNull(),
      );
      expect(mockApiFetch.mock.calls.length).toBe(callsBefore);
      expect(screen.getByText("What is a variable?")).toBeTruthy();
    });

    it("confirming issues exactly one DELETE to the conversation endpoint, empties the thread, and preserves the conversation id for the next send", async () => {
      setupCourseAndHistory(
        [{ role: "user", content: "What is a variable?", citations: [] }],
        "conv-99",
      );
      mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          (!init || init.method === undefined)
        ) {
          return Promise.resolve(
            jsonResponse({
              conversation_id: "conv-99",
              messages: [{ role: "user", content: "What is a variable?", citations: [] }],
            }),
          );
        }
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve({ json: () => Promise.resolve({}) } as Response);
        }
        return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
      });

      const sse = makeControlledSseStream();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/messages")) {
          expect(url).toContain("conv-99");
          return Promise.resolve({ ok: true, body: sse.stream } as unknown as Response);
        }
        return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
      });

      renderPage();
      await screen.findByText("What is a variable?");

      fireEvent.click(
        screen.getByRole("button", { name: /Clear conversation/ }),
      );
      await screen.findByText("Clear this conversation?");
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Clear conversation",
        }),
      );

      await waitFor(() =>
        expect(screen.queryByText("What is a variable?")).toBeNull(),
      );
      await screen.findByText("Ask a question about this course's materials.");

      const deleteCalls = mockApiFetch.mock.calls.filter(
        ([path, init]) =>
          path === `/courses/${COURSE.id}/chat/conversation` &&
          (init as RequestInit | undefined)?.method === "DELETE",
      );
      expect(deleteCalls).toHaveLength(1);

      // Composer enabled, and a new send still targets the SAME
      // conversation id used before the clear.
      const textarea = screen.getByPlaceholderText(
        "Ask a question about this course's materials",
      );
      expect((textarea as HTMLTextAreaElement).disabled).toBe(false);
      fireEvent.change(textarea, { target: { value: "A new question" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await screen.findByText("Thinking…");
    });

    it("closes an open citation panel on clear", async () => {
      setupCourseAndHistory([
        {
          role: "user",
          content: "What is a variable?",
          citations: [],
        },
        {
          role: "assistant",
          content: "A variable stores a value. [1]",
          citations: [
            {
              marker: 1,
              book_id: "book-1",
              original_filename: "intro.pdf",
              page_start: 4,
              page_end: 4,
              section_path: ["Chapter 1"],
            },
          ],
        },
      ]);
      mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve({ json: () => Promise.resolve({}) } as Response);
        }
        if (path === `/courses/${COURSE.id}/chat/conversation`) {
          return Promise.resolve(
            jsonResponse({
              conversation_id: "conv-1",
              messages: [
                { role: "user", content: "What is a variable?", citations: [] },
                {
                  role: "assistant",
                  content: "A variable stores a value. [1]",
                  citations: [
                    {
                      marker: 1,
                      book_id: "book-1",
                      original_filename: "intro.pdf",
                      page_start: 4,
                      page_end: 4,
                      section_path: ["Chapter 1"],
                    },
                  ],
                },
              ],
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
      });

      renderPage();
      const badge = await screen.findByRole("button", {
        name: "Open citation 1: intro.pdf, page 4",
      });
      fireEvent.click(badge);
      await screen.findByText("intro.pdf");

      fireEvent.click(
        screen.getByRole("button", { name: /Clear conversation/ }),
      );
      await screen.findByText("Clear this conversation?");
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Clear conversation",
        }),
      );

      await waitFor(() => expect(screen.queryByText("intro.pdf")).toBeNull());
      await screen.findByText("Ask a question about this course's materials.");
    });

    it("removes an error turn's retry affordance after a clear", async () => {
      setupCourseAndHistory([]);
      const failingStream = makeControlledSseStream();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/messages")) {
          return Promise.resolve({ ok: true, body: failingStream.stream } as unknown as Response);
        }
        return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
      });

      renderPage();
      await screen.findByPlaceholderText("Ask a question about this course's materials");

      const textarea = screen.getByPlaceholderText(
        "Ask a question about this course's materials",
      );
      fireEvent.change(textarea, { target: { value: "What is recursion?" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await screen.findByText("Thinking…");
      failingStream.push("error");
      failingStream.close();

      await screen.findByText("Could not get a response. Try again.");
      expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();

      mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve({ json: () => Promise.resolve({}) } as Response);
        }
        return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
      });

      fireEvent.click(
        screen.getByRole("button", { name: /Clear conversation/ }),
      );
      await screen.findByText("Clear this conversation?");
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Clear conversation",
        }),
      );

      await waitFor(() =>
        expect(
          screen.queryByText("Could not get a response. Try again."),
        ).toBeNull(),
      );
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
      await screen.findByText("Ask a question about this course's materials.");
    });

    it("disables the control when the conversation has zero messages and no turn is in flight", async () => {
      setupCourseAndHistory([]);
      renderPage();

      const button = await screen.findByRole("button", {
        name: /Clear conversation/,
      });
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });

    it("disables the control while a turn is thinking or streaming", async () => {
      setupCourseAndHistory([]);
      const sse = makeControlledSseStream();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/messages")) {
          return Promise.resolve({ ok: true, body: sse.stream } as unknown as Response);
        }
        return Promise.reject(new Error(`Unexpected fetch call: ${url}`));
      });

      renderPage();
      await screen.findByPlaceholderText("Ask a question about this course's materials");

      const textarea = screen.getByPlaceholderText(
        "Ask a question about this course's materials",
      );
      fireEvent.change(textarea, { target: { value: "What is a loop?" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await screen.findByText("Thinking…");
      const button = screen.getByRole("button", { name: /Clear conversation/ });
      expect((button as HTMLButtonElement).disabled).toBe(true);

      sse.push("token", "A loop repeats.");
      sse.push("citations", []);
      sse.push("done");
      sse.close();
    });

    it("a failed DELETE leaves every message rendered, closes the dialog, and surfaces the API's error message", async () => {
      setupCourseAndHistory([
        { role: "user", content: "What is a variable?", citations: [] },
      ]);
      const { ApiError } = await import("../../api/client");
      mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          init?.method === "DELETE"
        ) {
          return Promise.reject(new ApiError(500, "Something broke"));
        }
        if (path === `/courses/${COURSE.id}/chat/conversation`) {
          return Promise.resolve(
            jsonResponse({
              conversation_id: "conv-1",
              messages: [
                { role: "user", content: "What is a variable?", citations: [] },
              ],
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
      });

      renderPage();
      await screen.findByText("What is a variable?");

      fireEvent.click(
        screen.getByRole("button", { name: /Clear conversation/ }),
      );
      await screen.findByText("Clear this conversation?");
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Clear conversation",
        }),
      );

      await screen.findByText("Something broke");
      expect(screen.queryByText("Clear this conversation?")).toBeNull();
      expect(screen.getByText("What is a variable?")).toBeTruthy();
    });

    it("a 401 from the DELETE redirects to /login; a 403 redirects to /", async () => {
      const { ApiError } = await import("../../api/client");

      setupCourseAndHistory([
        { role: "user", content: "What is a variable?", citations: [] },
      ]);
      mockApiFetch.mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/courses") return Promise.resolve(jsonResponse([COURSE]));
        if (
          path === `/courses/${COURSE.id}/chat/conversation` &&
          init?.method === "DELETE"
        ) {
          return Promise.reject(new ApiError(401, "unauthorized"));
        }
        if (path === `/courses/${COURSE.id}/chat/conversation`) {
          return Promise.resolve(
            jsonResponse({
              conversation_id: "conv-1",
              messages: [
                { role: "user", content: "What is a variable?", citations: [] },
              ],
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected apiFetch call: ${path}`));
      });

      renderPage();
      await screen.findByText("What is a variable?");
      fireEvent.click(
        screen.getByRole("button", { name: /Clear conversation/ }),
      );
      await screen.findByText("Clear this conversation?");
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Clear conversation",
        }),
      );

      await waitFor(() =>
        expect(mockNavigate).toHaveBeenCalledWith("/login", { replace: true }),
      );
    });
  });

  it("disables Send while the trimmed textarea is empty", async () => {
    setupCourseAndHistory([]);
    renderPage();

    await screen.findByPlaceholderText("Ask a question about this course's materials");
    const button = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    const textarea = screen.getByPlaceholderText(
      "Ask a question about this course's materials",
    );
    fireEvent.change(textarea, { target: { value: "   " } });
    expect(button.disabled).toBe(true);

    fireEvent.change(textarea, { target: { value: "hello" } });
    expect(button.disabled).toBe(false);
  });
});
