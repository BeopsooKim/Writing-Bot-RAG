from __future__ import annotations

import json
import subprocess
import sys

import pytest

from research_rag.defense_route_guard import (
    RawDefenseQueryBlocked,
    assert_not_raw_defense_query,
    build_raw_defense_query_block_message,
    is_defense_facing_query,
)
from research_rag.config import RagConfig
from research_rag.retrieval import retrieve


@pytest.mark.parametrize(
    "query",
    [
        "Can I say IEEE 519 compliance in defense?",
        "Draft final defense script",
        "Use 1,440 in final slide",
        "PF -> Fault -> Harmonic physical validation",
    ],
)
def test_defense_facing_raw_query_detected(query: str) -> None:
    assert is_defense_facing_query(query)
    with pytest.raises(RawDefenseQueryBlocked):
        assert_not_raw_defense_query(query, "test")


def test_normal_literature_query_allowed() -> None:
    assert not is_defense_facing_query("normal literature search on HVDC topology")
    assert_not_raw_defense_query("normal literature search on HVDC topology", "test")


def test_block_payload_contract() -> None:
    payload = build_raw_defense_query_block_message("Draft final defense script", "test")
    assert payload["gate"] == "blocked"
    assert payload["reason"] == "raw_defense_query_blocked"
    assert payload["final_prose_allowed"] is False
    assert payload["prepared_answer"] is None
    assert payload["results"] == []


def test_cli_query_blocks_defense_before_retrieval() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_rag.cli",
            "query",
            "Can I say IEEE 519 compliance in defense?",
            "--no-rerank",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["gate"] == "blocked"
    assert payload["reason"] == "raw_defense_query_blocked"
    assert payload["final_prose_allowed"] is False
    assert payload["prepared_answer"] is None
    assert payload["results"] == []


def test_retrieve_blocks_defense_before_search(tmp_path) -> None:
    with pytest.raises(RawDefenseQueryBlocked):
        retrieve(RagConfig.from_root(tmp_path), "PF -> Fault -> Harmonic physical validation", mode="bm25")
