/**
 * Owns the whole assistant-text rendering pipeline for the course chat, now
 * a three-stage pipeline:
 *
 * 1. Math segmentation (`splitMathSegments`) -
 *    LaTeX delimiter scanning runs first, before anything else touches the
 *    text, because markdown parsing would otherwise eat `\[`, `\(`, `*`, and
 *    `_` inside TeX source (`a_1 * b_2` would become emphasis) and corrupt
 *    the expression before KaTeX ever sees it.
 * 2. Placeholder tokenization - math segments and resolved `[n]` citation
 *    markers are folded into a single markdown source string as inert
 *    private-use-area placeholder tokens, recorded in a slot table. This
 *    must happen BEFORE the markdown parse: `[` and `]` are markdown link
 *    syntax, so attempting the `[n]` split on markdown's own output text
 *    leaves would silently find nothing (micromark is free to tokenize an
 *    unmatched bracket run into separate text nodes) and every citation
 *    badge would vanish.
 * 3. A single markdown parse (`react-markdown` + `remark-gfm`) over the
 *    placeholder-bearing source, with the placeholders swapped back for
 *    KaTeX nodes / `CitationBadge`s in the rendered text leaves.
 *
 * Markdown is layered on without widening the raw-HTML safety boundary:
 * the math/citation stages are reused unchanged, styling is conservative and
 * inherited, and `rehype-raw` is never enabled.
 *
 * Both the persisted message row (`ChatMessage.tsx`) and the in-flight
 * streaming row (`ChatPage.tsx`) call this same function, so the live text
 * and the final text always read identically - the `ASSISTANT_ROW_CLASS`
 * comment's stated intent.
 */
import type { ReactNode } from "react";
import katex from "katex";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationBadge, type OnOpenCitation } from "./CitationBadge";
import type { CitationSpan } from "./useChatStream";

export type MathSegment =
  | { type: "text"; value: string }
  | { type: "math"; tex: string; display: boolean; open: string; close: string };

const MARKER_PATTERN = /(\[\d+\])/g;
const MARKER_CAPTURE = /^\[(\d+)\]$/;

// Delimiter pairs, longest opener first: `$$` and `\[` are always attempted
// before the one-character `$`, so a display opener like `$$\left[3\right]$$`
// is never mis-read as an empty inline `$` span (must_haves.key_links).
const DELIMITERS: { open: string; close: string; display: boolean }[] = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
  { open: "$", close: "$", display: false },
];

function isWhitespace(char: string | undefined): boolean {
  return char !== undefined && /\s/.test(char);
}

/**
 * Left-to-right scanner over the assistant text, hand-written rather than a
 * regex alternation, because the acceptance rules below (whitespace-
 * adjacency rejection, blank-line rejection, escaped-dollar handling,
 * longest-opener-first matching) are not expressible cleanly as one regex -
 * a scanner keeps every rule in one readable place.
 *
 * Invariant: for any input string, concatenating the text segments' values
 * and each math segment's `open + tex + close` reproduces the input exactly.
 * This is what guarantees a no-math message renders byte-identically to
 * before this change existed, and it is maintained by every rejection path
 * emitting its consumed characters as text rather than dropping them.
 */
export function splitMathSegments(text: string): MathSegment[] {
  const segments: MathSegment[] = [];
  let buffer = "";
  let i = 0;

  const flush = () => {
    if (buffer.length > 0) {
      segments.push({ type: "text", value: buffer });
      buffer = "";
    }
  };

  outer: while (i < text.length) {
    // A backslash-escaped dollar is a literal dollar character, not a
    // delimiter - consume both characters as one literal "$" so the
    // scanner never mistakes what follows for an opener boundary.
    if (text[i] === "\\" && text[i + 1] === "$") {
      buffer += "$";
      i += 2;
      continue;
    }

    for (const delimiter of DELIMITERS) {
      if (text.startsWith(delimiter.open, i)) {
        const contentStart = i + delimiter.open.length;
        const closeIndex = text.indexOf(delimiter.close, contentStart);

        if (closeIndex === -1) {
          // Unterminated (streaming half-arrival) - degrade the rest of the
          // string to literal text rather than throwing or blanking the row.
          buffer += text.slice(i);
          break outer;
        }

        const content = text.slice(contentStart, closeIndex);
        const rejected =
          content.trim().length === 0 || // empty or whitespace-only
          isWhitespace(content[0]) || // opener followed by whitespace (currency guard)
          isWhitespace(content[content.length - 1]) || // closer preceded by whitespace (currency guard)
          content.includes("\n\n"); // a real expression never spans a blank line

        if (rejected) {
          // Emit the opening delimiter as literal text and resume scanning
          // from just after it - the content and closer get re-scanned
          // normally, which is what keeps the function total.
          buffer += delimiter.open;
          i = contentStart;
          continue outer;
        }

        flush();
        segments.push({
          type: "math",
          tex: content,
          display: delimiter.display,
          open: delimiter.open,
          close: delimiter.close,
        });
        i = closeIndex + delimiter.close.length;
        continue outer;
      }
    }

    buffer += text[i];
    i += 1;
  }
  flush();
  return segments;
}

