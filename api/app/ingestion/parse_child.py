"""Child-process entry point for the Docling parse stage.

Run as `python -m app.ingestion.parse_child <pdf_path> <out_json>` by
`app.ingestion.pipeline.parse_book_in_subprocess`. The parse runs in its own
OS process because a thread cannot be killed: on a timeout, a crash, or an
out-of-memory kill the parent can terminate (or observe the death of) a
process and report a readable `[parse]` failure, while a runaway thread would
keep running and compete with the next book's parse.

The parsed `DoclingDocument` is handed back as lossless Docling JSON, never
Markdown or plain text, so page numbers and bbox provenance survive the
process boundary. `ImageRefMode.PLACEHOLDER` keeps embedded page
images out of the JSON: chunking only needs text, structure, and `prov`.

On any exception the full traceback goes to stderr (its last line is
`ExcType: message`, which the parent surfaces in `status_error`) and the
process exits 1. Docling imports stay lazy inside `app.ingestion.parsing`, so
importing this module never requires the ML stack.
"""

import sys
import traceback


def main(argv: list[str]) -> int:
    pdf_path, out_json = argv

    try:
        from docling_core.types.doc.base import ImageRefMode

        from app.ingestion.parsing import parse_book

        doc = parse_book(pdf_path)
        doc.save_as_json(out_json, image_mode=ImageRefMode.PLACEHOLDER)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
