"""学生PBL向けの調査目的整理。

初版は外部送信を行わず、熱海DR3で必要な問いに限定して再現可能な候補を返す。
将来のLLM実装でも同じレスポンス契約を使用する。
"""
from __future__ import annotations

import json
import re
from typing import Any

import llm_adapter


GOALS = {
    "conversion": {
        "route": "atami_conversion",
        "objective": "熱海を旅行先として検討した学生が、訪問に至らなかった障壁を明らかにする",
        "target": "大学生調査のうち、熱海を検討したが未訪問の学生",
        "decision": "訪問直前の障壁を減らすために、どの情報・過ごし方を次に検証するか",
        "analysis": "未訪問者の中から検討経験者を抽出し、旅行意欲・障壁・希望体験を確認する",
        "why": "熱海に無関心な人ではなく、候補にしたのに最後に選ばなかった人を見ると、施策で変えられる障壁を探しやすいためです。",
    },
    "policy": {
        "route": "atami_policy_test",
        "objective": "既存調査から若者向け施策仮説を整理し、次に比較検証すべき候補を明らかにする",
        "target": "熱海を認知・検討している大学生",
        "decision": "3つの施策仮説のうち、どれをどの条件で試すか",
        "analysis": "学生調査の障壁・希望体験を根拠に3案を作り、未確認事項を比較調査へ変換する",
        "why": "既存データだけで施策効果は断定できないため、根拠のある候補へ絞って同じ回答者・同じ尺度で比べるためです。",
    },
}


AUDIENCE_WORDS = ("学生", "大学生", "若者", "若年", "Z世代", "10代", "20代", "学部", "院生", "大学院")

ATAMI_BARRIER_REPLY = """ご質問を、分析できる形に整理しました。

明らかにしたいこと：
熱海を候補にしたが訪問しなかった学生の障壁を把握する

見る対象：
大学生・大学院生のうち、
「熱海を旅行先として検討したことがある」かつ「実際には訪問していない」回答者

使う設問：
・熱海に行こうと考えたことがあるか
・実際に熱海へ訪問したことがあるか
・行こうと思ったのに行かなかった理由
・熱海でしたいこと
・大学種別、学部生/院生、性別

分析の進め方：
1. 対象者を抽出する
2. 訪問しなかった理由を集計する
3. 属性別に理由と「熱海でしたいこと」を比較する
4. 障壁を下げる施策仮説を整理する

この内容で分析を進めますか？"""


def _is_atami_barrier_question(text: str) -> bool:
    """一時デモ用: 訪問検討から未訪問に至った障壁の相談を安定して判定する。"""
    return (
        "熱海" in text
        and "実際には" in text
        and "何が障壁" in text
        and any(word in text for word in ("候補", "検討", "行こうと考え"))
        and any(word in text for word in ("訪問しな", "未訪問", "来なかった", "行かなかった"))
        and any(word in text for word in ("障壁", "理由", "要因", "妨げ"))
    )


