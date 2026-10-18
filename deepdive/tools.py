"""Typed, role-restricted tools. Agents cannot publish, mutate memory, or execute code."""
import http.client
from itertools import zip_longest
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote, urlsplit
from pydantic import Field

from .guardrails import cache_key, redact
from .models import StrictModel
from .retrieval import PublicHTTP, extract_page, search_public, split_passages
from .storage import digest


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)


class FetchArgs(StrictModel):
    url: str = Field(min_length=1, max_length=4096)


class PassageArgs(StrictModel):
    passage_ids: list[str] = Field(min_length=1, max_length=12)


class TableArgs(StrictModel):
    source_id: str
    table_index: int = Field(ge=0, le=7)


class Operand(StrictModel):
    passage_id: str
    value: float = Field(allow_inf_nan=False)
    literal: str = Field(min_length=1, max_length=40)


class CalculateArgs(StrictModel):
    operation: Literal["add", "subtract", "multiply", "divide", "percent_change"]
    inputs: list[Operand] = Field(min_length=2, max_length=2)


class MarketArgs(StrictModel):
    symbol: str = Field(pattern=r"^[A-Z0-9^][A-Z0-9.\-^=]{0,14}$")
    period: Literal["1mo", "3mo", "6mo", "1y"] = "3mo"


SCHEMAS = {"search_web": SearchArgs, "fetch_source": FetchArgs, "search_evidence": SearchArgs,
           "read_passages": PassageArgs, "read_table": TableArgs, "calculate": CalculateArgs,
           "recall_memory": SearchArgs, "fetch_market_series": MarketArgs}
PERMISSIONS = {
    "researcher": set(SCHEMAS),
    "analyst": {"search_evidence", "read_passages", "read_table", "calculate"},
    "verifier": {"read_passages", "read_table"},
}


def quality(url):
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith((".gov", ".edu", ".gov.uk")) or host in {"arxiv.org", "www.nature.com"} or host.startswith("docs."):
        return {"rank": 1, "label": "primary candidate", "reason": "Government, academic, or documentation domain; verify authorship"}
    if any(host == d or host.endswith("." + d) for d in ["reddit.com", "quora.com", "stackoverflow.com", "news.ycombinator.com"]):
        return {"rank": 3, "label": "community", "reason": "User-contributed discussion; opinions require attribution"}
    return {"rank": 2, "label": "unclassified web source", "reason": "Authorship and primary status require inspection"}


