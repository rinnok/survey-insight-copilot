"""熱海DR3向けの固定分析。

汎用的な推測ではなく、大学生調査で合意した定義だけを再現する。
個票は返さず、集計値・計算条件・限界だけを返す。
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

import branching_survey


EXPECTED = {
    "raw": 125,
    "students": 115,
    "visited": 76,
    "unvisited": 39,
    "consideredUnvisited": 24,
    "highIntent": 20,
}


def _columns(df: pd.DataFrame, phrase: str) -> list[str]:
    return [str(col) for col in df.columns if phrase.lower() in str(col).lower()]


def _one(df: pd.DataFrame, phrase: str) -> str:
    matches = _columns(df, phrase)
    if len(matches) != 1:
        raise ValueError(f"必要列を一意に特定できません: {phrase}（{len(matches)}列）")
    return matches[0]


def _optional_one(df: pd.DataFrame, phrase: str) -> str | None:
    matches = _columns(df, phrase)
    return matches[0] if len(matches) == 1 else None


def _coalesce(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    if not columns:
        raise ValueError("統合対象の列がありません")
    result = df[columns[0]]
    for column in columns[1:]:
        result = result.combine_first(df[column])
    return result.fillna("").astype(str).str.strip()


def _tokens(series: pd.Series, mask: pd.Series, column: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    answered = series.loc[mask].dropna().astype(str).loc[lambda values: values.str.strip() != ""]
    for raw in answered:
        for token in re.split(r",\s*|[;；\n]", raw):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    items = [
        {"label": label.split("(", 1)[0].strip(), "rawLabel": label, "n": n,
         "pct": round(n / int(len(answered)) * 100, 1) if int(len(answered)) else 0}
        for label, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {"column": column, "denominator": int(len(answered)), "focusN": int(mask.sum()), "items": items}


def _count(summary: dict[str, Any], phrase: str) -> int:
    match = next((item for item in summary["items"] if phrase.lower() in item["rawLabel"].lower()), None)
    return int(match["n"]) if match else 0


def _display_value(value: str) -> str:
    raw = str(value).strip()
    lower = raw.lower()
    if "undergraduate" in lower:
        return "学部生"
    if "graduate student" in lower:
        return "大学院生"
    if "international" in lower and ("yes" in lower or raw.startswith("はい")):
        return "留学生"
    if "international" in lower and ("no" in lower or raw.startswith("いいえ")):
        return "非留学生"
    return raw.split("(", 1)[0].split("\n", 1)[0].strip() or "未回答"


def _segment_payload(
    df: pd.DataFrame,
    mask: pd.Series,
    unvisited: pd.Series,
    considered: pd.Series,
    high_intent: pd.Series,
    barrier_col: str,
    desired_col: str,
) -> dict[str, Any]:
    group_unvisited = unvisited & mask
    group_considered = considered & mask
    group_high = high_intent & mask
    considered_n = int(group_considered.sum())
    barriers = _tokens(df[barrier_col], group_considered, barrier_col)
    desired = _tokens(df[desired_col], group_considered, desired_col)
    small_sample = considered_n < 5
    return {
        "unvisitedN": int(group_unvisited.sum()),
        "consideredN": considered_n,
        "highIntentN": int(group_high.sum()),
        "barriers": barriers,
        "desired": desired,
        "smallSample": small_sample,
        "suppressed": False,
    }


def _attribute_segments(
    df: pd.DataFrame,
    students: pd.Series,
    unvisited: pd.Series,
    considered: pd.Series,
    high_intent: pd.Series,
    barrier_col: str,
    desired_col: str,
    student_col: str,
) -> list[dict[str, Any]]:
    definitions = [
        ("academic_status", "学籍", student_col),
        ("university", "大学", _optional_one(df, "What university do you attend")),
        ("international", "留学生区分", _optional_one(df, "Are you an international student")),
        ("gender", "性別", _optional_one(df, "What is your gender")),
    ]
    dimensions: list[dict[str, Any]] = []
    for dimension_id, label, column in definitions:
        if not column:
            continue
        values = df[column].fillna("").astype(str).str.strip()
        candidates = [value for value in values.loc[unvisited].unique().tolist() if value]
        if not 2 <= len(candidates) <= 8:
            continue
        groups = []
        for index, raw_value in enumerate(candidates, 1):
            mask = students & values.eq(raw_value)
            payload = _segment_payload(df, mask, unvisited, considered, high_intent, barrier_col, desired_col)
            if payload["unvisitedN"] == 0:
                continue
            display = _display_value(raw_value)
            if dimension_id == "international":
                display = "留学生" if raw_value.startswith("はい") or "yes" in raw_value.lower() else "非留学生"
            groups.append({"id": f"{dimension_id}-{index}", "label": display, **payload})
        groups.sort(key=lambda group: (-group["unvisitedN"], group["label"]))
        if len(groups) >= 2:
            dimensions.append({"id": dimension_id, "label": label, "column": column, "groups": groups})
    return dimensions


def _axis_value(axis_id: str, raw_value: Any) -> str | None:
    raw = str(raw_value or "").strip()
    lower = raw.lower()
    if not raw or raw.lower() == "nan":
        return None
    if axis_id == "university":
        return "shibaura" if "芝浦" in raw or "shibaura" in lower else "other"
    if axis_id == "academic_status":
        if "undergraduate" in lower or "学部" in raw:
            return "undergraduate"
        if "graduate student" in lower or "大学院" in raw or "修士" in raw or "博士" in raw:
            return "graduate"
        return None
    if axis_id == "gender":
        if "female" in lower or "女性" in raw:
            return "female"
        if "male" in lower or "男性" in raw:
            return "male"
        return "other"
    if axis_id == "international":
        if raw.startswith("はい") or "(yes)" in lower:
            return "international"
        if raw.startswith("いいえ") or "(no)" in lower:
            return "domestic"
    return None


def _comparison_payload(
    df: pd.DataFrame,
    considered: pd.Series,
    student_col: str,
    barrier_col: str,
    desired_col: str,
) -> dict[str, Any]:
    """Return only anonymous aggregates for the four intentionally limited axes."""
    columns = {
        "university": _optional_one(df, "What university do you attend"),
        "academic_status": student_col,
        "gender": _optional_one(df, "What is your gender"),
        "international": _optional_one(df, "Are you an international student"),
    }
    axes = [
        {"id": "university", "label": "大学", "values": [
            {"id": "shibaura", "label": "芝浦工業大学"},
            {"id": "other", "label": "その他の大学"},
        ]},
        {"id": "academic_status", "label": "学籍", "values": [
            {"id": "undergraduate", "label": "学部生"},
            {"id": "graduate", "label": "大学院生"},
        ]},
        {"id": "gender", "label": "性別", "values": [
            {"id": "male", "label": "男性"},
            {"id": "female", "label": "女性"},
            {"id": "other", "label": "その他・回答しない"},
        ]},
        {"id": "international", "label": "留学生区分", "values": [
            {"id": "international", "label": "留学生"},
            {"id": "domestic", "label": "非留学生"},
        ]},
    ]
    normalized = {
        axis_id: df[column].map(lambda value, key=axis_id: _axis_value(key, value))
        for axis_id, column in columns.items() if column
    }
    cell_keys: dict[tuple[str, ...], list[int]] = {}
    for index in df.index[considered]:
        values = tuple(normalized[axis["id"]].loc[index] or "unknown" for axis in axes)
        cell_keys.setdefault(values, []).append(index)
    cells = []
    for values, indices in cell_keys.items():
        mask = df.index.isin(indices)
        barrier = _tokens(df[barrier_col], pd.Series(mask, index=df.index), barrier_col)
        desired = _tokens(df[desired_col], pd.Series(mask, index=df.index), desired_col)
        cells.append({
            "attributes": dict(zip((axis["id"] for axis in axes), values)),
            "n": len(indices),
            "questions": {
                "barriers": {"denominator": barrier["denominator"], "items": barrier["items"]},
                "desired": {"denominator": desired["denominator"], "items": desired["items"]},
            },
        })
    return {
        "population": "熱海を検討したが未訪問の学生",
        "populationN": int(considered.sum()),
        "axes": axes,
        "questions": [
            {"id": "barriers", "label": "熱海を選ばなかった理由", "multiple": True},
            {"id": "desired", "label": "熱海でしたいこと", "multiple": True},
        ],
        "cells": cells,
        "privacy": "個票は返さず、4軸の匿名集計だけを表示します。",
    }


def analyze(df: pd.DataFrame, filename: str = "", route: str = "atami_policy_test") -> dict[str, Any]:
    form_snapshot = branching_survey.load_snapshot("atami-form-snapshot.v1.json")
    denominator_contract = branching_survey.build_denominator_contract(df, form_snapshot)
    student_col = _one(df, "Are you currently a bachelors or masters student")
    intent_cols = list(dict.fromkeys(
        _columns(df, "How likely do you want to travel")
        + _columns(df, "How much do you want to go on vacation or a trip right now")
    ))
    visit_cols = _columns(df, "Have you ever visited Atami City")
    considered_col = _one(df, "Have you ever thought about going to Atami")
    barrier_col = _one(df, "reasons why you decided not to go")
    desired_col = _one(df, "What would you like to do in Atami")
    if len(intent_cols) != 2 or len(visit_cols) != 2:
        raise ValueError(f"分岐列を特定できません（旅行意欲{len(intent_cols)}列・訪問経験{len(visit_cols)}列）")

    student_text = df[student_col].fillna("").astype(str)
    students = (
        student_text.str.contains("undergraduate", case=False, regex=False)
        | student_text.str.contains("graduate student", case=False, regex=False)
    )
    visit = _coalesce(df, visit_cols)
    visited = students & visit.str.contains("(Yes)", regex=False)
    unvisited = students & visit.str.contains("(No)", regex=False)
    considered = unvisited & df[considered_col].fillna("").astype(str).str.contains(
        "I have thought about it", regex=False
    )
    intent = _coalesce(df, intent_cols).str.extract(r"^(\d)")[0]
    high_intent = considered & intent.isin(["4", "5"])

    actual = {
        "raw": int(len(df)),
        "students": int(students.sum()),
        "visited": int(visited.sum()),
        "unvisited": int(unvisited.sum()),
        "consideredUnvisited": int(considered.sum()),
        "highIntent": int(high_intent.sum()),
    }
    checks = [
        {"key": key, "label": label, "actual": actual[key], "expected": EXPECTED[key],
         "ok": actual[key] == EXPECTED[key]}
        for key, label in [
            ("raw", "元回答"), ("students", "大学生・大学院生"), ("visited", "訪問済み"),
            ("unvisited", "未訪問"), ("consideredUnvisited", "検討したが未訪問"),
            ("highIntent", "旅行意欲4・5"),
        ]
    ]
    barriers = _tokens(df[barrier_col], considered, barrier_col)
    desired = _tokens(df[desired_col], considered, desired_col)
    focus_expectations = [
        ("未訪問理由の有効回答", barriers["denominator"], 23),
        ("希望体験の有効回答", desired["denominator"], 24),
        ("何ができるか不明", _count(barriers, "Don't know what we can do"), 8),
        ("同行者不在", _count(barriers, "No one wants to travel with me"), 6),
        ("費用懸念", _count(barriers, "costs seem to be high"), 6),
        ("海鮮・グルメ希望", _count(desired, "Seafood and high end dining"), 18),
        ("温泉希望", _count(desired, "Hot springs"), 14),
        ("海辺散歩希望", _count(desired, "A stroll along the coast"), 11),
    ]
    checks.extend({"key": f"focus-{index}", "label": label, "actual": actual_n, "expected": expected_n,
                   "ok": actual_n == expected_n}
                  for index, (label, actual_n, expected_n) in enumerate(focus_expectations, 1))
    checks.extend(denominator_contract["checks"])
    canonical = all(check["ok"] for check in checks)
    evidence = [
        {"stage": "学生", "columns": [student_col], "condition": "社会人・その他を除外",
         "numerator": actual["students"], "denominator": actual["raw"]},
        {"stage": "訪問経験", "columns": visit_cols, "condition": "2つの分岐列を行単位で統合し Yes / No を判定",
         "numerator": actual["visited"], "denominator": actual["students"]},
        {"stage": "検討未訪問", "columns": [considered_col], "condition": "学生 ∩ 未訪問 ∩ 行こうと考えたことがある",
         "numerator": actual["consideredUnvisited"], "denominator": actual["unvisited"]},
        {"stage": "高旅行意欲", "columns": intent_cols, "condition": "検討未訪問 ∩ 旅行意欲4または5",
         "numerator": actual["highIntent"], "denominator": actual["consideredUnvisited"]},
        {"stage": "未訪問理由", "columns": [barrier_col], "condition": "検討未訪問者の有効回答を複数回答として集計",
         "numerator": barriers["denominator"], "denominator": actual["consideredUnvisited"]},
        {"stage": "希望体験", "columns": [desired_col], "condition": "検討未訪問者の有効回答を複数回答として集計",
         "numerator": desired["denominator"], "denominator": actual["consideredUnvisited"]},
    ]
    result = {
        "filename": filename,
        "quality": {"canonical": canonical, "checks": checks,
                    "message": "基準値と一致。次の調査設計へ進めます。" if canonical else
                    "基準値と不一致です。列・分岐・除外条件を確認するまで次の調査設計を停止します。"},
        "counts": actual,
        "funnel": [
            {"label": "元回答", "n": actual["raw"], "note": "社会人等10件を含む"},
            {"label": "大学生・大学院生", "n": actual["students"], "note": "分析対象"},
            {"label": "未訪問", "n": actual["unvisited"], "note": f"訪問済み {actual['visited']}件"},
            {"label": "検討したが未訪問", "n": actual["consideredUnvisited"], "note": "今回の焦点"},
            {"label": "旅行意欲4・5", "n": actual["highIntent"], "note": "24件中"},
        ],
        "barriers": barriers,
        "desired": desired,
        "evidence": evidence,
        "denominatorContract": denominator_contract,
        "segmentOverall": _segment_payload(
            df, students, unvisited, considered, high_intent, barrier_col, desired_col
        ),
        "segments": _attribute_segments(
            df, students, unvisited, considered, high_intent, barrier_col, desired_col, student_col
        ),
        "comparison": _comparison_payload(df, considered, student_col, barrier_col, desired_col),
    }
    if not canonical:
        return result

    unknown_n = _count(barriers, "Don't know what we can do")
    companion_n = _count(barriers, "No one wants to travel with me")
    cost_n = _count(barriers, "costs seem to be high")
    seafood_n = _count(desired, "Seafood and high end dining")
    spring_n = _count(desired, "Hot springs")
    stroll_n = _count(desired, "A stroll along the coast")
    result.update({
        "claims": {
            "canSay": [
                "学生115件のうち、熱海未訪問は39件だった。",
                "未訪問39件のうち24件は、熱海を検討した経験があった。",
                "検討未訪問24件のうち20件は、現在の旅行意欲が4または5だった。",
                f"検討未訪問者では『何ができるか分からない』が{unknown_n}件で最も多かった。",
            ],
            "cannotSay": [
                "大学生全体がチル旅を好む。",
                "情報施策を実施すれば来訪が増える。",
                "この標本が全国の大学生を代表する。",
            ],
        },
        "plans": [
            {"id": "A", "name": "予算・所要時間・雨天可否が分かる半日プラン",
             "basis": f"費用{cost_n}件、『何ができるか分からない』{unknown_n}件"},
            {"id": "B", "name": "ひとり・友人・パートナー別の過ごし方",
             "basis": f"『一緒に行く人がいない』{companion_n}件"},
            {"id": "C", "name": "海鮮・温泉・海辺散歩を選べる定番プラン",
             "basis": f"希望上位：海鮮{seafood_n}件、温泉{spring_n}件、海辺散歩{stroll_n}件"},
        ],
        "survey": next_survey(),
    })
    result["route"] = route
    if route == "atami_conversion":
        result.pop("plans", None)
        result.pop("survey", None)
    return result


def next_survey() -> dict[str, Any]:
    return {
        "title": "熱海の訪問候補3案 比較調査",
        "purpose": "3案の相対評価と、選択条件を確認する。施策効果の検証ではない。",
        "questions": [
            {"id": "Q1", "text": "あなたの所属を教えてください。", "type": "単一選択",
             "options": ["学部生", "大学院生（院生）", "その他"]},
            {"id": "Q2", "text": "現在の熱海との関わりに最も近いものを選んでください。", "type": "単一選択",
             "options": ["訪問したことがある", "検討したが行かなかった", "知っているが検討していない", "知らなかった"]},
            {"id": "Q3", "text": "次の各案があれば、熱海を候補に入れたいと思いますか？", "type": "5段階マトリクス",
             "options": ["A：予算・時間・雨天情報", "B：ひとり・同行者別プラン", "C：海鮮・温泉・海辺散歩"],
             "scale": ["1 全く思わない", "2", "3", "4", "5 とても思う"]},
            {"id": "Q4", "text": "最も利用したい案を1つ選んでください。", "type": "単一選択",
             "options": ["A", "B", "C", "どれも利用したくない"]},
            {"id": "Q5", "text": "交通費を含む総予算はいくらまでなら選びやすいですか？", "type": "単一選択",
             "options": ["5千円未満", "5千～1万円", "1万～1万5千円", "1万5千～2万円", "2万円以上"]},
            {"id": "Q6", "text": "誰と行く想定が最も近いですか？", "type": "単一選択",
             "options": ["ひとり", "友人", "パートナー", "家族", "未定"]},
            {"id": "Q7", "text": "希望する時期と滞在方法を選んでください。", "type": "単一選択",
             "options": ["平日・日帰り", "平日・宿泊", "休日・日帰り", "休日・宿泊", "どちらでもよい"]},
            {"id": "Q8", "text": "選んだ案の決め手を教えてください。", "type": "自由記述", "options": []},
        ],
    }


def survey_contract() -> dict[str, Any]:
    """next_survey()の設問定義から分析契約を発行する。

    契約は declared_survey_v1 エンジンの唯一の入力であり、集計の種類は
    distribution / likert_summary / crosstab の3つに固定する（design-survey-loop-v1.md §4）。
    """
    survey = next_survey()
    type_map = {"単一選択": "single", "5段階マトリクス": "matrix5", "自由記述": "free"}
    role_map = {"Q1": "attribute", "Q2": "attribute", "Q3": "metric", "Q4": "decision",
                "Q5": "metric", "Q6": "metric", "Q7": "metric", "Q8": "context"}
    questions = []
    for q in survey["questions"]:
        entry: dict[str, Any] = {
            "id": q["id"], "text": q["text"],
            "type": type_map.get(q["type"], "single"),
            "role": role_map.get(q["id"], "context"),
        }
        if entry["type"] == "matrix5":
            entry["items"] = list(q["options"])
        else:
            entry["options"] = list(q.get("options", []))
        questions.append(entry)
    return {
        "schemaVersion": 1,
        "engine": "declared_survey_v1",
        "title": survey["title"],
        "purpose": survey["purpose"],
        "questions": questions,
        "aggregations": [
            {"id": "agg-q4", "kind": "distribution", "question": "Q4"},
            {"id": "agg-q3", "kind": "likert_summary", "question": "Q3", "measures": ["top2_share", "mean"]},
            {"id": "agg-q4xq1", "kind": "crosstab", "row": "Q4", "col": "Q1"},
            {"id": "agg-q4xq2", "kind": "crosstab", "row": "Q4", "col": "Q2"},
            {"id": "agg-q5", "kind": "distribution", "question": "Q5"},
            {"id": "agg-q6", "kind": "distribution", "question": "Q6"},
            {"id": "agg-q7", "kind": "distribution", "question": "Q7"},
        ],
        "structuralChecks": {
            "requiredQuestions": ["Q1", "Q2", "Q3", "Q4"],
            "minRows": 20,
            "maxUnknownOptionShare": 0.2,
        },
        "claimsTemplate": {
            "cannotSay": [
                "この結果は施策の効果を示さない。",
                "回答者がこの層の母集団を代表するとは限らない。",
                "選ばれた案が実際の来訪につながるかは、この調査では確認できない。",
            ],
        },
    }