def clarify(message: str, context: str = "") -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        raise ValueError("今考えていることを短く入力してください")
    combined = f"{str(context or '').strip()} {text}".strip()
    lower = combined.lower()
    if _is_atami_barrier_question(text):
        brief = dict(GOALS["conversion"])
        brief.update({
            "objective": "熱海を候補にしたが訪問しなかった学生の障壁を把握する",
            "target": "大学生・大学院生のうち、熱海を旅行先として検討したことがあり、実際には訪問していない回答者",
            "decision": "障壁を下げるために、次に検証する施策仮説を整理する",
            "analysis": "対象者を抽出し、未訪問理由を集計したうえで、属性別に理由と熱海でしたいことを比較する",
        })
        return {
            "status": "proposal",
            "reply": ATAMI_BARRIER_REPLY,
            "brief": brief,
            "choices": [],
        }
    if "熱海" not in combined:
        return {
            "status": "unsupported",
            "reply": "初版は熱海の大学生調査に限定しています。熱海についての相談なら、下の候補から続けられます。別地域で使うには、その地域の調査データと設問対応表が必要です。",
            "required": ["対象地域のアンケート", "調査対象・募集方法・時期", "明らかにしたい判断"],
            "choices": [
                {"id": "conversion", "label": "熱海で来なかった障壁を知る", "message": "学生が熱海を検討したのに、訪問しなかった障壁を明らかにしたい"},
                {"id": "policy", "label": "熱海で次の施策を絞る", "message": "熱海の若者向け施策を整理し、次にどれを検証するか決めたい"},
            ],
        }
    policy_words = ("施策", "チル", "プラン", "どれ", "比較", "提案", "方向性")
    conversion_words = ("来ない", "来なか", "訪問", "選ば", "離脱", "障壁", "行かな", "理由", "候補にした")
    latest = text.lower()
    policy_override = any(phrase in latest for phrase in ("理由ではなく", "障壁ではなく", "来なかった理由ではなく"))
    conversion_override = any(phrase in latest for phrase in ("施策ではなく", "施策じゃなく", "施策ではない"))
    latest_policy = any(word in latest for word in policy_words)
    latest_conversion = any(word in latest for word in conversion_words)
    if policy_override:
        key = "policy"
    elif conversion_override:
        key = "conversion"
    elif latest_policy and not latest_conversion:
        key = "policy"
    elif latest_conversion and not latest_policy:
        key = "conversion"
    elif latest_policy and latest_conversion:
        policy_position = max(latest.rfind(word) for word in policy_words if word in latest)
        conversion_position = max(latest.rfind(word) for word in conversion_words if word in latest)
        key = "conversion" if conversion_position > policy_position else "policy"
    elif any(word in lower for word in policy_words):
        key = "policy"
    elif any(word in lower for word in conversion_words):
        key = "conversion"
    else:
        return {
            "status": "needs_choice",
            "reply": "いまの話には『来なかった理由』と『施策を選ぶこと』の2つが含まれています。今回は、どちらを先に明らかにしたいですか？",
            "choices": [
                {"id": "conversion", "label": "来なかった障壁を知りたい", "message": "学生が熱海を検討したのに、訪問しなかった障壁を明らかにしたい"},
                {"id": "policy", "label": "次に試す施策を絞りたい", "message": "若者向け施策の候補を整理し、次にどれを検証するか決めたい"},
            ],
        }
    brief = dict(GOALS[key])
    if key == "conversion" and any(word in text for word in ("属性", "学部", "院生", "大学院")):
        brief.update({
            "objective": "熱海を旅行先として検討したが未訪問の学生について、訪問障壁と属性による違いを明らかにする",
            "decision": "属性ごとに異なる障壁を踏まえ、次に確認すべき情報・過ごし方を決める",
            "analysis": "検討したが未訪問の学生を抽出し、学籍などの属性別に障壁・希望体験を比較する",
            "why": "熱海に無関心な人ではなく、候補にしたのに選ばなかった人を属性別に見ると、誰にどの障壁が大きいかを確認できるためです。",
        })
    return {
        "status": "proposal",
        "reply": "つまり、次のことを明らかにしたいという理解で合っていますか？",
        "brief": brief,
        "choices": [],
    }


