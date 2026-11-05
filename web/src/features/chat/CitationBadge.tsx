import { Badge } from "@/components/ui/badge";

import type { CitationSpan } from "./useChatStream";

export type OnOpenCitation = (bookId: string, page: number, filename: string) => void;

interface CitationBadgeProps {
  citation: CitationSpan;
  onOpen: OnOpenCitation;
}

/**
 * Renders one `[n]` marker as a clickable, keyboard-focusable citation
 * Reuses the
 * shadcn `Badge`'s existing accent `default` variant via `asChild` (the
 * same `Slot`-based pattern already used by `Button asChild` in
 * `LoginPage.tsx`) so the rendered DOM node is a real `<button>`, not a
 * bare `<span>` - inline at the surrounding text's baseline, never
 * superscript-raised.
 */
export function CitationBadge({ citation, onOpen }: CitationBadgeProps) {
  return (
    <Badge asChild className="mx-0.5 cursor-pointer align-baseline">
      <button
        type="button"
        aria-label={`Open citation ${citation.marker}: ${citation.original_filename}, page ${citation.page_start}`}
        onClick={() =>
          onOpen(citation.book_id, citation.page_start, citation.original_filename)
        }
      >
        [{citation.marker}]
      </button>
    </Badge>
  );
}
