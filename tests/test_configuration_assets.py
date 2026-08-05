"""Tests for versioned discovery and local-runtime contracts."""

import json
import tomllib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parents[1]


def _toml(name: str) -> dict[str, Any]:
    """Load a repository TOML contract."""
    with (_ROOT / "config" / name).open("rb") as stream:
        return tomllib.load(stream)


def _json(name: str) -> dict[str, Any]:
    """Load a repository JSON contract."""
    return json.loads((_ROOT / "config" / name).read_text(encoding="utf-8"))


def test_source_registry_is_explicit_and_default_deny() -> None:
    """Selected sources implement the approved internal-pilot action boundary."""
    registry = _toml("source-registry.toml")
    sources = registry["sources"]
    assert registry["default_action"] == "deny"
    assert {source["code"] for source in sources} == {
        "repec",
        "crossref",
        "core",
        "boston_fed",
        "board_fomc",
    }
    assert all(source["enabled"] is True for source in sources)
    assert all(source["review_status"] == "approved_internal_pilot" for source in sources)
    assert all(source["actions"]["fetch_content"] == "allow_internal" for source in sources)
    assert all(source["actions"]["export_full_text"] == "deny" for source in sources)
    assert all(source["actions"]["redistribute"] == "deny" for source in sources)


def test_source_cards_cover_every_approved_connector() -> None:
    """Each enabled source has a reviewed acquisition entry point and limitations."""
    registry = _toml("source-registry.toml")
    cards = _toml("source-cards.toml")["cards"]
    assert {card["source_code"] for card in cards} == {source["code"] for source in registry["sources"]}
    assert all(card["entry_points"] and card["known_limitations"] for card in cards)


def test_temporal_policy_excludes_unknown_and_learned_inputs() -> None:
    """Strict cutoff semantics are fixed before a search implementation exists."""
    policy = _toml("temporal-policy.toml")
    assert policy["mode"] == "strict"
    assert policy["eligibility"]["allowed_safety_classes"] == ["T0", "T1"]
    assert policy["eligibility"]["unknown_availability"] == "exclude"
    assert policy["learned_artifacts"]["strict_mode_behavior"] == "exclude_unless_certified"


def test_local_profile_targets_reference_mac_without_proprietary_runtime() -> None:
    """The checked-in resource envelope matches the requested initial host."""
    profile = _toml("local.toml")
    assert profile["profile"] == "local"
    assert profile["memory"]["system_reserve_gib"] >= 8
    assert profile["concurrency"]["heavy_pipeline_jobs"] == 1
    assert profile["acceptance"] == {"document_versions": 1000, "evidence_units": 50000}
    assert profile["runtime"]["proprietary_runtime_required"] is False


def test_compose_api_healthcheck_uses_authoritative_readiness_endpoint() -> None:
    """Container health reflects the same migration, index, and capacity gate as the API."""
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "urllib.request.urlopen('http://127.0.0.1:8000/health/ready'" in compose


def test_benchmark_plan_is_balanced_and_has_a_held_out_split() -> None:
    """The pilot benchmark fixes task allocation before retrieval optimization."""
    plan = _json("benchmark-plan.json")
    assert sum(item["count"] for item in plan["task_strata"]) == plan["minimum_task_count"] == 60
    assert len(plan["topic_balance"]) == 5
    assert sum(plan["split_policy"][name] for name in ("development", "validation", "test")) == 1
    assert plan["split_policy"]["test"] > 0


def test_json_schemas_are_versioned_and_closed() -> None:
    """Benchmark and evidence payloads reject unversioned extra fields by contract."""
    benchmark = _json("schemas/benchmark-task-v1.schema.json")
    evidence = _json("schemas/evidence-record-v1.schema.json")
    assert benchmark["$id"] == "urn:eeg:schema:benchmark-task:v1"
    assert benchmark["additionalProperties"] is False
    assert evidence["$id"] == "urn:eeg:schema:evidence-record:v1"
    assert evidence["additionalProperties"] is False
    assert evidence["$defs"]["field_revision"]["properties"]["anchors"]["minItems"] == 1
