"""Document library, immutable evidence, and structure-preserving chunking."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .document_parser import MAX_BYTES
from .gateway import encoding, token_count
from .guardrails import redact
from .storage import digest

CHUNK_VERSION = "structure-384-overlap64-v1"
PARSER_VERSION = "pdf-docx-text-v1"
MAX_CHUNKS = 128
MAX_SELECTED_CHUNKS = 512
PARSER_SLOT = threading.BoundedSemaphore(1)


def chunks(blocks):
    """Contiguous character slices; boundaries prefer paragraphs, then sentences.

    Never cross a page/section. Exact offsets refer to the redacted extracted block,
    not PDF binary offsets. Tokenization controls size, never decodes partial UTF-8.
    """
    tokenizer = encoding()
    count = (lambda text: len(tokenizer.encode(text, disallowed_special=()))) if tokenizer else token_count
    for block_index, block in enumerate(blocks):
        text = redact(block["text"])
        start = 0
        while start < len(text):
            low, high = start + 1, min(len(text), start + 6000)
            while low <= high:
                middle = (low + high) // 2
                if count(text[start:middle]) <= 384:
                    low = middle + 1
                else:
                    high = middle - 1
            end = max(start + 1, high)
            if end < len(text):
                breaks = [m.end() for m in re.finditer(r"\n\s*\n|(?<=[.!?])\s+|\n", text[start:end])]
                candidates = [b for b in breaks if b >= (end - start) // 2]
                if candidates:
                    end = start + candidates[-1]
            value = text[start:end]
            if value.strip():
                location = f"page {block['page']}" if block.get("page") else f"section {block_index + 1}"
                if block.get("section"):
                    location += ": " + redact(block["section"])
                yield {"text": value, "page": block.get("page"), "section": redact(block.get("section", "")),
                       "block": block_index, "start": start, "end": end, "tokens": count(value),
                       "location": f"{location}, characters {start}–{end}"}
            if end >= len(text):
                break
            overlap = end
            # Overlap whole words where possible, with a strict 64-token bound.
            for match in reversed(list(re.finditer(r"\S+", text[start:end]))):
                candidate = start + match.start()
                if count(text[candidate:end]) > 64:
                    break
                overlap = candidate
            start = max(start + 1, overlap if overlap > start else end)


class Documents:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
                    id UNINDEXED, scope UNINDEXED, source_id UNINDEXED, title, section, text,
                    tokenize='unicode61');
                CREATE TABLE IF NOT EXISTS document_vectors (
                    id TEXT NOT NULL, scope TEXT NOT NULL, profile TEXT NOT NULL,
                    dimensions INTEGER NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(id, profile));
                CREATE INDEX IF NOT EXISTS document_vectors_scope ON document_vectors(scope, profile);
            """)

    def list(self, scope):
        return [d for d in self.store.list("document", scope) if d["active"]]

    def selected(self, scope, ids):
        result = [self.store.get("document", doc_id, scope) for doc_id in ids]
        if any(not d or not d["active"] for d in result):
            raise ValueError("Selected document is missing, removed, or outside this session/project")
        if sum(d["chunk_count"] for d in result) > MAX_SELECTED_CHUNKS:
            raise ValueError("Select at most 512 document chunks per run")
        return result

    def ingest(self, scope, filename, data):
        if (not filename or len(filename) > 180 or Path(filename).name != filename
                or any(c in filename for c in '\\/:\x00') or any(ord(c) < 32 for c in filename)):
            raise ValueError("Use a plain filename without paths or control characters")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("Supported documents: PDF, DOCX, TXT, and Markdown")
        if not data or len(data) > MAX_BYTES:
            raise ValueError("Upload must contain 1 byte to 5 MB")
        content_hash = hashlib.sha256(data).hexdigest()
        identifier = "document_" + digest([scope, content_hash, suffix, PARSER_VERSION, CHUNK_VERSION])[:24]
        with PARSER_SLOT:
            existing = self.store.get("document", identifier, scope)
            if existing and existing["active"]:
                return existing
            if len(self.list(scope)) >= 50:
                raise ValueError("Document library limit is 50 active documents per scope")
            try:
                parsed = subprocess.run([sys.executable, "-m", "deepdive.document_parser", suffix],
                    input=data, capture_output=True, timeout=30,
                    cwd=Path(__file__).resolve().parent.parent,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            except subprocess.TimeoutExpired:
                raise ValueError("Document parsing exceeded 30 seconds") from None
            try:
                result = json.loads(parsed.stdout)
            except (ValueError, UnicodeError):
                raise ValueError("Document parser exceeded its limits or returned invalid output") from None
            if parsed.returncode or "error" in result:
                raise ValueError(result.get("error", "Document could not be parsed safely"))
            passages = list(chunks(result["blocks"]))
            if not passages or len(passages) > MAX_CHUNKS:
                raise ValueError("Document must contain 1–128 chunks; split larger documents before upload")
            if sum(d["chunk_count"] for d in self.list(scope)) + len(passages) > 2048:
                raise ValueError("Document library limit is 2,048 active chunks per scope")
            sid = "source_" + identifier.removeprefix("document_")
            now = time.time()
            document = {"id": identifier, "source_id": sid, "name": redact(filename), "content_hash": content_hash,
                        "bytes": len(data), "chunk_count": len(passages), "chunk_version": CHUNK_VERSION,
                        "parser_version": PARSER_VERSION,
                        "warnings": result["warnings"], "active": True, "created": now}
            source = {"id": sid, "document_id": identifier, "title": redact(filename), "kind": "document",
                      "requested_url": "", "final_url": "", "retrieved_at": now, "fresh_until": 1e20,
                      "content_hash": content_hash, "content_type": suffix, "status": "uploaded",
                      "quality": {"rank": 2, "label": "uploaded document", "reason": "User-supplied evidence; authorship and claims require verification"},
                      "text": "\n\n".join(redact(b["text"]) for b in result["blocks"]),
                      "tables": [], "media": [], "warnings": result["warnings"], "created": now}
            records = [("document", document), ("source", source)]
            records += [("passage", {**p, "id": f"{sid}_p{i}", "source_id": sid, "kind": "text", "created": now})
                        for i, p in enumerate(passages)]
            with self.store.lock, self.store.connection() as db:
                if existing:
                    # Reactivating identical content preserves historical source/offset identity.
                    document = {**existing, "active": True}
                    db.execute("UPDATE records SET body=? WHERE id=?", (json.dumps(document), identifier))
                else:
                    for kind, item in records:
                        db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (kind, item["id"], scope, now, json.dumps(item)))
                        if kind == "passage":
                            db.execute("INSERT INTO search VALUES(?,?,?,?)", (item["id"], scope, kind, item["text"]))
                            db.execute("INSERT INTO document_search VALUES(?,?,?,?,?,?)",
                                       (item["id"], scope, sid, source["title"], item["section"], item["text"]))
                db.execute("DELETE FROM cache WHERE scope=?", (scope,))
            return document

    def remove(self, scope, document_id):
        doc = self.store.get("document", document_id, scope)
        if not doc:
            raise KeyError(document_id)
        self.store.update("document", document_id, active=False)
        with self.store.connection() as db:
            db.execute("DELETE FROM document_vectors WHERE scope=? AND id IN (SELECT id FROM document_search WHERE source_id=?)",
                       (scope, doc["source_id"]))
        self.store.clear_cache(scope)
        return {"removed": True, "historical_evidence_retained": True}

    def purge_source(self, scope, source_id):
        with self.store.connection() as db:
            db.execute("DELETE FROM document_vectors WHERE scope=? AND id IN (SELECT id FROM document_search WHERE source_id=?)",
                       (scope, source_id))
            db.execute("DELETE FROM document_search WHERE scope=? AND source_id=?", (scope, source_id))
        for doc in self.store.list("document", scope):
            if doc["source_id"] == source_id:
                self.store.delete("document", doc["id"], scope)
