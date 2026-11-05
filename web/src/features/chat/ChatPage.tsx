import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, Trash2 } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { ASSISTANT_ROW_CLASS, ChatMessage, type ChatMessageData } from "./ChatMessage";
import { CitationPanel } from "./CitationPanel";
import { renderAssistantContent } from "./renderAssistantContent";
import { type CitationSpan, useChatStream } from "./useChatStream";

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
  | { kind: "ready"; course: Course }
  | { kind: "pageError" };

type ConversationState =
  | { kind: "loading" }
  | { kind: "ok"; conversationId: string }
  | { kind: "error" };

// A turn is the request/response unit from submit through the assistant's
// full reply. "thinking" covers condense->retrieve->gate->generate before
// the first token event; "streaming" accumulates tokens as they arrive;
// "error" surfaces the inline turn-failure Alert. Composer
// re-enables immediately on "error" - the failure is surfaced entirely at
// the message-thread level, never at the composer itself.
type TurnState =
  | { kind: "idle" }
  | { kind: "thinking" }
  | { kind: "streaming"; text: string }
  | { kind: "error"; failedContent: string };

// Citation-panel state: not
// rendered at all until the first citation click this session (closed);
// once open, clicking a different citation swaps bookId/page/filename in
// place rather than unmounting/remounting the panel.
type CitationPanelState =
  | { open: false }
  | { open: true; bookId: string; page: number; filename: string };

/**
 * `/courses/:id/chat`. Mirrors
 * SearchPage's loading|ok|error page state machine and 401->/login /
 * 403->/ redirect convention, but with NO AppHeader - its own sticky
 * toolbar, matching ViewerPage's full-page-surface precedent.
 *
 * Two independent state machines: the outer `state` (loading|ready|
 * pageError) gates the course header/toolbar (mirrors every other
 * course-scoped page); the inner `convState` (loading|ok|error) gates only
 * the message-list region, per the "conversation load failure
 * replaces the whole message-list region" contract - the toolbar with the
 * course title stays visible even if history fails to load. A 403 on
 * either fetch redirects to `/`, a 401 to `/login`.
 *
 * Assistant text renders as markdown (bold, lists, code, headings, GFM
 * tables), `[n]` citation markers render as clickable `CitationBadge`s, and
 * LaTeX math is typeset via KaTeX - all three live in
 * `renderAssistantContent.tsx`'s single three-stage assistant-text
 * pipeline, shared by the persisted message row (`ChatMessage.tsx`) and the
 * in-flight streaming row below, so the live text and the final text always
 * read identically.
 */
