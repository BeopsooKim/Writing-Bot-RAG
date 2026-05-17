from __future__ import annotations

import re
from dataclasses import dataclass


DEFENSE_QUERY_PATTERNS: tuple[str, ...] = (
    r"\bdissertation\s+defense\b",
    r"\bdefense\b",
    r"\bcommittee\b",
    r"\bfinal\s+(script|slide|thesis)\b",
    r"\bSCRIPT_(?:GO|NO_GO)\b",
    r"\bclaim\s+ledger\b",
    r"\banswer\s+card\b",
    r"\bslide\s+audit\b",
    r"\bKPG\b",
    r"\b13\s*/\s*7\b",
    r"\b1,?440\b",
    r"\b22\b",
    r"\bPF\s*[-\u2010-\u2015>]+\s*Fault\s*[-\u2010-\u2015>]+\s*Harmonic\b",
    r"\bPF[- ]Fault[- ]Harmonic\b",
    r"\bIEEE\s*519\b",
    r"\bIEC\s+compliance\b",
    r"\bbreaker[- ]?duty\b",
    r"\bprotection\s+coordination\b",
    r"\bfinal\s+utility\s+planning\b",
    r"\bsolver\s+superiority\b",
    r"\bbenchmark\s+originality\b",
    r"\bpost[- ]fault\s+harmonic\s+validation\b",
)


@dataclass(frozen=True)
class RawDefenseQueryBlocked(RuntimeError):
    payload: dict[str, object]

    def __str__(self) -> str:
        return str(self.payload.get("reason", "raw_defense_query_blocked"))


def is_defense_facing_query(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in DEFENSE_QUERY_PATTERNS)


def build_raw_defense_query_block_message(text: str, script_name: str) -> dict[str, object]:
    return {
        "gate": "blocked",
        "reason": "raw_defense_query_blocked",
        "script_name": script_name,
        "query": text,
        "required_wrapper": "skills/writing-bot/scripts/rag_defense_answer_card.py",
        "final_prose_allowed": False,
        "prepared_answer": None,
        "results": [],
    }


def assert_not_raw_defense_query(text: str, script_name: str) -> None:
    if is_defense_facing_query(text):
        raise RawDefenseQueryBlocked(build_raw_defense_query_block_message(text, script_name))