class ToolBox:
    def __init__(self, store, memory, context, run, http=None):
        self.store, self.memory, self.context, self.run = store, memory, context, run
        self.scope = run["scope"]
        self.http = http or PublicHTTP()
        self.locks = {}
        self.lock = threading.RLock()
        self.document_search = None

    def allow_network(self):
        if self.run["request"].get("research_mode") == "documents":
            raise ValueError("Public network tools are disabled in document-only research")

    def schemas(self, role="researcher"):
        names = PERMISSIONS[role]
        if self.run["request"].get("research_mode") == "documents":
            names = names - {"search_web", "fetch_source", "fetch_market_series"}
        return {name: SCHEMAS[name].model_json_schema() for name in sorted(names)}

    def call(self, role, name, arguments):
        self.context.check()
        started = time.monotonic()
        try:
            if name not in PERMISSIONS.get(role, set()):
                raise ValueError("Tool is not permitted for this role")
            args = SCHEMAS[name].model_validate(arguments)
            result = getattr(self, name)(args)
            self.store.event(self.run["id"], "tool_result", role=role, tool=name,
                             arguments=arguments, seconds=round(time.monotonic() - started, 3), outcome="ok")
            return result
        except (ValueError, KeyError, TypeError, IndexError, OSError, http.client.HTTPException) as exc:
            self.store.event(self.run["id"], "tool_result", role=role, tool=name,
                             arguments=arguments, seconds=round(time.monotonic() - started, 3), outcome="error", error=str(exc))
            return {"error": redact(str(exc)), "tool": name}

    def _cached(self, kind, arguments, ttl, operation):
        key = cache_key(kind, scope=self.scope, **arguments)
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            value = None if self.run["request"]["refresh"] else self.store.cached(self.scope, key)
            if value is not None:
                self.store.event(self.run["id"], "cache_hit", cache=kind, key=key)
                return value
            value = operation()
            self.store.cache(self.scope, key, value, ttl)
            return value

    def search_web(self, args):
        self.allow_network()
        return self._cached("search", {"query": args.query.strip(), "provider": "ddg-html-lite"}, 3600,
                            lambda: search_public(self.http, args.query.strip()))

    def _attach(self, source):
        with self.store.lock:
            run = self.store.get("run", self.run["id"])
            ids = sorted(set(run.get("source_ids", [])) | {source["id"]})
            if len(ids) > self.context.limits.sources:
                raise ValueError("Source limit reached")
            self.store.update("run", run["id"], source_ids=ids)
        return {"source_id": source["id"], "title": source["title"], "url": source.get("final_url"),
                "quality": source["quality"], "passages": [p for p in self.store.list("passage", self.scope)
                                                         if p["source_id"] == source["id"]][:4],
                "table_count": len(source.get("tables", []))}

    def fetch_source(self, args):
        self.allow_network()
        ttl = 900 if self.run.get("time_sensitive") else 86400
        key = cache_key("page", url=args.url, scope=self.scope, ttl=ttl)
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            run = self.store.get("run", self.run["id"])
            visited = run.get("visited_urls", [])
            if args.url not in visited:
                if len(visited) >= self.context.limits.sources:
                    raise ValueError("Source limit reached")
                with self.store.lock:
                    run = self.store.get("run", run["id"])
                    visited = run.get("visited_urls", [])
                    if args.url not in visited:
                        if len(visited) >= self.context.limits.sources:
                            raise ValueError("Source limit reached")
                        self.store.update("run", run["id"], visited_urls=visited + [args.url])
            cached = None if self.run["request"]["refresh"] else self.store.cached(self.scope, key)
            source = self.store.get("source", cached["source_id"], self.scope) if cached else None
            if source:
                self.store.event(run["id"], "cache_hit", cache="page", source_id=source["id"])
                return self._attach(source)
            # A refresh fetches each URL once per run, not once per agent.
            for sid in run.get("source_ids", []):
                source = self.store.get("source", sid, self.scope)
                if source and source["requested_url"] == args.url and source.get("owner_run") == run["id"]:
                    return self._attach(source)
            response = self.http.get(args.url)
            extraction_key = cache_key("extraction", hash=response["sha256"], extractor="2", scope=self.scope)
            extracted = self.store.cached(self.scope, extraction_key)
            if extracted is None:
                extracted = extract_page(response)
                self.store.cache(self.scope, extraction_key, extracted, 86400)
            if not extracted["text"].strip():
                raise ValueError("No readable evidence in source")
            sid = "source_" + digest([self.scope, args.url, response["sha256"], response["retrieved_at"]])[:24]
            source = {"id": sid, "owner_run": run["id"], "requested_url": args.url, "final_url": response["url"],
                      "retrieved_at": response["retrieved_at"], "fresh_until": response["retrieved_at"] + ttl,
                      "content_hash": response["sha256"], "content_type": response["content_type"], "status": "retrieved",
                      "quality": quality(response["url"]), **extracted}
            self.store.put("source", source, self.scope)
            for index, text in enumerate(split_passages(extracted["text"])):
                self.store.put("passage", {"id": f"{sid}_p{index}", "source_id": sid,
                                          "text": text, "location": f"text:{index}", "kind": "text"}, self.scope)
            for table in source["tables"]:
                self.store.put("passage", {"id": f"{sid}_t{table['index']}", "source_id": sid,
                                          "text": json.dumps(table, ensure_ascii=False), "location": f"table:{table['index']}",
                                          "kind": "table"}, self.scope)
            self.store.cache(self.scope, key, {"source_id": sid}, ttl)
            return self._attach(source)

    def _allowed(self, passage):
        if not passage:
            return False
        source = self.store.get("source", passage["source_id"], self.scope)
        return source and source["fresh_until"] > time.time() and source["id"] in self.store.get("run", self.run["id"]).get("source_ids", [])

    def search_evidence(self, args):
        documents = self.document_search(args.query) if self.document_search and self.run.get("document_source_ids") else []
        web = [p for p in self.store.search(self.scope, args.query, limit=100)
               if self._allowed(p) and p["source_id"] not in self.run.get("document_source_ids", [])
               and (self.run["request"].get("research_mode") != "documents" or p.get("kind") == "calculation")][:12]
        return [p for pair in zip_longest(documents, web) for p in pair if p][:12]

    def read_passages(self, args):
        passages = [self.store.get("passage", pid, self.scope) for pid in args.passage_ids]
        if not all(self._allowed(p) for p in passages):
            raise ValueError("Passage is missing, stale, or outside this run")
        return passages

    def read_table(self, args):
        source = self.store.get("source", args.source_id, self.scope)
        if not source or source["id"] not in self.store.get("run", self.run["id"]).get("source_ids", []):
            raise ValueError("Source is outside this run")
        return self.read_passages(PassageArgs(passage_ids=[f"{source['id']}_t{args.table_index}"]))

    def recall_memory(self, args):
        return self.memory.recall(self.scope, args.query)

    def calculate(self, args):
        passages = self.read_passages(PassageArgs(passage_ids=[i.passage_id for i in args.inputs]))
        for operand, passage in zip(args.inputs, passages):
            if operand.literal not in passage["text"] or float(operand.literal.replace(",", "").rstrip("%")) != operand.value:
                raise ValueError("Calculation operands must match literal source values")
        a, b = [i.value for i in args.inputs]
        if args.operation in {"divide", "percent_change"} and (b if args.operation == "divide" else a) == 0:
            raise ValueError("Division by zero")
        result = {"add": lambda: a + b, "subtract": lambda: a - b, "multiply": lambda: a * b,
                  "divide": lambda: a / b, "percent_change": lambda: (b - a) / a * 100}[args.operation]()
        if not math.isfinite(result):
            raise ValueError("Nonfinite calculation")
        sid = "source_" + digest([self.scope, args.model_dump()])[:24]
        source = self.store.get("source", sid, self.scope)
        if source is None:
            sources = [self.store.get("source", p["source_id"], self.scope) for p in passages]
            text = f"Calculation: {args.operation}; inputs {a}, {b}; result {result:.10g}. Inputs: " + ", ".join(i.passage_id for i in args.inputs)
            source = {"id": sid, "owner_run": self.run["id"], "requested_url": "", "final_url": "",
                      "title": "Derived calculation", "text": text, "kind": "calculation", "status": "retrieved",
                      "retrieved_at": time.time(), "fresh_until": min(s["fresh_until"] for s in sources),
                      "content_hash": digest(text), "quality": {"rank": 1, "label": "derived", "reason": "Code calculation; input meanings still require verification"},
                      "inputs": args.model_dump(), "tables": []}
            self.store.put("source", source, self.scope)
            self.store.put("passage", {"id": sid + "_p0", "source_id": sid, "text": text,
                                      "location": "calculation", "kind": "calculation"}, self.scope)
        return self._attach(source)

    def fetch_market_series(self, args):
        self.allow_network()
        question = self.run["request"]["question"]
        if not re.search(r"\b(stock|share|market|ticker|price|equity|invest|trading|finance)\w*\b", question, re.I):
            raise ValueError("Market retrieval requires a market-related question")
        # Require explicit user symbol. Never infer the first short word or default to AAPL.
        if not re.search(r"(?<![\w])\$?" + re.escape(args.symbol) + r"(?![\w])", question):
            raise ValueError("Specify the ticker explicitly in the question, e.g. $MSFT")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(args.symbol, safe='')}?range={args.period}&interval=1d"
        def fetch():
            response = self.http.get(url)
            payload = json.loads(response["body"])
            if payload.get("chart", {}).get("error") or not payload.get("chart", {}).get("result"):
                raise ValueError("Market provider has no data for this instrument")
            result = payload["chart"]["result"][0]
            if result["meta"]["symbol"].upper() != args.symbol:
                raise ValueError("Provider returned a different instrument")
            closes = result["indicators"]["quote"][0]["close"]
            rows = [[datetime.fromtimestamp(t, timezone.utc).date().isoformat(), float(c)]
                    for t, c in zip(result["timestamp"], closes) if c is not None and math.isfinite(c)]
            if not rows:
                raise ValueError("No market series")
            return {"rows": rows, "currency": result["meta"]["currency"], "retrieved_at": response["retrieved_at"],
                    "hash": response["sha256"]}
        # Count market responses against the same source ceiling.
        with self.store.lock:
            run = self.store.get("run", self.run["id"])
            if url not in run.get("visited_urls", []):
                if len(run.get("visited_urls", [])) >= self.context.limits.sources:
                    raise ValueError("Source limit reached")
                self.store.update("run", run["id"], visited_urls=run.get("visited_urls", []) + [url])
        data = self._cached("market", {"symbol": args.symbol, "period": args.period}, 300, fetch)
        sid = "source_" + digest([self.scope, url, data["retrieved_at"], data["hash"]])[:24]
        source = self.store.get("source", sid, self.scope)
        if source is None:
            table = {"index": 0, "rows": [["Date (UTC)", f"Close ({data['currency']})"]] + data["rows"],
                     "caption": f"{args.symbol} daily closing prices; {args.period}"}
            text = json.dumps(table)
            source = {"id": sid, "owner_run": run["id"], "requested_url": url, "final_url": url,
                      "title": table["caption"], "text": text, "content_hash": data["hash"],
                      "retrieved_at": data["retrieved_at"], "fresh_until": data["retrieved_at"] + 300,
                      "status": "retrieved", "kind": "market", "tables": [table], "currency": data["currency"],
                      "quality": {"rank": 2, "label": "market data", "reason": "Yahoo chart response; delayed or unavailable data possible"}}
            self.store.put("source", source, self.scope)
            self.store.put("passage", {"id": sid + "_t0", "source_id": sid, "text": text,
                                      "location": "table:0", "kind": "table"}, self.scope)
        return self._attach(source)
