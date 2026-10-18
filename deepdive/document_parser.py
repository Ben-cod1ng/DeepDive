"""Bounded text extraction in a disposable child process; no model or network calls."""
import io
import json
import re
import sys
import zipfile

MAX_BYTES = 5_000_000
MAX_CHARS = 240_000
MAX_PAGES = 200


def extract(data, suffix):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Upload must contain 1 byte to 5 MB")
    blocks, warnings = [], []
    if suffix in {".txt", ".md"}:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("Text documents must be UTF-8 without NUL bytes")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        section, start = "", 0
        headings = list(re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)) if suffix == ".md" else []
        for heading in headings:
            if text[start:heading.start()].strip():
                blocks.append({"text": text[start:heading.start()], "section": section, "page": None})
            section, start = heading.group(1)[:200], heading.start()
        blocks.append({"text": text[start:], "section": section, "page": None})
    elif suffix == ".pdf":
        from pypdf import PdfReader
        if not data.startswith(b"%PDF-"):
            raise ValueError("File is not a PDF")
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are unsupported; upload an unlocked copy")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF exceeds 200 pages")
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 2_000_000:
                raise ValueError("PDF page content exceeds the extraction limit")
            text = page.extract_text() or ""
            blocks.append({"text": text, "section": "", "page": number})
            if not text.strip():
                warnings.append(f"Page {number} has no extractable text; OCR is not enabled")
            if sum(len(b["text"]) for b in blocks) > MAX_CHARS:
                raise ValueError("Extracted document exceeds 240,000 characters")
    elif suffix == ".docx":
        from defusedxml import ElementTree
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) > 1000 or sum(e.file_size for e in entries) > 20_000_000
                    or any(e.flag_bits & 1 for e in entries)
                    or len({e.filename for e in entries}) != len(entries)):
                raise ValueError("DOCX archive exceeds limits or contains encrypted/duplicate entries")
            if any("vbaproject" in e.filename.lower() for e in entries):
                raise ValueError("Macro-enabled documents are unsupported")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 4_000_000:
                raise ValueError("DOCX text XML exceeds 4 MB")
            root = ElementTree.fromstring(archive.read(info))
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        body = root.find(ns + "body")
        if body is None:
            raise ValueError("DOCX has no document body")
        section, paragraphs = "", []
        def paragraph_text(node):
            return "".join((n.text or "") if n.tag == ns + "t" else "\n" if n.tag == ns + "br" else "\t"
                           for n in node.iter() if n.tag in {ns + "t", ns + "br", ns + "tab"})
        for node in body:
            if node.tag == ns + "p":
                text = paragraph_text(node)
                style = node.find(f"{ns}pPr/{ns}pStyle")
                if style is not None and style.get(ns + "val", "").lower().startswith("heading"):
                    if paragraphs:
                        blocks.append({"text": "\n\n".join(paragraphs), "section": section, "page": None})
                    section, paragraphs = text[:200], []
                paragraphs.append(text)
            elif node.tag == ns + "tbl":
                for row in node.findall(ns + "tr"):
                    paragraphs.append(" | ".join(paragraph_text(cell) for cell in row.findall(ns + "tc")))
        blocks.append({"text": "\n\n".join(paragraphs), "section": section, "page": None})
        warnings.append("DOCX locations use sections and text offsets; pagination, images, headers, and footnotes are not extracted")
    else:
        raise ValueError("Supported documents: PDF, DOCX, TXT, and Markdown")
    if sum(len(b["text"]) for b in blocks) > MAX_CHARS:
        raise ValueError("Extracted document exceeds 240,000 characters")
    if not any(b["text"].strip() for b in blocks):
        raise ValueError("No readable text; scanned documents require OCR before upload")
    return {"blocks": blocks, "warnings": warnings}


def main():
    # Linux containers have both this process limit and the Compose memory limit.
    # Windows uses the parent wall-clock timeout and input/output limits.
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        result = extract(sys.stdin.buffer.read(MAX_BYTES + 1), sys.argv[1])
        print(json.dumps(result, ensure_ascii=True))
    except Exception as exc:
        message = str(exc) if type(exc) is ValueError else "Document could not be parsed safely"
        print(json.dumps({"error": message}))
        sys.exit(1)


if __name__ == "__main__":
    main()
