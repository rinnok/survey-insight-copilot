"""登録材料を、既知・未知・次の検証候補へ整理する。"""
from __future__ import annotations

import re
from typing import Any


TOPIC_WORDS = ("年代", "目的", "理由", "情報", "費用", "予算", "訪問", "検討", "満足", "再訪", "平日", "同行")


def _clean(text: Any) -> str:
    return "" if text is None else str(text).strip()


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。\n]+", text) if len(part.strip()) >= 3]


def _known_from_dataset(material: dict[str, Any]) -> list[dict[str, Any]]:
    summary = material.get("summary", {})
    rows = int(summary.get("rows", 0))
    result = [{
        "statement": f"「{material['title']}」には{rows}件の回答がある。",
        "source": material["title"],
        "evidenceType": "アンケート集計",
        "strength": "確認済み",
        "scope": "この調査の回答者内",
    }]
    for column in summary.get("columns", []):
        name = _clean(column.get("shortName") or column.get("name"))
        if not any(word in name for word in TOPIC_WORDS):
            continue
        tokens = column.get("tokens") or column.get("values") or []
        if not tokens:
            continue
        top = tokens[0]
        denominator = int(column.get("nonEmpty", rows))
        # Numeric-looking answers can coincidentally be tagged as multi-select.
        # Keep the wording neutral unless the leading token is actually categorical.
        try:
            float(str(top.get("value", "")).replace(",", ""))
            top_is_numeric = True
        except ValueError:
            top_is_numeric = False
        wording = "選択" if column.get("looksMultiChoice") and not top_is_numeric else "回答"
        result.append({
            "statement": f"「{name}」では「{top['value']}」が最多（{wording}{top['n']}件／回答{denominator}件）。",
            "source": material["title"],
            "evidenceType": "アンケート集計",
            "strength": "確認済み",
            "scope": "この設問の回答者内",
        })
        if len(result) >= 5:
            break
    return result


def _known_from_note(material: dict[str, Any]) -> list[dict[str, Any]]:
    strength = "外部根拠" if material["kind"] == "論文・既存資料" else ("現場情報" if material["kind"] == "DMO・地域から聞いたこと" else "未検証")
    return [{
        "statement": sentence,
        "source": material["title"],
        "evidenceType": material["kind"],
        "strength": strength,
        "scope": "登録者が入力した情報",
    } for sentence in _sentences(material.get("content", ""))[:4]]


def _atami_candidates(target: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "H-COST",
            "hypothesis": "費用の高さより、旅行総額が分からないことが訪問の障壁になっている。",
            "gap": "費用の高さと不透明さを区別できていない。",
            "why": "割引と予算情報のどちらを優先するかが変わる。",
            "method": "アンケート",
            "target": target or "熱海を知っている大学生",
            "priority": 1,
            "effort": "小",
            "decisionImpact": "高",
        },
        {
            "id": "H-PLAN",
            "hypothesis": "熱海での過ごし方が分からず、旅行先の候補から外している。",
            "gap": "情報不足の具体的な内容が分からない。",
            "why": "認知広告とモデルコース提示のどちらを行うかが変わる。",
            "method": "アンケート",
            "target": target or "熱海を知っている大学生",
            "priority": 2,
            "effort": "小",
            "decisionImpact": "高",
        },
        {
            "id": "H-SCHEDULE",
            "hypothesis": "熱海への関心はあるが、同行者や日程の調整が訪問を妨げている。",
            "gap": "関心が訪問へ変わらない生活上の条件が分からない。",
            "why": "個人向け情報と友人同士で選べる提案のどちらを優先するかが変わる。",
            "method": "アンケート",
            "target": target or "熱海を検討した未訪問の大学生",
            "priority": 3,
            "effort": "小",
            "decisionImpact": "中",
        },
        {
            "id": "H-NOREASON",
            "hypothesis": "明確な不満ではなく、他の旅行先が自然に優先されている。",
            "gap": "「特に理由はない」の判断過程が分からない。",
            "why": "障壁解消より、比較時に思い出してもらう施策が必要か判断できる。",
            "method": "任意：少人数インタビュー",
            "target": target or "熱海を検討した未訪問の大学生",
            "priority": 4,
            "effort": "中",
            "decisionImpact": "中",
        },
    ]


def _generic_candidates(decision: str, target: str) -> list[dict[str, Any]]:
    subject = target or "対象者"
    return [
        {"id": "H-BARRIER", "hypothesis": f"{subject}の行動を、費用・時間・情報不足のいずれかが妨げている。", "gap": "主な障壁が分からない。", "why": "最初に解く課題が変わる。", "method": "アンケート", "target": target, "priority": 1, "effort": "小", "decisionImpact": "高"},
        {"id": "H-SEGMENT", "hypothesis": f"{subject}は、経験の違いによって重視する条件が異なる。", "gap": "対象者を一括で扱っている。", "why": "対象別に提案を変えるべきか判断できる。", "method": "アンケート", "target": target, "priority": 2, "effort": "小", "decisionImpact": "中"},
        {"id": "H-CONTEXT", "hypothesis": f"{subject}には、既存の選択肢では捉えられない事情がある。", "gap": "選択肢にない理由が分からない。", "why": "新しい仮説を発見できる。", "method": "任意：少人数インタビュー", "target": target, "priority": 3, "effort": "中", "decisionImpact": "中"},
    ]


def organize_project(project: dict[str, Any]) -> dict[str, Any]:
    known: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for material in project.get("materials", []):
        if material["kind"] == "アンケートデータ":
            known.extend(_known_from_dataset(material))
        elif material["kind"] in ("学生の気づき", "分析メモ"):
            unverified.extend({"gap": f"未検証メモ：{sentence}", "why": f"出典：{material['title']}。事実として使う前に根拠確認が必要。", "candidateId": ""} for sentence in _sentences(material.get("content", ""))[:4])
        else:
            known.extend(_known_from_note(material))
    for analysis in project.get("analyses", [])[:4]:
        result = analysis.get("result", {})
        known.append({
            "statement": result.get("headline", analysis["question"]),
            "source": f"保存済み分析：{analysis['question']}",
            "evidenceType": "実データ分析",
            "strength": "確認済み",
            "scope": f"回答{result.get('rows', '—')}件",
        })

    corpus = " ".join([project.get("title", ""), project.get("decision_question", "")] + [m.get("title", "") + " " + m.get("content", "") for m in project.get("materials", [])])
    is_atami = "熱海" in corpus
    candidates = _atami_candidates(project.get("target", "")) if is_atami else _generic_candidates(project["decision_question"], project.get("target", ""))
    unknown = unverified + [{"gap": item["gap"], "why": item["why"], "candidateId": item["id"]} for item in candidates]
    cautions = []
    datasets = [m for m in project.get("materials", []) if m["kind"] == "アンケートデータ"]
    if len(datasets) > 1:
        cautions.append("複数のアンケートは対象者・募集方法・時期が同じとは限らないため、単純合算しない。")
    missing_profiles = [m["title"] for m in datasets if not m.get("summary", {}).get("studyProfile")]
    if missing_profiles:
        cautions.append("対象者・募集方法・調査時期が未登録の調査があります。調査間比較は、条件を確認するまで行いません。")
    if not datasets:
        cautions.append("回答データが未登録です。入力メモは事実ではなく、現場情報または仮説として扱います。")
    return {
        "known": known[:16],
        "unknown": unknown,
        "candidates": candidates,
        "cautions": cautions,
        "summary": f"材料{len(project.get('materials', []))}件から、確認できること{len(known[:16])}件、検証候補{len(candidates)}件を整理しました。",
    }