// Module-level memo cache keyed by display-mode + TeX source. The streaming
// row re-renders on every token, so without this every math expression in
// the accumulated answer would be re-typeset on every single token (quadratic
// in answer length). A coarse full-clear at 500 entries bounds
// memory without needing an LRU.
const katexHtmlCache = new Map<string, string | null>();

function renderMathHtml(tex: string, display: boolean): string | null {
  const cacheKey = `${display ? "d" : "i"}:${tex}`;
  const cached = katexHtmlCache.get(cacheKey);
  if (cached !== undefined) return cached;

  let html: string | null;
  try {
    html = katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      output: "htmlAndMathml",
      strict: false,
      // Explicit even though it is KaTeX's own default,
      // so a future edit can't silently drop it. Refuses \href, \url, and
      // \includegraphics - the commands that could otherwise emit a live
      // link or an outbound image request from model-authored TeX.
      trust: false,
    });
  } catch {
    // A KaTeX failure that bypasses throwOnError: false. Cache
    // the miss too so a persistently-invalid expression doesn't re-throw on
    // every re-render.
    html = null;
  }

  if (katexHtmlCache.size >= 500) katexHtmlCache.clear();
  katexHtmlCache.set(cacheKey, html);
  return html;
}

/**
 * Renders one math segment. A `span` for inline math (sits on the
 * surrounding text's baseline) and a block-styled `span` for display math
 * (occupies its own centered line). Display math is a `span`, not a `div`,
 * because it now lands inside markdown's `<p>` output - a `<div>` inside a
 * `<p>` is invalid DOM nesting and React would warn at render time. A
 * `display: block` span is valid there and is visually identical.
 *
 * This is the one narrow exception to this codebase's no-raw-HTML-injection
 * contract (see `highlightSnippet.tsx`'s safety contract), and it is
 * justified here: the string assigned via `dangerouslySetInnerHTML`
 * is KaTeX's own generated markup, never the model's own text; the
 * untrusted TeX source above is only ever passed to `katex.renderToString`
 * as a JavaScript string argument and is never itself assigned to
 * `innerHTML`; and `trust: false` makes KaTeX refuse the commands that
 * could emit arbitrary markup or URLs.
 */
