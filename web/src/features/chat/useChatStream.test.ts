import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { ApiError } from "../../api/client";
import { type ChatStreamCallbacks, useChatStream } from "./useChatStream";

/**
 * Contract under test (useChatStream.ts header): request-level failures
 * (non-2xx, fetch rejection) throw an `ApiError`; once the response body is
 * being read, every outcome ends in EXACTLY ONE of `onDone` / `onError` and
 * `sendMessage` resolves - so the chat composer can never stay locked.
 */

const encoder = new TextEncoder();

function frame(event: string, data?: unknown): Uint8Array {
  let text = `event: ${event}\n`;
  if (data !== undefined) text += `data: ${JSON.stringify(data)}\n`;
  return encoder.encode(`${text}\n`);
}

function streamOf(...chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

function okResponse(body: unknown): Response {
  return { ok: true, status: 200, body } as unknown as Response;
}

function makeCallbacks() {
  const callbacks = {
    onToken: vi.fn(),
    onCitations: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  } satisfies ChatStreamCallbacks;
  return callbacks;
}

describe("useChatStream terminal-event guarantee", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("token + citations + done calls onDone once and never onError", async () => {
    mockFetch.mockResolvedValue(
      okResponse(streamOf(frame("token", "Hi"), frame("citations", []), frame("done"))),
    );
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    await result.current.sendMessage("c1", "conv1", "hello", callbacks);

    expect(callbacks.onToken).toHaveBeenCalledWith("Hi");
    expect(callbacks.onDone).toHaveBeenCalledTimes(1);
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("a close with no terminal event calls onError once and never onDone", async () => {
    mockFetch.mockResolvedValue(okResponse(streamOf(frame("token", "partial"))));
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    await result.current.sendMessage("c1", "conv1", "hello", callbacks);

    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });

  it("a read() rejection mid-stream calls onError once and sendMessage resolves", async () => {
    const reads = [
      Promise.resolve({ done: false, value: frame("token", "partial") }),
      Promise.reject(new TypeError("network error")),
    ];
    const reader = { read: vi.fn(() => reads.shift()) };
    mockFetch.mockResolvedValue(okResponse({ getReader: () => reader }));
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    await expect(
      result.current.sendMessage("c1", "conv1", "hello", callbacks),
    ).resolves.toBeUndefined();

    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });

  it("an ok response with a null body calls onError once", async () => {
    mockFetch.mockResolvedValue(okResponse(null));
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    await result.current.sendMessage("c1", "conv1", "hello", callbacks);

    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });

  it("an explicit error event calls onError exactly once (no second call on close)", async () => {
    mockFetch.mockResolvedValue(okResponse(streamOf(frame("token", "x"), frame("error"))));
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    await result.current.sendMessage("c1", "conv1", "hello", callbacks);

    expect(callbacks.onError).toHaveBeenCalledTimes(1);
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });

  it("a 401 response rejects with an ApiError carrying the status", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 401, body: null } as unknown as Response);
    const { result } = renderHook(() => useChatStream());
    const callbacks = makeCallbacks();

    const error = await result.current
      .sendMessage("c1", "conv1", "hello", callbacks)
      .then(
        () => null,
        (caught: unknown) => caught,
      );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onDone).not.toHaveBeenCalled();
  });
});
