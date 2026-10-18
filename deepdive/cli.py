"""Command line client. The API process owns execution and persistence."""
import argparse
import json
import time
import uuid
from pathlib import Path
from .client import Client

TERMINAL = {"completed", "insufficient", "failed", "stopped", "cancelled", "limited", "interrupted"}


def main():
    parser = argparse.ArgumentParser(description="DeepDive local research client")
    parser.add_argument("--api-url", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    research = sub.add_parser("research", help="Start research")
    research.add_argument("question")
    research.add_argument("--session")
    research.add_argument("--project")
    research.add_argument("--source", action="append", default=[])
    research.add_argument("--document", action="append", default=[], help="Document ID from upload/list; repeat to select")
    research.add_argument("--mode", choices=["web", "documents", "mixed"], default="web")
    research.add_argument("--auto", action="store_true", help="Explicitly skip manual review")
    research.add_argument("--refresh", action="store_true")
    research.add_argument("--wait", action="store_true")
    for name in ["status", "events", "cancel", "resume", "export", "review", "download"]:
        p = sub.add_parser(name)
        p.add_argument("run_id")
        if name == "review":
            p.add_argument("decision", choices=["approve", "revise", "stop"])
            p.add_argument("--feedback", default="")
        if name == "export":
            p.add_argument("--output", required=True)
        if name == "download":
            p.add_argument("artifact_id")
            p.add_argument("--output", required=True)
    sub.add_parser("health")
    sub.add_parser("sessions")
    for name in ["upload", "documents", "remove-document"]:
        p = sub.add_parser(name)
        selected_scope = p.add_mutually_exclusive_group(required=True)
        selected_scope.add_argument("--session")
        selected_scope.add_argument("--project")
        if name == "upload":
            p.add_argument("file")
        if name == "remove-document":
            p.add_argument("document_id")
    args = parser.parse_args()
    client = Client(args.api_url)
    try:
        if args.command == "research":
            result = client.request("POST", "/runs", json={"question": args.question, "session_id": args.session,
                "project_id": args.project, "source_urls": args.source, "review_mode": "auto" if args.auto else "manual",
                "document_ids": args.document, "research_mode": args.mode,
                "refresh": args.refresh}, headers={"Idempotency-Key": uuid.uuid4().hex})
            print("Run:", result["id"])
            if args.wait:
                last_stage = None
                while result["status"] not in TERMINAL | {"waiting_review"}:
                    if result["stage"] != last_stage:
                        print(result["stage"], flush=True)
                        last_stage = result["stage"]
                    time.sleep(1)
                    result = client.request("GET", "/runs/" + result["id"])
        elif args.command in {"upload", "documents", "remove-document"}:
            scope = {"session_id": args.session} if args.session else {"project_id": args.project}
            if args.command == "upload":
                path = Path(args.file)
                if path.stat().st_size > 5_000_000:
                    raise ValueError("Upload exceeds 5 MB")
                result = client.upload(path.name, path.read_bytes(), **scope)
            elif args.command == "documents":
                result = client.request("GET", "/documents", params=scope)
            else:
                result = client.request("DELETE", "/documents/" + args.document_id, params=scope)
        elif args.command in {"health", "sessions"}:
            result = client.request("GET", "/" + args.command)
        elif args.command == "review":
            result = client.request("POST", f"/runs/{args.run_id}/review",
                json={"decision": args.decision, "feedback": args.feedback}, headers={"Idempotency-Key": uuid.uuid4().hex})
        elif args.command in {"cancel", "resume"}:
            result = client.request("POST", f"/runs/{args.run_id}/{args.command}")
        elif args.command == "download":
            Path(args.output).write_bytes(client.download(args.run_id, args.artifact_id))
            result = {"saved": args.output}
        else:
            suffix = "" if args.command == "status" else "/" + args.command
            result = client.request("GET", f"/runs/{args.run_id}{suffix}")
            if args.command == "export":
                Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                result = {"saved": args.output}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
