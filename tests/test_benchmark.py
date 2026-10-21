import json
from decimal import Decimal
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from deepdive.benchmark import benchmark
from deepdive.config import Settings
from deepdive.gateway import BudgetExceeded
from deepdive.models import Limits
from deepdive.service import ResearchService
from deepdive.spending import SpendLedger
from .fakes import FixtureGateway, FixtureHTTP


PRICES = {"fixture": {"input": "10", "output": "20", "request": "0", "effective_date": "fixture"}}


def test_spend_reservations_include_concurrent_attempts_failures_and_restart(tmp_path):
    path = tmp_path / "spend.json"
    ledger = SpendLedger(path, ".10", "1", PRICES, lambda: Decimal("2"))
    def request(_):
        try:
            return ledger.reserve_request("fixture", 1000, 1000)
        except BudgetExceeded:
            return None
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            reserved = [n for n in pool.map(request, range(12)) if n is not None]
        assert len(reserved) == 3  # Each reserves $0.033; concurrent attempts cannot exceed $0.10.
        ledger.settle(reserved[0], {"prompt_tokens": 100, "completion_tokens": 100})
        ledger.settle(reserved[1], {})  # Missing usage retains the full estimate.
        assert ledger.summary()["unknown_attempts"] == 2
        assert Decimal(ledger.summary()["accounted_upper_estimate_usd"]) == Decimal(".0693")
        with pytest.raises(ValueError, match="owned"):
            SpendLedger(path, ".10", "1", PRICES, lambda: 2)
    finally:
        ledger.close()
    ledger = SpendLedger(path, ".10", "1", PRICES, lambda: Decimal("1.01"))
    try:
        with pytest.raises(BudgetExceeded, match="demo reserve"):
            ledger.reserve_request("fixture", 100, 100)
        assert len(ledger.data["attempts"]) == 3
    finally:
        ledger.close()


def test_benchmark_recovers_completed_turn_without_repeating_and_keeps_sessions_isolated(tmp_path):
    output = tmp_path / "result.json"
    dataset = {"version": "test", "questions": [
        {"id": "follow_up", "turns": [{"question": "Study participants"}, {"question": "What was its follow-up?"}]},
        {"id": "heldout", "question": "Not selected"}]}
    settings = Settings(tmp_path / "application")
    ledger = SimpleNamespace(summary=lambda: {"fixture": True})
    gateways = []
    def factory(config):
        gateway = FixtureGateway()
        gateways.append(gateway)
        return ResearchService(config, gateway=gateway, http=FixtureHTTP())
    def interrupted_factory(config):
        service = factory(config)
        execute = service.execute
        def interrupt_after_execution(run_id):
            execute(run_id)
            raise RuntimeError("Fixture interruption before output checkpoint")
        service.execute = interrupt_after_execution
        return service
    with pytest.raises(RuntimeError, match="Fixture interruption"):
        benchmark(dataset, settings, output, Limits(), ["follow_up"], ledger=ledger, factory=interrupted_factory)
    state = json.loads(output.read_text())
    first_run = state["cases"]["follow_up"]["run_ids"][0]
    result = benchmark(dataset, settings, output, Limits(), ["follow_up"], True, ledger, factory)
    entry = result["cases"]["follow_up"]
    assert entry["run_ids"][0] == first_run
    assert len(entry["run_ids"]) == 2 and len(entry["metrics"]) == 2
    assert gateways[-1].roles.count("planner") == 1  # Only the second turn executed.
    assert result["not_run"] == ["heldout"] and result["status"] == "partial"
    assert not (settings.data / "research.sqlite3").exists()
    result2 = benchmark(dataset, settings, output, Limits(), ["follow_up"], True, ledger, factory)
    assert result2["cases"] == result["cases"] and gateways[-1].roles == []
    with pytest.raises(ValueError, match="changed"):
        benchmark(dataset, settings, output, Limits(model_calls=25), ["follow_up"], True, ledger, factory)
