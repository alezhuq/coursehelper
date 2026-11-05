import type { OnOpenCitation } from "./CitationBadge";
import { renderAssistantContent } from "./renderAssistantContent";
import type { CitationSpan } from "./useChatStream";

export interface ChatMessageData {
  role: string;
  content: string;
  citations: CitationSpan[];
}

// Shared with ChatPage's in-flight streaming row so the "live" assistant
// text and the persisted assistant row read identically once the stream
// finishes.
export const ASSISTANT_ROW_CLASS =
  "max-w-full break-words text-base leading-[1.5] text-slate-900";

interface ChatMessageProps {
  message: ChatMessageData;
  onOpenCitation: OnOpenCitation;
}

/**
 * One message row. User rows are
 * right-aligned on a slate-100 block; assistant rows (including the
 * refusal) render as markdown-formatted prose directly on white -
 * no card/bubble, no destructive/warning treatment, since a refusal is a
 * correct, calm answer, not a failure state. Bold, lists, code, headings,
 * and GFM tables render as real elements; `[n]`
 * citation markers are still upgraded to clickable `CitationBadge`s, and a
 * message with no citations renders no additional citation UI at all,
 * never a blank placeholder. LaTeX math (`$...$`, `$$...$$`, `\(...\)`,
 * `\[...\]`) is typeset via KaTeX. All three concerns (math segmentation,
 * citation-marker tokenization, markdown parsing) live in
 * `renderAssistantContent.tsx`'s single three-stage assistant-text
 * pipeline.
 */
export function ChatMessage({ message, onOpenCitation }: ChatMessageProps) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-md bg-slate-100 px-4 py-2 text-base leading-[1.5] text-slate-900">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className={ASSISTANT_ROW_CLASS}>
        {renderAssistantContent(message.content, message.citations, onOpenCitation)}
      </div>
    </div>
  );
}
