"""Optional persistent spending guard for explicitly started live evaluations."""
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import httpx
from .config import MESH_BASE_URL
from .gateway import BudgetExceeded


def money(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("Money must be a finite nonnegative decimal")
    return number


def metadata(settings):
    """Read only billing/model metadata; never generate content."""
    with httpx.Client(base_url=MESH_BASE_URL + "/",
                      headers={"Authorization": "Bearer " + settings.mesh_key}, timeout=30) as client:
        response = client.get("balance")
        response.raise_for_status()
        balance = money(response.json()["available_usd"])
        prices = {}
        for model in {settings.model, settings.embedding_model}:
            response = client.get("models/" + model)
            response.raise_for_status()
            pricing = response.json()["pricing"]
            if pricing["pricing_unit"] != "per_1m_tokens":
                raise ValueError("Evaluation spending guard requires token-based pricing")
            # Text-only requests do not use image/audio/batch/flex dimensions.
            for field in ("long_context_input_usd_per_1m", "long_context_output_usd_per_1m",
                          "cache_write_input_usd_per_1m", "cache_write_1h_input_usd_per_1m",
                          "reasoning_output_usd_per_1m"):
                if pricing.get(field) is not None:
                    raise ValueError("This pricing profile requires additional budget accounting")
            prices[model] = {"input": str(money(pricing["prompt_usd_per_1m"])),
                             "output": str(money(pricing["completion_usd_per_1m"])),
                             "request": str(money(pricing.get("request_usd") or 0)),
                             "effective_date": pricing.get("effective_date")}
    return balance, prices


def available_balance(settings):
    with httpx.Client(headers={"Authorization": "Bearer " + settings.mesh_key}, timeout=30) as client:
        response = client.get(MESH_BASE_URL + "/balance")
        response.raise_for_status()
        return money(response.json()["available_usd"])


class SpendLedger:
    """One evaluation process owns a ledger; threads reserve atomically.

    Failed/unknown attempts retain their full reservation. Reported token usage
    releases only the unused estimate. This is not a provider billing statement.
    """
    def __init__(self, path, ceiling, reserve, prices, balance_reader):
        self.path = Path(path)
        self.ceiling, self.reserve = money(ceiling), money(reserve)
        if not self.ceiling or not self.reserve:
            raise ValueError("Set a positive evaluation ceiling and demo reserve")
        self.prices, self.balance_reader = prices, balance_reader
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents two processes sharing this ledger and overspending.
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            self.owner = self.lock_path.open("x")
        except FileExistsError:
            raise ValueError("Evaluation ledger is owned or interrupted; inspect its lock before recovery")
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if (money(self.data["ceiling_usd"]) != self.ceiling
                        or money(self.data["reserve_usd"]) != self.reserve):
                    raise ValueError("Existing ledger allocation cannot be changed")
            else:
                self.data = {"ceiling_usd": str(self.ceiling), "reserve_usd": str(self.reserve),
                             "created_at": datetime.now(timezone.utc).isoformat(), "attempts": []}
            self.save()
        except BaseException:
            self.close()
            raise

    def save(self):
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def estimate(self, model, prompt_tokens, output_tokens):
        price = self.prices[model]
        # Extra margin protects rounding; never assume cache discounts.
        return (money(prompt_tokens) * money(price["input"]) / 1_000_000
                + money(output_tokens) * money(price["output"]) / 1_000_000
                + money(price["request"])) * Decimal("1.10")

    def reserve_request(self, model, input_bound, output_limit):
        with self.lock:
            cost = self.estimate(model, input_bound, output_limit)
            spent = sum((money(a["accounted_usd"]) for a in self.data["attempts"]), Decimal(0))
            if spent + cost > self.ceiling:
                raise BudgetExceeded("Evaluation spending ceiling reached; demo reserve preserved")
            inflight = sum((money(a["accounted_usd"]) for a in self.data["attempts"]
                            if a["status"] == "reserved"), Decimal(0))
            if money(self.balance_reader()) - inflight - cost < self.reserve:
                raise BudgetExceeded("Current Mesh balance cannot preserve the demo reserve")
            number = len(self.data["attempts"])
            self.data["attempts"].append({"model": model, "input_bound": input_bound,
                                         "output_limit": output_limit, "accounted_usd": str(cost),
                                         "status": "reserved", "pricing": self.prices[model]})
            self.save()
            return number

    def settle(self, number, usage):
        with self.lock:
            item = self.data["attempts"][number]
            prompt, output = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if output is None and item["output_limit"] == 0:
                output = 0
            if not isinstance(prompt, int) or not isinstance(output, int) or prompt < 0 or output < 0:
                # Missing accounting is never treated as zero spend.
                item["status"] = "unknown_usage"
            else:
                cost = self.estimate(item["model"], prompt, output)
                item.update(status="token_estimate", accounted_usd=str(cost),
                            prompt_tokens=prompt, completion_tokens=output)
            self.save()

    def summary(self):
        with self.lock:
            return {"ceiling_usd": str(self.ceiling), "demo_reserve_usd": str(self.reserve),
                    "accounted_upper_estimate_usd": str(sum(
                        (money(a["accounted_usd"]) for a in self.data["attempts"]), Decimal(0))),
                    "attempts": len(self.data["attempts"]),
                    "unknown_attempts": sum(a["status"] in {"reserved", "unknown_usage"} for a in self.data["attempts"]),
                    "meaning": "Conservative request reservations/token estimates, not measured provider charges"}

    def close(self):
        self.owner.close()
        self.lock_path.unlink(missing_ok=True)
