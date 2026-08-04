"""調査目的を仮説へ分解し、短く自然な設問案へ変換する。"""
from __future__ import annotations

import re
from typing import Any


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _q(qid: str, text: str, qtype: str, options: list[str], purpose: str, analysis: str, branch: str = "") -> dict[str, Any]:
    return {
        "id": qid,
        "text": text,
        "type": qtype,
        "options": options,
        "purpose": purpose,
        "analysis": analysis,
        "branch": branch,
    }


def _normalized(text: str) -> str:
    return re.sub(r"\s", "", clean(text).lower())


def _has(text: str, *words: str) -> bool:
    normalized = _normalized(text)
    return any(word in normalized for word in words)


def _subject_from_objective(objective: str) -> str:
    subject = re.sub(r"(を)?(明らかにしたい|知りたい|検証したい|確認したい).*$", "", clean(objective)).strip(" 、。")
    return subject or clean(objective).rstrip("。！？")


def suggest_hypotheses(objective: str, target: str, known: str = "") -> dict[str, Any]:
    objective, target, known = clean(objective), clean(target), clean(known)
    if len(objective) < 8:
        raise ValueError("調査目的を8文字以上で入力してください")
    if not target:
        raise ValueError("誰に聞くかを入力してください")

    hypotheses: list[dict[str, Any]] = []
    if _has(objective, "熱海"):
        candidates = [
            ("熱海での過ごし方が分からず、旅行先の候補から外している。", "過ごし方の理解と検討・訪問経験を比較", "アンケート"),
            ("旅行にかかる総額が分からず、熱海への訪問をためらっている。", "費用の不透明さと訪問意向を比較", "アンケート"),
            ("熱海に関心はあるが、同行者や日程の調整が訪問を妨げている。", "同行者・日程・時間の障壁を比較", "アンケート"),
        ]
        if _has(objective, "選ぶ", "選ばない", "理由", "なぜ"):
            candidates.insert(0, ("熱海を検討しても、具体的な旅行イメージを持てない学生は訪問に至りにくい。", "旅行イメージの有無と訪問段階を比較", "アンケート"))
    elif _has(objective, "施策", "候補", "比較", "どれ"):
        candidates = [
            ("提示する案によって、利用意向に差がある。", "案別の利用意向を比較", "アンケート"),
            ("利用意向の差は、費用と使いやすさの評価によって生じる。", "選択理由と利用意向を比較", "アンケート"),
            ("想定していない不安が、利用を妨げている。", "ためらう理由を探索", "インタビュー"),
        ]
    else:
        subject = _subject_from_objective(objective)
        candidates = [
            (f"対象者の経験によって、{subject}に違いがある。", "経験別に回答を比較", "アンケート"),
            (f"費用・時間・情報不足のいずれかが、{subject}を妨げている。", "理由の種類と多さを確認", "インタビュー→アンケート"),
            (f"条件を具体的に示すと、{subject}への意向が高まる。", "条件提示後の意向を確認", "アンケート"),
        ]

    for index, (text, evidence, method) in enumerate(candidates[:4], 1):
        hypotheses.append({
            "id": f"H{index}",
            "text": text,
            "evidenceNeeded": evidence,
            "recommendedMethod": method,
            "reason": "人数や群の差を確かめる" if method == "アンケート" else "理由を先に深掘りしてから、必要なら人数を確かめる",
        })
    return {
        "objective": objective,
        "target": target,
        "known": known,
        "hypotheses": hypotheses,
        "note": "候補は仮案です。学生が修正してから採用してください。",
    }


def _screening_question(target: str) -> dict[str, Any]:
    normalized = _normalized(target)
    if "芝浦工業" in normalized:
        return _q("Q1", "現在、芝浦工業大学に在籍していますか。", "単一選択", ["学部生", "大学院生（院生）", "在籍していない"], "対象者を確認", "学部生・院生を対象に集計")
    if "大学" in normalized or "学生" in normalized:
        area = "首都圏の" if "首都圏" in normalized else ""
        return _q("Q1", f"現在、{area}大学・大学院に在籍していますか。", "単一選択", ["学部生", "大学院生（院生）", "在籍していない"], "対象者を確認", "学部生・院生を対象に集計")
    return _q("Q1", f"あなたは「{target}」に当てはまりますか。", "単一選択", ["はい", "いいえ"], "対象者を確認", "「はい」の回答を対象に集計")


def _condition_question(hypothesis: str) -> str:
    if _has(hypothesis, "総額", "費用", "予算"):
        return "交通費や現地で使う金額が事前に分かれば、熱海に行ってみたいと思いますか。"
    if _has(hypothesis, "過ごし方", "旅行イメージ"):
        return "熱海での過ごし方が事前に分かれば、行ってみたいと思いますか。"
    if _has(hypothesis, "同行者", "日程"):
        return "同行者と選べる短時間プランがあれば、熱海に行ってみたいと思いますか。"
    return "熱海の具体的な旅行プランが分かれば、行ってみたいと思いますか。"


