"""Explicit, network-only snapshot builder. No model calls; never overwrites a freeze.

Run from the repository root: python evaluation/build_sqlite_corpus.py
SQLite's documentation is public domain: https://www.sqlite.org/copyright.html
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deepdive.retrieval import PublicHTTP  # noqa: E402


def snapshot(url):
    response = PublicHTTP().get(url)
    soup = BeautifulSoup(response["body"], "html.parser")
    main = soup.select_one(".content") or soup.body
    for element in main.select("script, style, .fancy-toc1, .fancy-toc2"):
        element.decompose()
    for element in main.select("s, del, strike"):
        element.replace_with("[Superseded text: " + element.get_text(" ", strip=True) + "]")
    blocks = []
    for tag in main.find_all(["h1", "h2", "h3", "p", "li", "pre", "table"]):
        if any(p.name in {"p", "li", "pre", "table"} for p in tag.parents if p is not main):
            continue
        if tag.name == "table":
            rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                    for row in tag.find_all("tr")]
            text = "\n".join(" | ".join(row) for row in rows)
        else:
            text = " ".join(tag.get_text(" ", strip=True).split())
        if tag.name.startswith("h"):
            text = "#" * int(tag.name[1]) + " " + text
        if text:
            blocks.append(text)
    text = "\n\n".join(blocks)
    return {"name": url.rsplit("/", 1)[-1].replace(".html", ".md"), "text": text,
            "provenance": {"url": response["url"], "retrieved_at": datetime.now(timezone.utc).isoformat(),
                           "response_sha256": response["sha256"],
                           "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "permission": "SQLite documentation dedicated to the public domain",
                           "permission_url": "https://www.sqlite.org/copyright.html",
                           "extraction": "Heading/paragraph/list/table Markdown; superseded text explicitly labeled"}}


def main():
    output = Path("evaluation/documents.v2.json")
    if output.exists():
        raise SystemExit("Frozen corpus already exists; use a new version to refresh it")
    documents = [snapshot("https://www.sqlite.org/" + name + ".html") for name in ("wal", "lockingv3")]
    documents.append({"name": "unverified-draft.md", "text":
        "# Unverified migration draft\n\nThis is a deliberately incorrect synthetic draft, not SQLite documentation.\n\n"
        "WAL permits simultaneous writers on a network filesystem.\n\n"
        "# Conflicting checklist\n\nMode | Writers | Storage\nWAL | unlimited | shared network drive\n"
        "# Proposed threshold\n\nThe draft proposes checkpointing at 100 pages; this is not an official default.",
        "provenance": {"permission": "Project-authored synthetic negative; MIT repository license",
                       "purpose": "Near-topic conflicting passages and a table, never authoritative reference"}})
    questions = [
        {"id": "single_writer", "split": "development", "question": "How many writers can append to a SQLite WAL at one time?",
         "relevant": ["wal.md"], "relevant_passages": [{"document": "wal.md", "quote": "there can only be one writer at a time"}]},
        {"id": "network", "split": "development", "question": "Can SQLite WAL operate across machines over a network filesystem?",
         "relevant": ["wal.md"], "relevant_passages": [{"document": "wal.md", "quote": "WAL does not work over a network filesystem"}]},
        {"id": "checkpoint", "split": "held_out", "question": "What is the default automatic checkpoint size, in pages, and its compile-time macro?",
         "relevant": ["wal.md"], "relevant_passages": [{"document": "wal.md", "quote": "1000 pages. (The SQLITE_DEFAULT_WAL_AUTOCHECKPOINT compile-time option can be used to specify a different default.)"}]},
        {"id": "reserved", "split": "held_out", "question": "In rollback mode, how does a RESERVED lock differ from a PENDING lock for new readers?",
         "relevant": ["lockingv3.md"], "relevant_passages": [{"document": "lockingv3.md", "quote": "new SHARED locks can be acquired while there is a RESERVED lock"}]},
        {"id": "version", "split": "held_out", "question": "Which version and date made WAL efficient for large transactions?",
         "relevant": ["wal.md"], "relevant_passages": [{"document": "wal.md", "quote": "Beginning with version 3.11.0 (2016-02-15), WAL mode works as efficiently with large transactions as does rollback mode."}]},
        {"id": "conflicting", "split": "held_out", "question": "Compare the draft's simultaneous-writer claim with the official WAL rule.",
         "relevant": ["wal.md", "unverified-draft.md"], "relevant_passages": [
             {"document": "wal.md", "quote": "there can only be one writer at a time"},
             {"document": "unverified-draft.md", "quote": "WAL permits simultaneous writers on a network filesystem."}]},
        {"id": "absent", "split": "held_out", "question": "What is the maximum PostgreSQL replication slot count in version 18?",
         "relevant": [], "relevant_passages": []},
    ]
    result = {"version": "2.0", "label_status": "authored exact-quotation relevance; independent review pending",
              "independent_review": {"status": "pending"}, "documents": documents, "questions": questions}
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"documents": len(documents), "characters": sum(len(d["text"]) for d in documents),
                      "questions": len(questions), "ai_calls": 0}))


if __name__ == "__main__":
    main()
