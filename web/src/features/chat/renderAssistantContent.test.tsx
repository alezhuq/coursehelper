import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderAssistantContent } from "./renderAssistantContent";
import type { CitationSpan } from "./useChatStream";

const noop = vi.fn();

function texOf(container: HTMLElement, index = 0): string | null | undefined {
  return container.querySelectorAll('annotation[encoding="application/x-tex"]')[index]
    ?.textContent;
}

// Lifted to module scope so both the citation-ordering
// describe and the new composition/code-restoration/forgery-guard describes
// can reuse the same two-span fixture. Mechanical move only, no assertion
// change.
const CITATIONS: CitationSpan[] = [
  {
    marker: 1,
    book_id: "book-1",
    original_filename: "geometry.pdf",
    page_start: 12,
    page_end: 12,
    section_path: ["Chapter 3"],
  },
  {
    marker: 2,
    book_id: "book-1",
    original_filename: "geometry.pdf",
    page_start: 13,
    page_end: 13,
    section_path: ["Chapter 3"],
  },
];

describe("renderAssistantContent (inline math end to end)", () => {
  it("renders exactly one .katex node for a single inline $...$ span", () => {
    const { container } = render(
      <div>
        {renderAssistantContent(
          "The identity is $x^2 + y^2 = z^2$ exactly.",
          [],
          noop,
        )}
      </div>,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
  });

  it("typesets the exact TeX source, verified via the MathML annotation", () => {
    const { container } = render(
      <div>
        {renderAssistantContent(
          "The identity is $x^2 + y^2 = z^2$ exactly.",
          [],
          noop,
        )}
      </div>,
    );

    const annotation = container.querySelector(
      'annotation[encoding="application/x-tex"]',
    );
    expect(annotation?.textContent).toBe("x^2 + y^2 = z^2");
  });

  it("preserves the surrounding prose around the math span", () => {
    const { container } = render(
      <div>
        {renderAssistantContent(
          "The identity is $x^2 + y^2 = z^2$ exactly.",
          [],
          noop,
        )}
      </div>,
    );

    expect(container.textContent?.startsWith("The identity is ")).toBe(true);
    expect(container.textContent?.endsWith(" exactly.")).toBe(true);
  });

  it("renders plain text with no math and no markers byte-identically (regression guard)", () => {
    const plain = "No math and no citation markers here at all.";
    const { container } = render(
      <div>{renderAssistantContent(plain, [], noop)}</div>,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe(plain);
  });
});

describe("renderAssistantContent (delimiter matrix)", () => {
  it("recognizes $$...$$ as display math", () => {
    const { container } = render(
      <div>
        {renderAssistantContent("$$\\int_0^1 f(x)\\,dx$$", [], noop)}
      </div>,
    );

    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(texOf(container)).toBe("\\int_0^1 f(x)\\,dx");
  });

  it("recognizes $...$ as inline math", () => {
    const { container } = render(<div>{renderAssistantContent("$x_i$", [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(0);
    expect(texOf(container)).toBe("x_i");
  });

  it("recognizes \\(...\\) as inline math", () => {
    const { container } = render(
      <div>{renderAssistantContent("\\(a \\ne b\\)", [], noop)}</div>,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(0);
    expect(texOf(container)).toBe("a \\ne b");
  });

  it("recognizes \\[...\\] as display math", () => {
    const { container } = render(
      <div>{renderAssistantContent("\\[a \\ne b\\]", [], noop)}</div>,
    );

    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(texOf(container)).toBe("a \\ne b");
  });

  it("matches the two-character $$ opener before the one-character $ opener", () => {
    const { container } = render(
      <div>{renderAssistantContent("$$a$$ then $b$", [], noop)}</div>,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(2);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(texOf(container, 0)).toBe("a");
    expect(texOf(container, 1)).toBe("b");
  });
});

describe("renderAssistantContent (false positives never become math)", () => {
  it("leaves two dollar amounts in prose as literal text (closing candidate preceded by whitespace)", () => {
    const text = "the reader costs $20 and the workbook $35";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe(text);
  });

  it("leaves a dollar sign followed by whitespace as literal text (opening candidate followed by whitespace)", () => {
    // A later $30 gives the scanner a real closing candidate, so this
    // specifically exercises the opener-followed-by-whitespace rejection
    // rather than the separate no-closer-found (unterminated) path.
    const text = "costs $ 20 today and $30 later";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe(text);
  });

  it("treats an empty $$$$ pair as no math rather than an empty expression", () => {
    const text = "an empty $$$$ pair of nothing";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe(text);
  });

  it("rejects a candidate span whose content contains a blank line", () => {
    const text = "price in$a\n\nb$time";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    // Whitespace-normalized: the blank line is now a real
    // markdown paragraph break, so it renders as two separate <p> elements
    // rather than one text run containing a literal "\n\n" - textContent
    // legitimately no longer contains the double newline. The katex-count
    // assertion above (the actual thing this test verifies) is unchanged.
    expect(container.textContent?.replace(/\s+/g, " ")).toBe(text.replace(/\s+/g, " "));
  });

  it("renders a backslash-escaped dollar as a single literal dollar character with no math", () => {
    const text = "a literal \\$5 escaped dollar";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe("a literal $5 escaped dollar");
  });
});

describe("renderAssistantContent (unterminated / streaming)", () => {
  it("renders a half-arrived $$ expression as literal text with no math node", () => {
    const text = "The formula is $$\\frac{a}{";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.textContent).toBe(text);
  });

  it("snaps to typeset math once the closing $$ arrives", () => {
    const text = "The formula is $$\\frac{a}{b}$$";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(texOf(container)).toBe("\\frac{a}{b}");
  });
});

describe("renderAssistantContent (citation ordering guard)", () => {
  it("resolves [n] markers outside math to badges and keeps math intact alongside them", () => {
    const text = "Pythagoras [1] gives $$c = \\sqrt{a^2 + b^2}$$ [2]";
    const { container } = render(<div>{renderAssistantContent(text, CITATIONS, noop)}</div>);

    expect(container.querySelectorAll("button")).toHaveLength(2);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
    expect(texOf(container)).toBe("c = \\sqrt{a^2 + b^2}");
  });

  it("never treats brackets inside math as citation markers", () => {
    const text = "$$\\left[ x \\right]$$";
    const { container } = render(<div>{renderAssistantContent(text, CITATIONS, noop)}</div>);

    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(container.querySelectorAll(".katex-display")).toHaveLength(1);
  });
});

describe("renderAssistantContent (math injection safety)", () => {
  it("produces no live script or img element and no onerror attribute for an injection payload inside math", () => {
    const text =
      "$\\text{<script>alert(1)</script>}$ and $\\text{<img src=x onerror=alert(1)>}$";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("script")).toHaveLength(0);
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelector("[onerror]")).toBeNull();
  });

  it("produces no <a> element for a javascript-URL \\href, proving trust:false is enforced", () => {
    const text = "$\\href{javascript:alert(1)}{click}$";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("a")).toHaveLength(0);
  });
});

describe("renderAssistantContent (markdown formatting)", () => {
  it("renders **bold** as a <strong> with no literal asterisks", () => {
    const { container } = render(
      <div>{renderAssistantContent("This is **bold** text.", [], noop)}</div>,
    );

    const strongs = container.querySelectorAll("strong");
    expect(strongs).toHaveLength(1);
    expect(strongs[0].textContent).toBe("bold");
    expect(container.textContent).not.toContain("**");
  });

  it("renders *italic* as an <em>", () => {
    const { container } = render(
      <div>{renderAssistantContent("This is *italic* text.", [], noop)}</div>,
    );

    expect(container.querySelectorAll("em")).toHaveLength(1);
  });

  it("renders a `- item` list as a <ul> with two <li> and no literal dashes", () => {
    const { container } = render(
      <div>{renderAssistantContent("- alpha\n- beta", [], noop)}</div>,
    );

    expect(container.querySelectorAll("ul")).toHaveLength(1);
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.textContent).not.toMatch(/^-|\n-/);
  });

  it("renders a `1. item` list as an <ol> with two <li>", () => {
    const { container } = render(
      <div>{renderAssistantContent("1. first\n2. second", [], noop)}</div>,
    );

    expect(container.querySelectorAll("ol")).toHaveLength(1);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("renders inline `code` as a <code> with no backticks", () => {
    const { container } = render(
      <div>{renderAssistantContent("Call `useState` to add state.", [], noop)}</div>,
    );

    expect(container.querySelectorAll("code")).toHaveLength(1);
    expect(container.querySelectorAll("code")[0].textContent).toBe("useState");
    expect(container.textContent).not.toContain("`");
  });

  it("renders a fenced code block as <pre><code>", () => {
    const text = "```js\nconst a = 1;\n```";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("pre")).toHaveLength(1);
    expect(container.querySelectorAll("pre > code")).toHaveLength(1);
    expect(container.querySelectorAll("pre code")[0].textContent).toContain("const a = 1;");
  });

  it("renders `## Heading` as a heading element with no literal hashes", () => {
    const { container } = render(<div>{renderAssistantContent("## Heading", [], noop)}</div>);

    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(container.textContent).not.toContain("##");
  });

  it("renders `> quoted` as a <blockquote>", () => {
    const { container } = render(<div>{renderAssistantContent("> quoted", [], noop)}</div>);

    expect(container.querySelectorAll("blockquote")).toHaveLength(1);
  });

  it("renders a GFM pipe table as a <table> with the expected th/td counts", () => {
    const text = "| A | B |\n| --- | --- |\n| 1 | 2 |";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("table")).toHaveLength(1);
    expect(container.querySelectorAll("table th")).toHaveLength(2);
    expect(container.querySelectorAll("table td")).toHaveLength(2);
  });
});

describe("renderAssistantContent (markdown composed with math and citations)", () => {
  it("keeps inline math and a citation badge inside the same list item", () => {
    const text = "- The identity $x^2 = y^2$ is proven here [1].\n- A plain second item.";
    const { container } = render(<div>{renderAssistantContent(text, CITATIONS, noop)}</div>);

    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(2);
    const firstItem = items[0];
    expect(firstItem.querySelectorAll(".katex")).toHaveLength(1);
    expect(firstItem.querySelectorAll("button")).toHaveLength(1);
  });

  it("renders bold text immediately adjacent to a citation marker", () => {
    const text = "**Key idea** [1] is the main point.";
    const { container } = render(<div>{renderAssistantContent(text, CITATIONS, noop)}</div>);

    expect(container.querySelectorAll("strong")).toHaveLength(1);
    expect(container.querySelectorAll("button")).toHaveLength(1);
  });

  it("typesets markdown-significant characters inside a math span as math, not emphasis", () => {
    const text = "The relation $a_1 * b_2$ holds.";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(container.querySelectorAll("em")).toHaveLength(0);
    expect(container.querySelectorAll("strong")).toHaveLength(0);
    expect(texOf(container)).toBe("a_1 * b_2");
  });
});

describe("renderAssistantContent (code-context restoration)", () => {
  it("renders a math-shaped span inside inline code as literal text, not math", () => {
    const text = "Use `$x$` as the placeholder syntax.";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(0);
    expect(container.querySelectorAll("code")[0].textContent).toBe("$x$");
    expect(container.textContent).not.toMatch(/[]/);
  });

  it("renders a citation-shaped marker inside a fenced block as literal text, not a badge", () => {
    const text = "```\nSee reference [1] in source.\n```";
    const { container } = render(<div>{renderAssistantContent(text, CITATIONS, noop)}</div>);

    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(container.querySelectorAll("pre code")[0].textContent).toContain("[1]");
  });
});

describe("renderAssistantContent (markdown safety)", () => {
  it("produces zero <script> elements for a <script> tag written as plain model text", () => {
    const text = "Here is a payload: <script>alert(1)</script> end.";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("script")).toHaveLength(0);
  });

  it("produces zero <img> elements and no onerror attribute for an <img onerror> tag written as plain model text", () => {
    const text = 'Here is a payload: <img src=x onerror=alert(1)> end.';
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.querySelector("[onerror]")).toBeNull();
  });

  it("produces no anchor with a javascript: href for a markdown link to a javascript: destination", () => {
    const text = "[click](javascript:alert(1))";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    const anchors = container.querySelectorAll("a");
    for (const anchor of anchors) {
      expect(anchor.getAttribute("href")?.startsWith("javascript:")).not.toBe(true);
    }
  });

  it("adds rel=noopener noreferrer to a rendered markdown link", () => {
    const text = "[docs](https://example.com/docs)";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
  });
});

describe("renderAssistantContent (streaming degradation)", () => {
  it("renders a half-arrived bold run without throwing and without blanking the row", () => {
    const text = "Some **bol";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.textContent).not.toBe("");
  });

  it("renders a half-arrived fenced code block without throwing", () => {
    const text = "```js\nconst a = 1";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.textContent).not.toBe("");
  });
});

describe("renderAssistantContent (placeholder forgery guard)", () => {
  it("strips a raw private-use-area sequence in model text so it cannot forge a slot reference", () => {
    // Real math ($x^2$) produces exactly one legitimate slot-0 placeholder.
    // The model's own text then tries to forge a second reference to that
    // same slot using the raw sentinel characters directly - if the strip
    // step in stage 2 did not run, this would render a second bogus .katex
    // node for text the model never actually wrote as math.
    const text = "$x^2$ and a forged reference m0 should stay inert.";
    const { container } = render(<div>{renderAssistantContent(text, [], noop)}</div>);

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(container.textContent).not.toMatch(/[]/);
  });
});