def _quality_check(question: dict[str, Any], objective: str) -> dict[str, Any]:
    text = question["text"]
    issues: list[str] = []
    if len(text) > 72:
        issues.append("設問文が長めです")
    if any(word in text for word in ("今回の調査対象", "その行動", "中心仮説")):
        issues.append("回答者に伝わりにくい表現があります")
    if any(word in text for word in ("良いと思いませんか", "当然", "すべきだと思いますか")):
        issues.append("誘導的な表現があります")
    if text.count("ますか") + text.count("ですか") > 1:
        issues.append("1問で複数のことを聞いている可能性があります")
    concept_groups = [
        ("料金", "費用", "価格", "安く", "予算"),
        ("使いやす", "便利", "操作"),
        ("時間", "日程", "所要時間"),
        ("同行者", "友人", "家族"),
        ("内容", "過ごし方", "体験"),
    ]
    concept_count = sum(any(word in text for word in group) for group in concept_groups)
    if concept_count >= 2 and any(mark in text for mark in ("、", "と", "や", "・")):
        issues.append("複数の内容を同時に評価しています")
    if clean(objective).rstrip("。！？") and clean(objective).rstrip("。！？") in text:
        issues.append("調査目的をそのまま質問しています")
    if question["type"] not in ("自由記述", "インタビュー") and not question["options"]:
        issues.append("選択肢がありません")
    if len(question["options"]) != len(set(question["options"])):
        issues.append("重複する選択肢があります")
    return {"id": question["id"], "status": "要確認" if issues else "OK", "issues": issues}


