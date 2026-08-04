"""Deterministic branch-aware denominator contracts for survey responses.

The module deliberately avoids semantic inference. A verified form snapshot
maps logical question IDs to response columns and declares the predicates used
for the analysis cohort and question reachability.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


SNAPSHOT_DIR = Path(__file__).parent / "documentation" / "fixtures"


def load_snapshot(name: str) -> dict[str, Any]:
    path = SNAPSHOT_DIR / name
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schemaVersion") != 1:
        raise ValueError("対応していないフォームスナップショットです")
    questions = snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("フォームスナップショットに設問がありません")
    ids = [str(item.get("id", "")).strip() for item in questions]
    if any(not question_id for question_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("フォームスナップショットの設問IDが不正です")
    if not isinstance(snapshot.get("population"), dict):
        raise ValueError("分析母集団の条件がありません")
    if not isinstance(snapshot.get("cohort"), dict):
        raise ValueError("分析対象の条件がありません")
    if not isinstance(snapshot.get("analyses"), list) or not snapshot["analyses"]:
        raise ValueError("分母を確認する分析設問がありません")


def _matching_columns(columns: list[str], matcher: dict[str, Any]) -> list[str]:
    exact = str(matcher.get("exact", "")).strip()
    contains = str(matcher.get("contains", "")).strip().lower()
    if exact:
        return [column for column in columns if column == exact]
    if contains:
        return [column for column in columns if contains in column.lower()]
    raise ValueError("設問の列照合条件がありません")


def resolve_questions(df: pd.DataFrame, snapshot: dict[str, Any]) -> dict[str, list[str]]:
    columns = [str(column) for column in df.columns]
    resolved: dict[str, list[str]] = {}
    for question in snapshot["questions"]:
        matches = _matching_columns(columns, question.get("columnMatch", {}))
        expected = question.get("expectedColumns", 1)
        if expected == "one_or_more":
            valid = len(matches) >= 1
        else:
            valid = len(matches) == int(expected)
        if not valid:
            raise ValueError(
                f"設問 {question['id']} の回答列を特定できません"
                f"（期待 {expected}、検出 {len(matches)}）"
            )
        resolved[question["id"]] = matches
    return resolved


def _coalesce(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    values = df[columns[0]].replace(r"^\s*$", pd.NA, regex=True)
    for column in columns[1:]:
        candidate = df[column].replace(r"^\s*$", pd.NA, regex=True)
        values = values.combine_first(candidate)
    return values.fillna("").astype(str).str.strip()


def logical_responses(
    df: pd.DataFrame, snapshot: dict[str, Any], resolved: dict[str, list[str]]
) -> dict[str, pd.Series]:
    responses: dict[str, pd.Series] = {}
    for question in snapshot["questions"]:
        question_id = question["id"]
        columns = resolved[question_id]
        combine = question.get("combine", "single")
        if combine == "first_non_empty":
            responses[question_id] = _coalesce(df, columns)
        elif combine == "single" and len(columns) == 1:
            responses[question_id] = df[columns[0]].fillna("").astype(str).str.strip()
        else:
            raise ValueError(f"設問 {question_id} の列統合方法が不正です")
    return responses


def evaluate_predicate(
    predicate: dict[str, Any], responses: dict[str, pd.Series], index: pd.Index
) -> pd.Series:
    if "all" in predicate:
        result = pd.Series(True, index=index)
        for child in predicate["all"]:
            result &= evaluate_predicate(child, responses, index)
        return result
    if "any" in predicate:
        result = pd.Series(False, index=index)
        for child in predicate["any"]:
            result |= evaluate_predicate(child, responses, index)
        return result
    if "not" in predicate:
        return ~evaluate_predicate(predicate["not"], responses, index)

    question_id = str(predicate.get("questionId", ""))
    if question_id not in responses:
        raise ValueError(f"分岐条件の設問IDが見つかりません: {question_id}")
    values = responses[question_id]
    operator = predicate.get("operator", "equals")
    expected = predicate.get("value", "")
    if operator == "equals":
        return values.eq(str(expected))
    if operator == "contains":
        return values.str.contains(str(expected), case=False, regex=False)
    if operator == "in":
        return values.isin([str(value) for value in predicate.get("values", [])])
    if operator == "regex":
        return values.str.contains(re.compile(str(expected), re.IGNORECASE), na=False)
    if operator == "answered":
        return values.ne("")
    raise ValueError(f"対応していない分岐演算子です: {operator}")


def build_denominator_contract(
    df: pd.DataFrame, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Build an aggregate-only contract; respondent rows never leave this module."""
    validate_snapshot(snapshot)
    resolved = resolve_questions(df, snapshot)
    responses = logical_responses(df, snapshot, resolved)
    population = evaluate_predicate(snapshot["population"]["predicate"], responses, df.index)
    cohort = evaluate_predicate(snapshot["cohort"]["predicate"], responses, df.index)
    if bool((cohort & ~population).any()):
        raise ValueError("分析対象条件が分析母集団の外側を含んでいます")

    questions = []
    checks = []
    for analysis in snapshot["analyses"]:
        question_id = analysis["questionId"]
        if question_id not in responses:
            raise ValueError(f"分析設問が見つかりません: {question_id}")
        reached = evaluate_predicate(analysis["reachWhen"], responses, df.index)
        if bool((reached & ~population).any()):
            raise ValueError(f"{question_id} の到達条件が分析母集団の外側を含んでいます")
        if bool((cohort & ~reached).any()):
            raise ValueError(f"{question_id} の分析対象に設問未到達者が含まれています")
        answered = responses[question_id].ne("")
        reached_answered = reached & answered
        reached_missing = reached & ~answered
        cohort_answered = cohort & answered
        cohort_missing = cohort & ~answered
        unexpected = population & ~reached & answered
        expected = analysis.get("expected", {})
        counts = {
            "reachedN": int(reached.sum()),
            "answeredN": int(reached_answered.sum()),
            "missingN": int(reached_missing.sum()),
            "excludedByBranchN": int((population & ~reached).sum()),
            "cohortN": int(cohort.sum()),
            "cohortAnsweredN": int(cohort_answered.sum()),
            "cohortMissingN": int(cohort_missing.sum()),
            "unexpectedAnswerN": int(unexpected.sum()),
        }
        for key, value in expected.items():
            checks.append({
                "key": f"{analysis['id']}.{key}",
                "label": f"{analysis['label']} / {key}",
                "actual": counts.get(key),
                "expected": value,
                "ok": counts.get(key) == value,
            })
        questions.append({
            "id": analysis["id"],
            "questionId": question_id,
            "label": analysis["label"],
            "reachCondition": analysis["reachCondition"],
            **counts,
        })

    return {
        "schemaVersion": 1,
        "snapshot": {
            "id": snapshot["id"],
            "title": snapshot["title"],
            "revision": snapshot["revision"],
            "verifiedAt": snapshot.get("verifiedAt", ""),
        },
        "universeN": int(len(df)),
        "population": {
            "label": snapshot["population"]["label"],
            "n": int(population.sum()),
        },
        "cohort": {
            "label": snapshot["cohort"]["label"],
            "n": int(cohort.sum()),
            "prerequisites": snapshot["cohort"].get("prerequisites", []),
        },
        "questions": questions,
        "checks": checks,
        "verified": all(check["ok"] for check in checks),
        "resolvedColumns": {
            question_id: {"count": len(columns), "columns": columns}
            for question_id, columns in resolved.items()
        },
    }