function renderMathNode(segment: Extract<MathSegment, { type: "math" }>, key: string): ReactNode {
  const html = renderMathHtml(segment.tex, segment.display);
  if (html === null) {
    // Fallback: invalid TeX that bypassed throwOnError - show the
    // original delimited source as plain text rather than unmounting the
    // whole message row.
    return `${segment.open}${segment.tex}${segment.close}`;
  }

  if (segment.display) {
    return (
      <span
        key={key}
        className="my-2 block text-center"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  return <span key={key} dangerouslySetInnerHTML={{ __html: html }} />;
}

// Private-use-area sentinels wrap every placeholder token. These carry no
// meaning in CommonMark or GFM, so remark/react-markdown cannot rewrite,
// split, or escape them, and the swap-back below is exact. Kind letters:
// `m` for a math slot, `c` for a citation slot.
const PLACEHOLDER_OPEN = "";
const PLACEHOLDER_CLOSE = "";
const PLACEHOLDER_PATTERN = /([mc]\d+)/g;
const PLACEHOLDER_CAPTURE = /^([mc])(\d+)$/;

// Strips any private-use-area sentinel the model's own text might
// contain, before placeholders are inserted, so model output can never
// forge a slot reference and cause an arbitrary recorded math/citation node
// to render at a position it does not own.
function stripForgedPlaceholders(text: string): string {
  return text.replace(/[]/g, "");
}

type MathSlot = { kind: "math"; segment: Extract<MathSegment, { type: "math" }> };
type CitationSlot = { kind: "citation"; citation: CitationSpan };
type Slot = MathSlot | CitationSlot;

/**
 * Stage 2: folds the math-segmented content into one markdown source string,
 * replacing each math segment and each resolved `[n]` citation marker with
 * an inert placeholder token, and returns the slot table needed to swap
 * those placeholders back for real nodes after the markdown parse.
 *
 * The `[n]` split runs here, on the raw text segments, and NOT after
 * markdown parsing - see the file header comment for why the latter would
 * silently drop every citation badge.
 */
function buildMarkdownSource(
  mathSegments: MathSegment[],
  citations: CitationSpan[],
): { source: string; slots: Slot[] } {
  const citationsByMarker = new Map(citations.map((citation) => [citation.marker, citation]));
  const slots: Slot[] = [];
  let source = "";

  for (const segment of mathSegments) {
    if (segment.type === "math") {
      const slotIndex = slots.length;
      slots.push({ kind: "math", segment });
      source += `${PLACEHOLDER_OPEN}m${slotIndex}${PLACEHOLDER_CLOSE}`;
      continue;
    }

    const safeText = stripForgedPlaceholders(segment.value);
    const parts = safeText.split(MARKER_PATTERN);
    for (const part of parts) {
      if (part.length === 0) continue;
      const match = MARKER_CAPTURE.exec(part);
      if (match) {
        const citation = citationsByMarker.get(Number(match[1]));
        if (citation) {
          const slotIndex = slots.length;
          slots.push({ kind: "citation", citation });
          source += `${PLACEHOLDER_OPEN}c${slotIndex}${PLACEHOLDER_CLOSE}`;
          continue;
        }
      }
      source += part;
    }
  }

  return { source, slots };
}

/**
 * Splits a rendered markdown text leaf on placeholder tokens and swaps each
 * one for its real node (KaTeX math or a `CitationBadge`), via the slot
 * table built in stage 2. Non-string children pass through untouched.
 */
function resolvePlaceholders(
  children: ReactNode,
  slots: Slot[],
  onOpenCitation: OnOpenCitation,
  keyPrefix: string,
): ReactNode {
  if (typeof children !== "string") return children;
  if (!children.includes(PLACEHOLDER_OPEN)) return children;

  return children
    .split(PLACEHOLDER_PATTERN)
    .filter((part) => part.length > 0)
    .map((part, index) => {
      const match = PLACEHOLDER_CAPTURE.exec(part);
      if (!match) return part;

      const slotIndex = Number(match[2]);
      const slot = slots[slotIndex];
      if (!slot) return part;

      const key = `${keyPrefix}-slot-${slotIndex}-${index}`;
      if (slot.kind === "math") {
        return renderMathNode(slot.segment, key);
      }
      return <CitationBadge key={key} citation={slot.citation} onOpen={onOpenCitation} />;
    });
}

/**
 * Code-context counterpart to `resolvePlaceholders`: used inside the `code`
 * and `pre` overrides, where a placeholder must be restored to its ORIGINAL
 * literal source (`open + tex + close` for math, `[n]` for a citation)
 * rather than becoming a badge or a KaTeX node. Without this, a `$A$` or
 * `[1]` the model wrote inside a code fence would render as leftover
 * private-use-area tofu glyphs - a visible garbage-character bug.
 */
function restoreLiteralPlaceholders(children: ReactNode, slots: Slot[]): ReactNode {
  if (typeof children !== "string") return children;
  if (!children.includes(PLACEHOLDER_OPEN)) return children;

  return children.replace(PLACEHOLDER_PATTERN, (token) => {
    const match = PLACEHOLDER_CAPTURE.exec(token);
    if (!match) return token;

    const slotIndex = Number(match[2]);
    const slot = slots[slotIndex];
    if (!slot) return token;

    if (slot.kind === "math") {
      return `${slot.segment.open}${slot.segment.tex}${slot.segment.close}`;
    }
    return `[${slot.citation.marker}]`;
  });
}

function resolveChildren(
  children: ReactNode,
  slots: Slot[],
  onOpenCitation: OnOpenCitation,
  keyPrefix: string,
): ReactNode {
  if (Array.isArray(children)) {
    return children.map((child, index) =>
      resolvePlaceholders(child, slots, onOpenCitation, `${keyPrefix}-${index}`),
    );
  }
  return resolvePlaceholders(children, slots, onOpenCitation, keyPrefix);
}

function resolveCodeChildren(children: ReactNode, slots: Slot[]): ReactNode {
  if (Array.isArray(children)) {
    return children.map((child) => restoreLiteralPlaceholders(child, slots));
  }
  return restoreLiteralPlaceholders(children, slots);
}

/**
 * Builds the `components` map passed to `ReactMarkdown`. A factory (rather
 * than a module-level constant) so each override's closure captures this
 * call's slot table, citations, and `onOpenCitation` - `ReactMarkdown`
 * re-renders on every streamed token, so a fresh map per call is required
 * for the swap-back to see the current slot table.
 *
 * No `prose` classes are available (`@tailwindcss/typography` is not
 * installed), so every element gets explicit Tailwind classes that inherit
 * rather than fight `ASSISTANT_ROW_CLASS`. A message with no markdown in it
 * still renders visually identically to before this change: no card,
 * border, or background is added to the row itself.
 */
function buildComponents(slots: Slot[], onOpenCitation: OnOpenCitation): Components {
  const resolve = (children: ReactNode, keyPrefix: string) =>
    resolveChildren(children, slots, onOpenCitation, keyPrefix);

  return {
    p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{resolve(children, "p")}</p>,
    ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-6">{children}</ul>,
    ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-6">{children}</ol>,
    li: ({ children }) => <li className="leading-[1.5]">{resolve(children, "li")}</li>,
    strong: ({ children }) => (
      <strong className="font-semibold">{resolve(children, "strong")}</strong>
    ),
    em: ({ children }) => <em className="italic">{resolve(children, "em")}</em>,
    del: ({ children }) => <del className="line-through">{resolve(children, "del")}</del>,
    code: ({ children }) => (
      <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800">
        {resolveCodeChildren(children, slots)}
      </code>
    ),
    pre: ({ children }) => (
      <pre className="my-2 overflow-x-auto rounded-md bg-slate-100 p-3 text-sm [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[1em]">
        {children}
      </pre>
    ),
    h1: ({ children }) => (
      <h1 className="mt-3 mb-1 text-lg font-semibold">{resolve(children, "h1")}</h1>
    ),
    h2: ({ children }) => (
      <h2 className="mt-3 mb-1 text-base font-semibold">{resolve(children, "h2")}</h2>
    ),
    h3: ({ children }) => (
      <h3 className="mt-3 mb-1 text-base font-semibold">{resolve(children, "h3")}</h3>
    ),
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-slate-200 pl-3 text-slate-600">
        {children}
      </blockquote>
    ),
    a: ({ children, href }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-slate-900 underline underline-offset-2"
      >
        {resolve(children, "a")}
      </a>
    ),
    hr: () => <hr className="my-3 border-slate-200" />,
    table: ({ children }) => (
      <table className="my-2 w-full border-collapse text-sm">{children}</table>
    ),
    th: ({ children }) => (
      <th className="border border-slate-200 px-2 py-1 text-left font-semibold">
        {resolve(children, "th")}
      </th>
    ),
    td: ({ children }) => (
      <td className="border border-slate-200 px-2 py-1">{resolve(children, "td")}</td>
    ),
  };
}

/**
 * The full assistant-text pipeline. See the file header for the three
 * stages. Returns a single React node (a `ReactMarkdown` element) rather
 * than the flat array the previous two-stage pipeline returned; both call
 * sites already interpolate the result as a single JSX child, so this is
 * source-compatible.
 */
export function renderAssistantContent(
  content: string,
  citations: CitationSpan[],
  onOpenCitation: OnOpenCitation,
): ReactNode {
  const mathSegments = splitMathSegments(content);
  const { source, slots } = buildMarkdownSource(mathSegments, citations);
  const components = buildComponents(slots, onOpenCitation);

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {source}
    </ReactMarkdown>
  );
}
