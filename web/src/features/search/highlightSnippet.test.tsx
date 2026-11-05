import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { renderSnippet } from "./highlightSnippet";

const START = "\x01";
const STOP = "\x02";

describe("renderSnippet (snippet rendering safety contract)", () => {
  it("wraps exactly one marked segment in a single <mark> with the inner text", () => {
    const snippet = `some text ${START}term${STOP} more text`;
    const { container } = render(<div>{renderSnippet(snippet)}</div>);

    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe("term");
  });

  it("renders plain text with no <mark> when there are no markers", () => {
    const snippet = "no highlighted terms in this sentence";
    const { container } = render(<div>{renderSnippet(snippet)}</div>);

    expect(container.querySelectorAll("mark")).toHaveLength(0);
    expect(container.textContent).toBe(snippet);
  });

  it("renders a literal <b> in chunk text as visible escaped text, never as an HTML element", () => {
    const snippet = `chunk text with a literal <b>bold</b> tag and ${START}match${STOP}`;
    const { container } = render(<div>{renderSnippet(snippet)}</div>);

    // No real <b> element was created from the chunk-derived string.
    expect(container.querySelectorAll("b")).toHaveLength(0);
    // The literal characters still appear as visible text (React's default
    // JSX escaping), proving the string was never interpreted as markup.
    expect(container.textContent).toContain("<b>bold</b>");
    // The genuine StartSel/StopSel match is still highlighted correctly.
    expect(container.querySelector("mark")?.textContent).toBe("match");
  });

  it("never uses dangerouslySetInnerHTML in its own source", () => {
    const sourcePath = path.resolve(
      process.cwd(),
      "src/features/search/highlightSnippet.tsx",
    );
    const source = readFileSync(sourcePath, "utf-8");
    expect(source).not.toContain("dangerouslySetInnerHTML");
  });
});
