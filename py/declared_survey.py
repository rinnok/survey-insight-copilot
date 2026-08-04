"""アプリが発行した調査契約（analysis contract）に基づく決定的分析エンジン。

design-survey-loop-v1.md §4・§6・§7 の実装。LLMを呼ばず、契約に書かれた
集計だけを実行する。自由記述は件数のみ数え、本文を返さない。
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import analysis_engine

normalize = analysis_engine.normalize
clean = analysis_engine.clean


class MappingNeeded(Exception):
    """列の自動照合が確定できず、学生の確認が必要。"""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__("列の対応づけを確認してください")
        self.items = items


def _single_candidates(question_text: str, columns: list[str]) -> list[str]:
    nq = normalize(question_text)
    exact = [c for c in columns if normalize(c) == nq]
    if exact:
        return exact
    return [c for c in columns if normalize(c).startswith(nq) or nq.startswith(normalize(c)) and normalize(c)]


def _matrix_candidates(question_text: str, item: str, columns: list[str]) -> list[str]:
    nq, ni = normalize(question_text), normalize(item)
    return [c for c in columns if nq in normalize(c) and ni in normalize(c)]


def match_columns(contract: dict[str, Any], columns: list[str],
                  manual: dict[str, Any] | None = None) -> dict[str, Any]:
    """契約の設問と回収データの列を照合する。

    戻り値: {questionId: 列名} （matrix5は {questionId: {item: 列名}}）。
    自動で一意に決まらない設問が残れば MappingNeeded を送出する。
    manual には学生が確定した対応（同じ形）を渡す。
    """
    manual = manual or {}
    mapping: dict[str, Any] = {}
    unresolved: list[dict[str, Any]] = []
    for q in contract["questions"]:
        qid = q["id"]
        if qid in manual and manual[qid]:
            mapping[qid] = manual[qid]
            continue
        if q["type"] == "matrix5":
            item_map: dict[str, str] = {}
            missing_items: list[dict[str, Any]] = []
            for item in q["items"]:
                cands = _matrix_candidates(q["text"], item, columns)
                if len(cands) == 1:
                    item_map[item] = cands[0]
                else:
                    missing_items.append({"item": item, "candidates": cands})
            if missing_items:
                unresolved.append({"questionId": qid, "text": q["text"], "type": "matrix5",
                                   "items": missing_items})
            else:
                mapping[qid] = item_map
        else:
            cands = _single_candidates(q["text"], columns)
            if len(cands) == 1:
                mapping[qid] = cands[0]
            else:
                unresolved.append({"questionId": qid, "text": q["text"], "type": q["type"],
                                   "candidates": cands})
    required = set(contract["structuralChecks"]["requiredQuestions"])
    blocking = [u for u in unresolved if u["questionId"] in required]
    if blocking:
        raise MappingNeeded(blocking)
    return mapping


def _answered(series: pd.Series) -> pd.Series:
    values = series.map(clean)
    return values[values != ""]


def structural_check(contract: dict[str, Any], df: pd.DataFrame,
                     mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """構造チェック。全okならvalid_new相当、1つでもngならinvalid。"""
    rules = contract["structuralChecks"]
    checks: list[dict[str, Any]] = []
    min_rows = int(rules.get("minRows", 0))
    checks.append({"key": "min-rows", "label": f"回答数が{min_rows}件以上",
                   "ok": len(df) >= min_rows, "detail": f"実際 {len(df)}件"})
    for qid in rules.get("requiredQuestions", []):
        checks.append({"key": f"mapped-{qid}", "label": f"{qid} の列が特定できる",
                       "ok": qid in mapping, "detail": ""})
    max_unknown = float(rules.get("maxUnknownOptionShare", 1.0))
    for q in contract["questions"]:
        qid = q["id"]
        if q["type"] != "single" or qid not in mapping or not q.get("options"):
            continue
        answered = _answered(df[mapping[qid]])
        if not len(answered):
            continue
        known = {normalize(o) for o in q["options"]}
        unknown_share = float((~answered.map(normalize).isin(known)).mean())
        checks.append({"key": f"options-{qid}", "label": f"{qid} の回答が選択肢と一致",
                       "ok": unknown_share <= max_unknown,
                       "detail": f"選択肢外の回答 {round(unknown_share * 100, 1)}%"})
    return checks


def _agg_distribution(q: dict[str, Any], series: pd.Series) -> dict[str, Any]:
    answered = _answered(series)
    counts = answered.map(normalize).value_counts()
    items = []
    for option in q["options"]:
        items.append({"value": option, "n": int(counts.get(normalize(option), 0))})
    known = {normalize(o) for o in q["options"]}
    unknown_n = int(sum(n for value, n in counts.items() if value not in known))
    denominator = int(len(answered))
    for item in items:
        item["share"] = round(item["n"] / denominator * 100, 1) if denominator else 0.0
    return {"kind": "distribution", "question": q["id"], "label": q["text"],
            "denominator": denominator, "items": items, "unknownN": unknown_n}


def _agg_likert(q: dict[str, Any], df: pd.DataFrame, item_map: dict[str, str]) -> dict[str, Any]:
    rows = []
    for item in q["items"]:
        series = _answered(df[item_map[item]])
        numeric = pd.to_numeric(series.str.extract(r"^(\d)")[0], errors="coerce").dropna()
        numeric = numeric[(numeric >= 1) & (numeric <= 5)]
        denominator = int(len(numeric))
        top2 = int((numeric >= 4).sum())
        rows.append({
            "item": item,
            "denominator": denominator,
            "mean": round(float(numeric.mean()), 2) if denominator else None,
            "top2N": top2,
            "top2Share": round(top2 / denominator * 100, 1) if denominator else 0.0,
        })
    return {"kind": "likert_summary", "question": q["id"], "label": q["text"], "rows": rows}


def _agg_crosstab(row_q: dict[str, Any], col_q: dict[str, Any],
                  row_series: pd.Series, col_series: pd.Series) -> dict[str, Any]:
    frame = pd.DataFrame({"row": row_series.map(clean), "col": col_series.map(clean)})
    frame = frame[(frame["row"] != "") & (frame["col"] != "")]
    frame["row_n"] = frame["row"].map(normalize)
    frame["col_n"] = frame["col"].map(normalize)
    col_totals = {normalize(o): int((frame["col_n"] == normalize(o)).sum()) for o in col_q["options"]}
    cells = []
    for row_opt in row_q["options"]:
        for col_opt in col_q["options"]:
            n = int(((frame["row_n"] == normalize(row_opt)) & (frame["col_n"] == normalize(col_opt))).sum())
            total = col_totals[normalize(col_opt)]
            cells.append({"row": row_opt, "col": col_opt, "n": n,
                          "share": round(n / total * 100, 1) if total else 0.0})
    return {
        "kind": "crosstab", "row": row_q["id"], "col": col_q["id"],
        "rowLabel": row_q["text"], "colLabel": col_q["text"],
        "rowOptions": list(row_q["options"]), "colOptions": list(col_q["options"]),
        "colDenominators": [{"col": o, "n": col_totals[normalize(o)]} for o in col_q["options"]],
        "cells": cells,
    }


def _build_claims(contract: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, list[str]]:
    can_say: list[str] = []
    questions = {q["id"]: q for q in contract["questions"]}
    for result in results:
        if result["kind"] == "distribution" and questions[result["question"]].get("role") == "decision":
            if result["denominator"]:
                top = max(result["items"], key=lambda item: item["n"])
                can_say.append(
                    f"{result['question']}では「{top['value']}」が最多の{top['n']}件だった（回答{result['denominator']}件中）。"
                )
        if result["kind"] == "likert_summary":
            rows = [r for r in result["rows"] if r["denominator"]]
            if rows:
                top = max(rows, key=lambda r: r["top2Share"])
                can_say.append(
                    f"{result['question']}では「{top['item']}」のtop2率（4・5の割合）が最も高かった"
                    f"（{top['top2Share']}%、回答{top['denominator']}件中）。"
                )
    cannot_say = list(contract.get("claimsTemplate", {}).get("cannotSay", []))
    return {"canSay": can_say, "cannotSay": cannot_say}


def analyze(contract: dict[str, Any], df: pd.DataFrame,
            mapping: dict[str, Any]) -> dict[str, Any]:
    """契約のaggregationsを実行し、analysis_json v2の中身（quality以外）を返す。"""
    questions = {q["id"]: q for q in contract["questions"]}
    results: list[dict[str, Any]] = []
    for agg in contract["aggregations"]:
        if agg["kind"] == "distribution":
            qid = agg["question"]
            if qid not in mapping:
                continue
            results.append({"aggregationId": agg["id"], **_agg_distribution(questions[qid], df[mapping[qid]])})
        elif agg["kind"] == "likert_summary":
            qid = agg["question"]
            if qid not in mapping:
                continue
            results.append({"aggregationId": agg["id"], **_agg_likert(questions[qid], df, mapping[qid])})
        elif agg["kind"] == "crosstab":
            row_id, col_id = agg["row"], agg["col"]
            if row_id not in mapping or col_id not in mapping:
                continue
            results.append({"aggregationId": agg["id"],
                            **_agg_crosstab(questions[row_id], questions[col_id],
                                            df[mapping[row_id]], df[mapping[col_id]])})
    free_text_count = {}
    for q in contract["questions"]:
        if q["type"] == "free" and q["id"] in mapping:
            free_text_count[q["id"]] = int(len(_answered(df[mapping[q["id"]]])))
    return {
        "results": results,
        "claims": _build_claims(contract, results),
        "freeTextCount": free_text_count,
    }