export function ChatPage() {
  const { id: courseId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { sendMessage } = useChatStream();

  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [convState, setConvState] = useState<ConversationState>({ kind: "loading" });
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [turn, setTurn] = useState<TurnState>({ kind: "idle" });
  const [draft, setDraft] = useState("");
  const [citationPanel, setCitationPanel] = useState<CitationPanelState>({
    open: false,
  });
  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);

  const streamTextRef = useRef("");
  const streamCitationsRef = useRef<CitationSpan[]>([]);
  const messageListRef = useRef<HTMLDivElement>(null);

  const loadHistory = useCallback(async () => {
    if (!courseId) return;
    setConvState({ kind: "loading" });
    try {
      const response = await apiFetch(`/courses/${courseId}/chat/conversation`);
      const data = (await response.json()) as {
        conversation_id: string;
        messages: ChatMessageData[];
      };
      setConvState({ kind: "ok", conversationId: data.conversation_id });
      setMessages(data.messages);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError && error.status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setConvState({ kind: "error" });
    }
  }, [courseId, navigate]);

  const loadCourse = useCallback(async () => {
    if (!courseId) return;
    setState({ kind: "loading" });
    try {
      const coursesResponse = await apiFetch("/courses");
      const courses = (await coursesResponse.json()) as Course[];
      const course = courses.find((candidate) => candidate.id === courseId);
      if (!course) {
        setState({ kind: "pageError" });
        return;
      }
      setState({ kind: "ready", course });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError && error.status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setState({ kind: "pageError" });
      return;
    }
    await loadHistory();
  }, [courseId, navigate, loadHistory]);

  useEffect(() => {
    void loadCourse();
    // Only ever re-runs when courseId itself changes - loadCourse's other
    // dependencies (navigate, loadHistory) are stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId]);

  // Always-follow auto-scroll (no scroll-lock yet) on every new message or streaming token. jsdom's test
  // environment does not implement Element.scrollTo, so this guards for
  // its absence rather than throwing during ChatPage.test.tsx runs.
  useEffect(() => {
    const list = messageListRef.current;
    if (list && typeof list.scrollTo === "function") {
      list.scrollTo({ top: list.scrollHeight });
    }
  }, [messages, turn]);

  const submitTurn = useCallback(
    async (content: string, conversationId: string) => {
      streamTextRef.current = "";
      streamCitationsRef.current = [];
      setTurn({ kind: "thinking" });
      try {
        await sendMessage(courseId ?? "", conversationId, content, {
          onToken: (delta) => {
            streamTextRef.current += delta;
            setTurn({ kind: "streaming", text: streamTextRef.current });
          },
          onCitations: (citations) => {
            streamCitationsRef.current = citations;
          },
          onDone: () => {
            setMessages((previous) => [
              ...previous,
              {
                role: "assistant",
                content: streamTextRef.current,
                citations: streamCitationsRef.current,
              },
            ]);
            setTurn({ kind: "idle" });
          },
          onError: () => {
            setTurn({ kind: "error", failedContent: content });
          },
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        if (error instanceof ApiError && error.status === 403) {
          navigate("/", { replace: true });
          return;
        }
        setTurn({ kind: "error", failedContent: content });
      }
    },
    [courseId, navigate, sendMessage],
  );

  // A submit while a prior turn is still in-flight is impossible by
  // construction - the textarea and Send button are both disabled for the
  // entire thinking->streaming window (see `inFlight` below), so this
  // early-return is a defensive backstop, never an actually reachable path.
  function handleSubmit() {
    if (convState.kind !== "ok") return;
    if (turn.kind === "thinking" || turn.kind === "streaming") return;
    const trimmed = draft.trim();
    if (!trimmed) return;

    // Append-only: a new submit always appends a fresh user row and never
    // overwrites a prior turn, including one that just failed (its Alert
    // simply falls out of view below the new turn once it starts).
    // Exception: re-submitting the exact text of the turn that just failed
    // is a retry. The server reuses that unanswered user turn instead of
    // persisting a second copy, so the thread must not show a duplicate row
    // that a reload would remove.
    if (turn.kind === "error" && trimmed === turn.failedContent) {
      setDraft("");
      void submitTurn(trimmed, convState.conversationId);
      return;
    }

    setMessages((previous) => [
      ...previous,
      { role: "user", content: trimmed, citations: [] },
    ]);
    setDraft("");
    void submitTurn(trimmed, convState.conversationId);
  }

  function handleRetry() {
    if (turn.kind !== "error" || convState.kind !== "ok") return;
    void submitTurn(turn.failedContent, convState.conversationId);
  }

  // Opening a citation mounts the panel if it was closed, or swaps its
  // content in place if a different citation was already open. Never
  // navigates away from the chat page.
  const handleOpenCitation = useCallback(
    (bookId: string, page: number, filename: string) => {
      setCitationPanel({ open: true, bookId, page, filename });
    },
    [],
  );

  const handleCloseCitationPanel = useCallback(() => {
    setCitationPanel({ open: false });
  }, []);

  // Clears the persisted conversation history behind a confirm dialog.
  // Local state is only reset AFTER the DELETE resolves - an optimistic
  // clear would show an empty thread that's still fully persisted
  // server-side if the request fails. `convState` (and its
  // `conversationId`) is deliberately left untouched: the server keeps the
  // conversation row, so the cached id stays valid for the next send.
  async function handleClearConfirm() {
    if (!courseId) return;
    setClearing(true);
    setClearError(null);
    try {
      await apiFetch(`/courses/${courseId}/chat/conversation`, {
        method: "DELETE",
      });
      setMessages([]);
      setTurn({ kind: "idle" });
      setCitationPanel({ open: false });
      setClearDialogOpen(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError && error.status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setClearDialogOpen(false);
      setClearError(error instanceof Error ? error.message : "Action failed");
    } finally {
      setClearing(false);
    }
  }

  // Enter submits; no Shift+Enter newline support yet - matches the app-wide Enter-to-submit convention used
  // by Search's input.
  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      handleSubmit();
    }
  }

  const inFlight = turn.kind === "thinking" || turn.kind === "streaming";
  const trimmedDraft = draft.trim();
  const nothingToClear = messages.length === 0 && turn.kind === "idle";
  const clearDisabled =
    state.kind !== "ready" ||
    convState.kind !== "ok" ||
    inFlight ||
    clearing ||
    nothingToClear;

  if (state.kind === "loading") {
    return (
      <main className="flex h-screen flex-col">
        <p className="py-16 text-center text-sm text-slate-500">Loading...</p>
      </main>
    );
  }

  if (state.kind === "pageError") {
    return (
      <main className="flex h-screen flex-col items-center justify-center">
        <p className="text-sm text-slate-500">Could not load this page.</p>
      </main>
    );
  }

  const { course } = state;

  return (
    <main className="flex h-screen flex-col">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-slate-100 px-4 py-2">
        <Button
          type="button"
          variant="ghost"
          onClick={() => navigate(`/courses/${courseId}`)}
        >
          <ChevronLeft />
          Back to course
        </Button>
        <h1 className="min-w-0 truncate text-xl font-semibold leading-[1.2] text-slate-900">
          {course.code} {course.title}
        </h1>
        <Button
          type="button"
          variant="ghost"
          disabled={clearDisabled}
          onClick={() => setClearDialogOpen(true)}
        >
          <Trash2 />
          Clear conversation
        </Button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div
          className={
            citationPanel.open
              ? "flex w-full max-w-[520px] flex-col border-r border-slate-200"
              : "mx-auto flex w-full max-w-[720px] flex-col"
          }
        >
          <div
            ref={messageListRef}
            className="flex flex-1 flex-col gap-4 overflow-y-auto px-6 py-6"
          >
            {convState.kind === "loading" ? (
              <p className="py-16 text-center text-sm text-slate-500">
                Loading conversation…
              </p>
            ) : convState.kind === "error" ? (
              <div className="flex flex-col items-center gap-4 py-16">
                <Alert variant="destructive" className="max-w-md">
                  <AlertDescription>
                    Could not load this conversation. Try again.
                  </AlertDescription>
                </Alert>
                <Button type="button" variant="outline" onClick={() => void loadHistory()}>
                  Try again
                </Button>
              </div>
            ) : messages.length === 0 && turn.kind === "idle" ? (
              <p className="py-16 text-center text-sm text-slate-500">
                Ask a question about this course's materials.
              </p>
            ) : (
              <>
                {messages.map((message, index) => (
                  <ChatMessage
                    key={index}
                    message={message}
                    onOpenCitation={handleOpenCitation}
                  />
                ))}

                {turn.kind === "thinking" && (
                  <div className="flex justify-start">
                    <p className="text-sm text-slate-500">Thinking…</p>
                  </div>
                )}

                {turn.kind === "streaming" && (
                  <div className="flex justify-start">
                    <div className={ASSISTANT_ROW_CLASS}>
                      {/* Empty citations array: resolved citation spans only
                          arrive on the "citations" SSE event near the end of
                          the turn, so during streaming there is nothing for
                          a [n] marker to resolve against yet - it correctly
                          stays literal text until the persisted row (which
                          does have citations) replaces this one on done. */}
                      {renderAssistantContent(turn.text, [], handleOpenCitation)}
                      <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-slate-400" />
                    </div>
                  </div>
                )}

                {turn.kind === "error" && (
                  <div className="flex flex-col items-start gap-2">
                    <Alert variant="destructive">
                      <AlertDescription>
                        Could not get a response. Try again.
                      </AlertDescription>
                    </Alert>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleRetry}
                    >
                      Try again
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>

          {clearError && (
            <Alert variant="destructive" className="mx-6 mb-2">
              <AlertDescription>{clearError}</AlertDescription>
            </Alert>
          )}

          <div className="flex items-end gap-2 border-t border-slate-200 bg-white px-6 py-4">
            <Textarea
              placeholder="Ask a question about this course's materials"
              rows={1}
              className="max-h-40"
              value={draft}
              disabled={inFlight || convState.kind !== "ok"}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <Button
              type="button"
              disabled={inFlight || convState.kind !== "ok" || !trimmedDraft}
              onClick={handleSubmit}
            >
              {inFlight ? "Sending..." : "Send"}
            </Button>
          </div>
        </div>

        {citationPanel.open && (
          <CitationPanel
            courseId={courseId ?? ""}
            bookId={citationPanel.bookId}
            filename={citationPanel.filename}
            page={citationPanel.page}
            onClose={handleCloseCitationPanel}
          />
        )}
      </div>

      <AlertDialog
        open={clearDialogOpen}
        onOpenChange={(open) => {
          if (!open) setClearDialogOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              Every message in this thread will be permanently deleted. This
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={clearing}
              onClick={(event) => {
                event.preventDefault();
                void handleClearConfirm();
              }}
            >
              {clearing ? "Clearing..." : "Clear conversation"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