def check_questions(questions: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    checks = [_quality_check(question, objective) for question in questions]
    seen: dict[str, int] = {}
    for index, question in enumerate(questions):
        key = _normalized(question.get("text", ""))
        if key and key in seen:
            checks[index]["status"] = "要確認"
            checks[index]["issues"].append("同じ内容の設問があります")
            first = seen[key]
            checks[first]["status"] = "要確認"
            checks[first]["issues"].append("同じ内容の設問があります")
        elif key:
            seen[key] = index
    return checks


def generate_questionnaire(
    objective: str,
    target: str,
    duration: int = 5,
    known: str = "",
    hypothesis: str = "",
    research_method: str = "アンケート",
) -> dict[str, Any]:
    objective, target, known, hypothesis, research_method = map(clean, (objective, target, known, hypothesis, research_method))
    if len(objective) < 8:
        raise ValueError("調査目的を8文字以上で入力してください")
    if not target:
        raise ValueError("誰に聞くかを入力してください")
    duration = max(2, min(int(duration or 5), 15))
    if not hypothesis:
        raise ValueError("仮説候補を選び、必要なら修正してから採用してください")

    if "インタビュー" in research_method:
        if _has(objective + hypothesis, "熱海"):
            questions = [
                _q("Q1", "熱海を旅行先として考えたきっかけを教えてください。", "インタビュー", [], "検討のきっかけを探す", "発言をきっかけ別に整理"),
                _q("Q2", "検討中に、どんな情報を調べましたか。", "インタビュー", [], "情報収集の実態を探す", "情報源と探した内容を整理"),
                _q("Q3", "最終的に訪れなかった理由を教えてください。", "インタビュー", [], "訪問しなかった経緯を探す", "理由と判断時点を整理"),
                _q("Q4", "同行者は、判断にどう影響しましたか。", "インタビュー", [], "同行者の影響を探す", "影響の有無と内容を整理"),
                _q("Q5", "日程は、判断にどう影響しましたか。", "インタビュー", [], "日程の影響を探す", "影響の有無と内容を整理"),
            ]
        else:
            subject = _subject_from_objective(objective)
            questions = [
                _q("Q1", f"{subject}について、これまでの経験を教えてください。", "インタビュー", [], "経験を把握する", "経験の種類を整理"),
                _q("Q2", "利用や検討のきっかけを教えてください。", "インタビュー", [], "きっかけを探す", "きっかけ別に整理"),
                _q("Q3", "利用をためらった理由を教えてください。", "インタビュー", [], "障壁を探す", "理由と判断時点を整理"),
                _q("Q4", "費用は、判断にどう影響しましたか。", "インタビュー", [], "費用の影響を探す", "影響の有無と内容を整理"),
                _q("Q5", "どんな条件なら利用しやすいですか。", "インタビュー", [], "利用条件を探す", "条件を分類し、後続アンケート候補にする"),
            ]
    else:
        questions = [_screening_question(target)]
    if "インタビュー" not in research_method and _has(objective + hypothesis, "熱海"):
        questions.extend([
            _q("Q2", "これまでの熱海への旅行経験について、最も近いものを選んでください。", "単一選択", ["訪れたことがある", "検討したが訪れなかった", "知っていたが検討しなかった", "ほとんど知らなかった"], "対象者と訪問段階を確認" if "検討" in target else "訪問段階を確認", "「検討したが訪れなかった」を対象に集計" if "未訪問" in target else ("上位2区分を対象に集計" if "検討" in target else "段階別の人数・割合を集計"), "対象外の区分は以降を終了" if "検討" in target else ""),
            _q("Q3", "熱海を訪れなかった理由を選んでください。", "複数選択", ["費用", "時間", "情報不足", "同行者", "魅力を感じない", "他の旅行先を選んだ", "その他"], "訪問の障壁を確認", "Q2の段階別に回答割合を比較", "Q2で未訪問の人"),
            _q("Q4", _condition_question(hypothesis), "5段階評価", ["全く思わない", "あまり思わない", "どちらともいえない", "やや思う", "とても思う"], "採用した仮説を確認", "平均と上位2段階の割合を算出"),
            _q("Q5", "熱海に行きやすくなる条件があれば教えてください。", "自由記述", [], "想定外の条件を探す", "回答を分類し、次の選択肢候補にする"),
        ])
    elif "インタビュー" not in research_method and _has(objective + hypothesis, "施策", "候補", "比較", "案"):
        questions.extend([
            _q("Q2", "最も利用したい案を1つ選んでください。", "単一選択", ["候補A", "候補B", "候補C", "利用したいものはない"], "第一選択を確認", "案別の選択率を比較"),
            _q("Q3", "選んだ理由を教えてください。", "複数選択", ["費用", "時間", "内容", "安心感", "使いやすさ", "その他"], "選択理由を確認", "案別に理由の割合を比較"),
            _q("Q4", "その案を実際に利用したいと思いますか。", "5段階評価", ["全く思わない", "あまり思わない", "どちらともいえない", "やや思う", "とても思う"], "利用意向を確認", "平均と上位2段階の割合を比較"),
            _q("Q5", "利用をためらう理由があれば教えてください。", "自由記述", [], "想定外の障壁を探す", "回答を分類して次の選択肢候補にする"),
        ])
    elif "インタビュー" not in research_method:
        if _has(hypothesis, "経験", "違い"):
            questions.extend([
                _q("Q2", "これまでに利用したことがありますか。", "単一選択", ["現在利用している", "以前利用した", "検討した", "利用していない"], "利用経験を確認", "経験別の人数を集計"),
                _q("Q3", "利用時に重視することを選んでください。", "複数選択", ["費用", "時間", "内容", "使いやすさ", "安心感", "その他"], "重視条件を確認", "経験別に回答割合を比較"),
                _q("Q4", "今後、利用したいと思いますか。", "5段階評価", ["全く思わない", "あまり思わない", "どちらともいえない", "やや思う", "とても思う"], "利用意向を確認", "経験別に平均を比較"),
                _q("Q5", "ほかに重視する条件があれば教えてください。", "自由記述", [], "想定外の条件を探す", "回答を分類する"),
            ])
        else:
            questions.extend([
                _q("Q2", "利用を妨げている理由を選んでください。", "複数選択", ["費用", "時間", "情報不足", "使いにくさ", "必要性を感じない", "その他"], "障壁を確認", "理由別の回答割合を集計"),
                _q("Q3", "最も大きな理由を1つ選んでください。", "単一選択", ["費用", "時間", "情報不足", "使いにくさ", "必要性を感じない", "その他"], "主な障壁を確認", "第一理由の割合を集計"),
                _q("Q4", "条件が改善すれば、利用したいと思いますか。", "5段階評価", ["全く思わない", "あまり思わない", "どちらともいえない", "やや思う", "とても思う"], "条件改善後の意向を確認", "平均と上位2段階の割合を算出"),
                _q("Q5", "必要な条件があれば教えてください。", "自由記述", [], "想定外の条件を探す", "回答を分類する"),
            ])

    max_questions = 5 if duration <= 5 else min(8, 5 + (duration - 5) // 2)
    questions = questions[:max_questions]
    quality_checks = check_questions(questions, objective)
    warnings = []
    if not known:
        warnings.append("既存調査と重複していないか確認してください。")
    if any(check["status"] != "OK" for check in quality_checks):
        warnings.append("品質チェックで要確認の設問があります。")
    return {
        "objective": objective,
        "target": target,
        "hypothesis": hypothesis,
        "instrumentType": "インタビュー" if "インタビュー" in research_method else "アンケート",
        "recommendedMethod": research_method,
        "known": known,
        "duration": duration,
        "estimatedMinutes": max(2, round(len(questions) * 0.7)),
        "questions": questions,
        "qualityChecks": quality_checks,
        "warnings": warnings,
        "decisionRule": "採用した仮説に対応する回答分布と群の差を確認し、仮説を残すか見直す。",
        "wordingRule": "伝わる範囲で短くする。必要な条件・期間・対象は省略しない。",
    }


def generate_consolidated_questionnaire(
    objective: str,
    target: str,
    candidates: list[dict[str, Any]],
    duration: int = 7,
) -> dict[str, Any]:
    """関連する複数仮説を、共通設問を持つ1つの調査へ統合する。"""
    objective, target = clean(objective), clean(target)
    if not 1 <= len(candidates) <= 4:
        raise ValueError("検証候補を1〜4件選んでください")
    survey_candidates = [item for item in candidates if item.get("method") == "アンケート"]
    separate = [item for item in candidates if item.get("method") != "アンケート"]
    if not survey_candidates:
        raise ValueError("アンケートへまとめられる候補を1件以上選んでください")

    is_atami = "熱海" in objective + " ".join(item.get("hypothesis", "") for item in candidates)
    questions = [_screening_question(target)]
    mapping: list[dict[str, Any]] = []
    if is_atami:
        questions.append(_q("Q2", "これまでの熱海への旅行経験について、最も近いものを選んでください。", "単一選択", ["訪れたことがある", "検討したが訪れなかった", "知っていたが検討しなかった", "ほとんど知らなかった"], "訪問段階を確認", "段階別の人数・割合を集計"))

    for candidate in survey_candidates:
        qid = f"Q{len(questions) + 1}"
        cid = candidate.get("id", "")
        hypothesis = candidate.get("hypothesis", "")
        if is_atami and (cid == "H-COST" or _has(hypothesis, "総額", "費用")):
            question = _q(qid, "熱海旅行の費用について、最も近いものを選んでください。", "単一選択", ["予算内だと思う", "高そうだが総額は分からない", "総額は分かるが高い", "調べていない", "分からない"], "費用の高さと不透明さを区別", "訪問段階別に回答割合を比較")
        elif is_atami and (cid == "H-PLAN" or _has(hypothesis, "過ごし方", "情報")):
            question = _q(qid, "熱海での過ごし方を具体的にイメージできますか。", "5段階評価", ["全くできない", "あまりできない", "どちらともいえない", "ややできる", "よくできる"], "旅行イメージを確認", "訪問段階別に平均を比較")
        elif is_atami and (cid == "H-SCHEDULE" or _has(hypothesis, "同行者", "日程")):
            question = _q(qid, "熱海旅行を妨げる条件を選んでください。", "複数選択", ["同行者が見つからない", "日程が合わない", "時間が足りない", "費用", "特にない", "その他"], "生活上の障壁を確認", "訪問段階別に回答割合を比較")
        elif cid == "H-SEGMENT" or _has(hypothesis, "経験", "違い"):
            question = _q(qid, "利用時に重視することを選んでください。", "複数選択", ["費用", "時間", "内容", "使いやすさ", "安心感", "その他"], "重視条件を確認", "経験別に回答割合を比較")
        else:
            question = _q(qid, "利用を妨げる理由を選んでください。", "複数選択", ["費用", "時間", "情報不足", "使いにくさ", "必要性を感じない", "その他"], "主な障壁を確認", "理由別の回答割合を集計")
        questions.append(question)
        mapping.append({"questionId": qid, "candidateId": cid, "hypothesis": hypothesis})

    outcome_id = f"Q{len(questions) + 1}"
    outcome_text = "今後1年以内に熱海を訪れたいと思いますか。" if is_atami else "今後、利用したいと思いますか。"
    questions.append(_q(outcome_id, outcome_text, "5段階評価", ["全く思わない", "あまり思わない", "どちらともいえない", "やや思う", "とても思う"], "共通の成果指標を確認", "各仮説設問との関係を比較"))
    mapping.append({"questionId": outcome_id, "candidateId": "COMMON-OUTCOME", "hypothesis": "共通成果指標"})
    checks = check_questions(questions, objective)
    return {
        "objective": objective,
        "target": target,
        "instrumentType": "統合アンケート",
        "hypotheses": survey_candidates,
        "questions": questions,
        "hypothesisMap": mapping,
        "qualityChecks": checks,
        "separateFollowups": separate,
        "estimatedMinutes": max(3, round(len(questions) * 0.7)),
        "decisionRule": "共通の訪問・利用意向と各仮説設問の関係を比較し、次に優先する施策仮説を選ぶ。",
        "warnings": ["同じ対象者・募集方法で1回実施する想定です。"] + (["インタビュー向きの候補は別枠にしました。"] if separate else []),
    }
