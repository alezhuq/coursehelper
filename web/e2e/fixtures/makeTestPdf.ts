/**
 * Hand-rolled minimal multi-page PDF generator for the E2E suite.
 * No external PDF-generation dependency is needed: a blank-page
 * PDF (no /Contents stream) is a perfectly valid document for pdf.js to
 * parse and render - this only needs to be real enough for react-pdf's
 * <Document>/<Page> to report a real `numPages` and paint a canvas per
 * page, which is all viewer.spec.ts/search.spec.ts assert against.
 *
 * Byte offsets in the `xref` table are computed from the actual emitted
 * bytes (not hardcoded) so the file is valid regardless of `pageCount`.
 */
export function makeMultiPagePdf(pageCount: number): Buffer {
  const catalogObjNum = 1;
  const pagesObjNum = 2;
  const firstPageObjNum = 3;

  const kids = Array.from(
    { length: pageCount },
    (_, index) => `${firstPageObjNum + index} 0 R`,
  ).join(" ");

  const objects: string[] = [
    `${catalogObjNum} 0 obj\n<< /Type /Catalog /Pages ${pagesObjNum} 0 R >>\nendobj\n`,
    `${pagesObjNum} 0 obj\n<< /Type /Pages /Kids [${kids}] /Count ${pageCount} >>\nendobj\n`,
    ...Array.from({ length: pageCount }, (_, index) => {
      const objNum = firstPageObjNum + index;
      return `${objNum} 0 obj\n<< /Type /Page /Parent ${pagesObjNum} 0 R /MediaBox [0 0 200 200] >>\nendobj\n`;
    }),
  ];

  const header = "%PDF-1.4\n";
  let body = "";
  const offsets: number[] = [0]; // object 0 is the free-list head, offset unused
  let cursor = header.length;
  for (const object of objects) {
    offsets.push(cursor);
    body += object;
    cursor += object.length;
  }

  const xrefStart = cursor;
  const totalObjects = objects.length + 1; // + the free object 0
  let xref = `xref\n0 ${totalObjects}\n0000000000 65535 f \n`;
  for (let i = 1; i < totalObjects; i++) {
    xref += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
  }

  const trailer = `trailer\n<< /Size ${totalObjects} /Root ${catalogObjNum} 0 R >>\nstartxref\n${xrefStart}\n%%EOF`;

  return Buffer.from(header + body + xref + trailer, "latin1");
}
