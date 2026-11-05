/**
 * Parses a `ts_headline` snippet's StartSel/StopSel control-character
 * markers into plain React nodes - never via React's raw-HTML injection
 * prop.
 *
 * `\x01`/`\x02` are the exact non-printable control characters
 * `api/app/search/repo.py`'s `HEADLINE_OPTIONS` configures as StartSel/
 * StopSel - chosen specifically because they can never collide with any
 * character that legitimately occurs in OCR'd/parsed PDF chunk text.
 * Every plain-text segment returned here is a bare string, which React
 * renders as an escaped text node automatically - so no chunk-derived
 * content, however it was produced, can ever inject markup into the page.
 */

import type { ReactNode } from "react";

const START = "\x01";
const STOP = "\x02";
const TOKEN_PATTERN = new RegExp(`(${START}[^${STOP}]*${STOP})`, "g");

export function renderSnippet(snippet: string): ReactNode[] {
  const parts = snippet.split(TOKEN_PATTERN);
  return parts
    .filter((part) => part.length > 0)
    .map((part, index) => {
      if (part.startsWith(START) && part.endsWith(STOP)) {
        const term = part.slice(1, -1);
        return (
          <mark
            key={index}
            className="rounded-sm bg-amber-200 px-0.5 text-slate-900"
          >
            {term}
          </mark>
        );
      }
      return part;
    });
}