def validate_brief(brief: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(brief, dict):
        raise ValueError("分析前に対話で目的を確定してください")
    cleaned = {key: str(brief.get(key, "")).strip() for key in ("route", "objective", "target", "decision")}
    if not all(cleaned.values()):
        raise ValueError("目的・対象・判断・分析ルートが不足しています")
    if cleaned["route"] not in {"atami_conversion", "atami_policy_test"}:
        raise ValueError("この分析ルートには対応していません")
    # LLMが生成したbriefは言い回しが多様なため、補助フィールドも含めてキーワードを判定する
    supplementary = " ".join(str(brief.get(key, "")) for key in ("analysis", "analysisHint", "why"))
    combined = " ".join(cleaned.values()) + " " + supplementary
    # ルートが熱海の2分析に限定済みのため、文言は「熱海」か対象層のどちらかが読み取れれば通す
    if "熱海" not in combined and not any(word in combined for word in AUDIENCE_WORDS):
        raise ValueError(
            "現在の実データ分析は、熱海の学生・若者に関する問いだけに対応しています。"
            "問いに『熱海』または対象層（例：熱海を検討したが未訪問の大学生）を含めて確定し直してください"
        )
    if cleaned["route"] == "atami_conversion" and not any(word in combined for word in ("訪問", "未訪問", "障壁", "来な", "行かな", "選ば", "離脱")):
        raise ValueError("確定した問いと『訪問障壁』分析が一致しません。問いに『訪問しなかった障壁』を含めるか、分析ルートを変えてください")
    if cleaned["route"] == "atami_policy_test" and not any(word in combined for word in ("施策", "仮説", "候補", "検証", "比較", "提案")):
        raise ValueError("確定した問いと『施策仮説』分析が一致しません。問いに『検証したい施策』を含めるか、分析ルートを変えてください")
    for key in ("why", "analysis"):
        value = str(brief.get(key, "")).strip()
        if value:
            cleaned[key] = value
    return cleaned


# --- Session dialogue (v3): LLMによる目的整理。個票・Excel本文は送らない。 ---

SESSION_SYSTEM_PROMPT = (
    "あなたは熱海DMO×学生PBLの調査目的整理アシスタントです。"
    "学生の発言だけから、目的・対象・支援する判断・分析ルートの候補を整理してください。"
    "数値、割合、因果効果を作ってはいけません。分析は別のPythonエンジンが行います。"
    "一度に複数の質問をせず、曖昧さが判断を変える場合だけ一問だけ聞いてください。"
    "既に答えられた内容を再質問しないでください。熱海に無関係な一般相談には対応範囲を説明してください。"
    "briefCandidateのtargetには『熱海』と対象層（大学生・若者など）を必ず明示してください。"
    "replyは画面にそのまま表示されます。140字以内を基本に、最大3ブロックまでにしてください。"
    "長い一文を避け、必要なら改行と「- 」の箇条書きを使ってください。"
    "replyにMarkdown見出し、#、■、JSON文字列、エスケープされた\\nを書かないでください。"
    "出力は次のJSON以外に何も書かないでください: "
    '{"reply": "string", "briefCandidate": {"objective": "string", "target": "string", '
    '"decision": "string", "route": "atami_conversion | atami_policy_test | undecided", '
    '"analysisHint": "string"}, "missingInformation": ["string"], "readyToConfirm": true}'
)

MAX_REPLY_CHARS = 320
MAX_FIELD_CHARS = 180
MAX_MISSING = 3
BRIEF_ROUTES = {"atami_conversion", "atami_policy_test", "undecided"}
RECENT_MESSAGE_LIMIT = 8


def _project_context(project: dict[str, Any]) -> str:
    parts = [
        f"連携先: {project.get('partner', '')}" if project.get("partner") else "",
        f"背景: {project.get('background', '')}" if project.get("background") else "",
        f"上位目的: {project.get('current_objective') or project.get('currentObjective', '')}",
    ]
    return " / ".join(part for part in parts if part)[:800]


def _survey_schema_context(project: dict[str, Any]) -> str:
    names: list[str] = []
    for material in project.get("materials", []):
        if material.get("kind") == "アンケートデータ":
            columns = (material.get("summary") or {}).get("columns", [])
            names.extend(str(col.get("shortName") or col.get("name", "")) for col in columns)
    return "、".join(name for name in names if name)[:800]


def _history_context(messages: list[dict[str, Any]]) -> str:
    recent = messages[-RECENT_MESSAGE_LIMIT:]
    older = messages[:-RECENT_MESSAGE_LIMIT] if len(messages) > RECENT_MESSAGE_LIMIT else []
    lines = []
    if older:
        summary = " ".join(m.get("content", "") for m in older if m.get("role") == "user")[:400]
        if summary:
            lines.append(f"[以前の要約] {summary}")
    for m in recent:
        role = "学生" if m.get("role") == "user" else "AI"
        lines.append(f"{role}: {m.get('content', '')}")
    return "\n".join(lines)


def _parse_json_block(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("JSON形式ではありません")
    return json.loads(text[start:end + 1])


def _validate_session_output(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("応答形式が不正です")
    reply = _clean_reply_text(payload.get("reply", ""))
    if not (1 <= len(reply) <= MAX_REPLY_CHARS):
        raise ValueError("replyの長さが不正です")
    ready_value = payload.get("readyToConfirm", False)
    if not isinstance(ready_value, bool):
        raise ValueError("readyToConfirmはbooleanで指定してください")
    ready = ready_value
    brief = payload.get("briefCandidate") or {}
    if not isinstance(brief, dict):
        raise ValueError("briefCandidateが不正です")
    cleaned_brief: dict[str, str] = {}
    for key in ("objective", "target", "decision", "analysisHint"):
        value = str(brief.get(key, "")).strip()
        if ready and not (1 <= len(value) <= MAX_FIELD_CHARS):
            raise ValueError(f"briefCandidate.{key}の長さが不正です")
        if value and len(value) > MAX_FIELD_CHARS:
            raise ValueError(f"briefCandidate.{key}の長さが不正です")
        cleaned_brief[key] = value
    route = str(brief.get("route", "undecided")).strip()
    if route not in BRIEF_ROUTES:
        raise ValueError("routeが不正です")
    cleaned_brief["route"] = route
    missing = payload.get("missingInformation") or []
    if not isinstance(missing, list) or len(missing) > MAX_MISSING:
        raise ValueError("missingInformationが不正です")
    return {
        "reply": reply,
        "briefCandidate": cleaned_brief,
        "missingInformation": [str(m).strip() for m in missing if str(m).strip()][:MAX_MISSING],
        "readyToConfirm": ready,
    }


def _clean_reply_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[¥￥\\]\s*r\s*[¥￥\\]\s*n", "\n", text)
    text = re.sub(r"[¥￥\\]\s*n", "\n", text)
    text = re.sub(r"\s*(?:[#＃]?\s*n\s*)?■\s*", "\n- ", text)
    text = re.sub(r"\n\s*[・*]\s*", "\n- ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _fallback_reply(message: str, context: str) -> dict[str, Any]:
    """LLM未接続・失敗時のルールベースフォールバック。AI返答として装わない。"""
    dialogue = clarify(message, context)
    brief = dialogue.get("brief") or {}
    ready = dialogue.get("status") == "proposal"
    if dialogue.get("status") == "needs_choice":
        missing = ["明らかにしたい問いをどちらかに絞ってください"]
    else:
        missing = list(dialogue.get("required", []))[:MAX_MISSING]
    return {
        "reply": dialogue.get("reply", ""),
        "briefCandidate": {
            "objective": brief.get("objective", ""),
            "target": brief.get("target", ""),
            "decision": brief.get("decision", ""),
            "route": brief.get("route", "undecided"),
            "analysisHint": brief.get("analysis", ""),
        },
        "missingInformation": missing,
        "readyToConfirm": ready,
    }


def _try_llm(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    result = llm_adapter.complete(system_prompt, user_prompt)
    payload = _validate_session_output(_parse_json_block(result.text))
    payload["model"] = {
        "provider": result.provider,
        "model": result.model,
        "inputTokens": result.input_tokens,
        "outputTokens": result.output_tokens,
        "usedFallback": False,
    }
    return payload


def _try_llm_with_transport_retry(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    try:
        return _try_llm(system_prompt, user_prompt)
    except llm_adapter.LLMError as exc:
        if exc.code not in {"timeout", "connection_error"}:
            raise
        return _try_llm(system_prompt, user_prompt)


def build_session_reply(project: dict[str, Any], message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """個票・Excel本文を含まない文脈だけでLLMに問いを整理させる。失敗時はルールベースへ落ちる。"""
    text = str(message or "").strip()
    if not text:
        raise ValueError("メッセージを入力してください")
    context_text = _history_context(history)
    if _is_atami_barrier_question(text):
        payload = _fallback_reply(text, context_text)
        payload["model"] = {
            "provider": "rule",
            "model": "temporary-atami-barrier-plan",
            "inputTokens": 0,
            "outputTokens": 0,
            "usedFallback": False,
        }
        return payload
    if llm_adapter.is_enabled():
        user_prompt = (
            f"[プロジェクト] {_project_context(project)}\n"
            f"[設問一覧] {_survey_schema_context(project)}\n"
            f"[会話]\n{context_text}\n学生: {text}\n上のJSON形式だけで返答してください。"
        )
        repair_prompt = user_prompt + "\n直前の出力はJSON形式が不正でした。指定のJSONのみで出し直してください。"
        try:
            return _try_llm_with_transport_retry(SESSION_SYSTEM_PROMPT, user_prompt)
        except llm_adapter.LLMError:
            pass
        except (ValueError, json.JSONDecodeError):
            try:
                return _try_llm_with_transport_retry(SESSION_SYSTEM_PROMPT, repair_prompt)
            except (llm_adapter.LLMError, ValueError, json.JSONDecodeError):
                pass
    payload = _fallback_reply(text, context_text)
    llm_status = llm_adapter.status()
    payload["model"] = {
        "provider": llm_status.get("provider", "disabled"),
        "model": llm_status.get("model", ""),
        "inputTokens": 0,
        "outputTokens": 0,
        "usedFallback": True,
        "errorCode": llm_status.get("reason", ""),
        "errorMessage": llm_status.get("message", ""),
    }
    return payload
