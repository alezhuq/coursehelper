/**
 * Consumes the POST-based chat SSE stream
 * (`POST /courses/:id/chat/conversations/:conversationId/messages`) via
 * fetch's streaming `ReadableStream` reader. The browser's native
 * `EventSource` is GET-only and cannot issue the mutating POST this
 * endpoint requires, so this hook reads `response.body!.getReader()`
 * directly and parses `event:`/`data:` frames by hand.
 *
 * Wire-format note: `fastapi.sse.EventSourceResponse` JSON-serializes
 * EVERY `data` value, including plain strings (`data="hi"` becomes the
 * wire bytes `data: "hi"`, quotes included) - so every dispatched frame's
 * joined `data:` lines must be `JSON.parse`d before use, not treated as
 * raw text. The `done` event carries no `data:` field at all.
 *
 * Contract: request-level failures (a non-2xx response, or `fetch` itself
 * rejecting) THROW - a non-2xx throws an `ApiError` so callers can branch on
 * `status` (401 -> /login, 403 -> /). Once the response body is being read,
 * every outcome ends in exactly one of `onDone` or `onError` and
 * `sendMessage` resolves: an explicit `done`/`error` event, a stream that
 * closes without either, a missing body, and a `read()` rejection all
 * terminate the turn, so the chat composer can never stay locked.
 */

import { ApiError } from "../../api/client";

const CSRF_HEADER_NAME = "X-Requested-With";
const CSRF_HEADER_VALUE = "fetch";

export interface CitationSpan {
  marker: number;
  book_id: string;
  original_filename: string;
  page_start: number;
  page_end: number;
  section_path: string[];
}

export interface ChatStreamCallbacks {
  onToken: (delta: string) => void;
  onCitations: (citations: CitationSpan[]) => void;
  onDone: () => void;
  onError: () => void;
}

function parseSseData(raw: string): unknown {
  if (raw === "") return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    // Tolerate a non-JSON payload rather than throwing mid-stream - treat
    // it as raw text.
    return raw;
  }
}

export function useChatStream() {
  async function sendMessage(
    courseId: string,
    conversationId: string,
    content: string,
    callbacks: ChatStreamCallbacks,
  ): Promise<void> {
    const response = await fetch(
      `/api/courses/${courseId}/chat/conversations/${conversationId}/messages`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE,
        },
        body: JSON.stringify({ content }),
      },
    );

    if (!response.ok) {
      throw new ApiError(
        response.status,
        `Chat request failed with status ${response.status}`,
      );
    }
    if (!response.body) {
      callbacks.onError();
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName = "message";
    let dataLines: string[] = [];
    // Set once `done` or `error` has been dispatched; the finally block
    // below calls onError exactly once when the body ends without either.
    let terminated = false;

    function dispatch() {
      const raw = dataLines.join("\n");
      const name = eventName;
      dataLines = [];
      eventName = "message";

      if (name === "done") {
        terminated = true;
        callbacks.onDone();
        return;
      }
      if (name === "error") {
        terminated = true;
        callbacks.onError();
        return;
      }

      const parsed = parseSseData(raw);
      if (name === "token") {
        callbacks.onToken(typeof parsed === "string" ? parsed : "");
      } else if (name === "citations") {
        callbacks.onCitations(Array.isArray(parsed) ? (parsed as CitationSpan[]) : []);
      }
    }

    function processBuffer() {
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex !== -1) {
        const rawLine = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;

        if (line === "") {
          dispatch();
        } else if (line.startsWith(":")) {
          // Comment / keep-alive ping line - ignored by design (SSE spec).
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).replace(/^ /, "");
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).replace(/^ /, ""));
        }

        newlineIndex = buffer.indexOf("\n");
      }
    }

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (value) {
          buffer += decoder.decode(value, { stream: true });
          processBuffer();
        }
        if (done) {
          buffer += decoder.decode();
          processBuffer();
          if (dataLines.length > 0 || eventName !== "message") dispatch();
          break;
        }
      }
    } catch {
      // A read() rejection (network drop mid-stream) falls through to the
      // finally block, which ends the turn via onError.
    } finally {
      if (!terminated) {
        callbacks.onError();
      }
    }
  }

  return { sendMessage };
}
