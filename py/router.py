"""server.py のディスパッチをブラウザ用に移した層。

ローカル版は HTTP サーバー（server.py）が `/api/*` を受けていた。公開版は
サーバーが無いので、ブラウザ内の Python（Pyodide）が同じパスを同じ形で処理する。
JS 側の api() が handle() を呼ぶだけで、フロントエンド（app.js）の呼び出しは
ローカル版と同じまま動く。

ローカル版との違いは 3 点だけ:
  - アクセスコード認証がない（静的サイトなので守る対象のサーバーが無い）
  - /api/atami-dr3/saved が使えない（PC の runtime/uploads にある実データを読む機能）
  - AI対話は無効（llm_adapter.py の冒頭を参照）
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import analysis_engine
import atami_dr3
import evidence_engine
import llm_adapter
import project_store
import questionnaire
import questionnaire_import
import research_dialogue
import session_store

DATASETS = analysis_engine.DatasetStore()
PROJECTS = project_store.ProjectStore()
SESSIONS = session_store.SessionStore(PROJECTS)


def handle(method: str, raw_path: str, body: dict[str, Any] | None = None) -> Any:
    """(method, path, body) を受けて server.py と同じ形の dict を返す。"""
    body = body or {}
    path = raw_path.split("?", 1)[0]

    if method == "GET" and path == "/api/health":
        return {"ok": True, "name": "Survey Insight Copilot", "mode": "web"}
    if method == "GET" and path == "/api/llm/status":
        return {"llm": llm_adapter.status()}
    if method == "GET" and path == "/api/workspace":
        project = PROJECTS.get_primary_project()
        return {
            "project": {
                "id": project["id"],
                "title": project["title"],
                "partner": project.get("partner", ""),
                "background": project.get("background", ""),
                "materials": [
                    {"id": m["id"], "kind": m["kind"], "title": m["title"]} for m in project["materials"]
                ],
            },
            "recentSessions": SESSIONS.list_recent(project["id"]),
            "llm": llm_adapter.status(),
        }

    # ---- 研究対話（LLM無効時は research_dialogue がルールベースへ落ちる） ----
    if method == "POST" and path == "/api/research-dialogue/clarify":
        return {"dialogue": research_dialogue.clarify(body.get("message", ""))}
    if method == "POST" and path == "/api/research-dialogue/validate":
        return {"brief": research_dialogue.validate_brief(body.get("brief"))}
    if method == "GET" and path == "/api/conversations":
        return {"conversations": PROJECTS.list_conversations()}
    if method == "POST" and path == "/api/conversations":
        message = body.get("message", "")
        dialogue = research_dialogue.clarify(message)
        conversation = PROJECTS.create_conversation(
            message, dialogue, body.get("projectId", ""), body.get("materialId", ""))
        return {"conversation": conversation}

    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["api", "conversations"]:
        conversation_id = parts[2]
        if method == "GET" and len(parts) == 3:
            return {"conversation": PROJECTS.get_conversation(conversation_id)}
        if method == "POST" and parts[3:] == ["messages"]:
            existing = PROJECTS.get_conversation(conversation_id)
            context = " ".join(
                item.get("text", "") for item in existing["messages"] if item.get("role") == "user")
            dialogue = research_dialogue.clarify(body.get("message", ""), context)
            return {"conversation": PROJECTS.append_conversation(
                conversation_id, body.get("message", ""), dialogue)}
        if method == "POST" and parts[3:] == ["brief"]:
            brief = research_dialogue.validate_brief(body.get("brief"))
            return {"conversation": PROJECTS.save_conversation_brief(conversation_id, brief)}

    # ---- 熱海DR3 固定分析 ----
    if method == "POST" and path == "/api/atami-dr3/saved":
        # ローカル版は runtime/uploads にある実アンケートを読む。公開版は実データを
        # 同梱しないため使えない。手元のファイルを選ぶ /analyze を使う。
        raise ValueError(
            "公開版には保存済みアンケートを同梱していません。"
            "お手元の回答ファイルを選択して分析してください。")
    if method == "POST" and path == "/api/atami-dr3/analyze":
        brief = research_dialogue.validate_brief(body.get("brief"))
        filename = Path(str(body.get("name", ""))).name
        if not filename:
            raise ValueError("ファイル名がありません")
        try:
            raw = base64.b64decode(body.get("data", ""), validate=True)
        except Exception as exc:
            raise ValueError("ファイルを受信できませんでした") from exc
        df = analysis_engine.read_table(filename, raw)
        return {"result": atami_dr3.analyze(df, filename, brief["route"])}
    if method == "POST" and path == "/api/atami-dr3/dataset":
        brief = research_dialogue.validate_brief(body.get("brief"))
        dataset_id = str(body.get("datasetId", ""))
        df = DATASETS.get(dataset_id)
        meta = DATASETS.meta(dataset_id)
        return {"result": atami_dr3.analyze(df, meta.get("filename", "dataset"), brief["route"])}

    # ---- プロジェクト ----
    if method == "GET" and path == "/api/projects":
        return {"projects": PROJECTS.list_projects()}
    if method == "POST" and path == "/api/projects":
        return {"project": PROJECTS.create_project(
            body.get("title", ""), body.get("decisionQuestion", ""), body.get("target", ""))}

    if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
        project_id = parts[2]
        if method == "GET" and len(parts) == 3:
            project = PROJECTS.get_project(project_id)
            for material in project["materials"]:
                if material["kind"] == "アンケートデータ" and material.get("file_path"):
                    file_path = Path(material["file_path"])
                    if file_path.exists():
                        material["dataset"] = DATASETS.register(
                            material["filename"], file_path.read_bytes())
            return {"project": project}
        if method == "PATCH" and len(parts) == 3:
            return {"project": PROJECTS.update_project(project_id, body)}
        if method == "POST" and parts[3:] == ["sessions"]:
            return {"session": SESSIONS.create_session(
                project_id, title=body.get("title", ""),
                dataset_material_id=body.get("datasetMaterialId", ""),
                survey_plan_id=body.get("surveyPlanId", ""))}
        if method == "POST" and parts[3:] == ["materials", "note"]:
            return {"material": PROJECTS.add_note(
                project_id, body.get("kind", "学生の気づき"),
                body.get("title", ""), body.get("content", ""))}
        if method == "POST" and parts[3:] == ["materials", "dataset"]:
            filename = Path(str(body.get("name", ""))).name
            if not filename:
                raise ValueError("ファイル名がありません")
            try:
                raw = base64.b64decode(body.get("data", ""), validate=True)
            except Exception as exc:
                raise ValueError("ファイルを受信できませんでした") from exc
            summary = DATASETS.register(filename, raw)
            profile = {
                "population": str(body.get("population", "")).strip(),
                "recruitment": str(body.get("recruitment", "")).strip(),
                "period": str(body.get("period", "")).strip(),
            }
            if not all(profile.values()):
                raise ValueError("調査対象・募集方法・調査時期を入力してください")
            summary["studyProfile"] = profile
            material = PROJECTS.add_dataset(project_id, filename, raw, summary)
            material["dataset"] = summary
            return {"material": material}
        if method == "POST" and parts[3:] == ["questionnaires", "parse"]:
            filename = Path(str(body.get("name", ""))).name
            if not filename:
                raise ValueError("ファイル名がありません")
            try:
                raw = base64.b64decode(body.get("data", ""), validate=True)
            except Exception as exc:
                raise ValueError("ファイルを受信できませんでした") from exc
            return {"draft": questionnaire_import.parse_questionnaire(filename, raw)}
        if method == "POST" and parts[3:] == ["materials", "questionnaire"]:
            filename = Path(str(body.get("name", ""))).name
            if not filename:
                raise ValueError("ファイル名がありません")
            if body.get("verified") is not True:
                raise ValueError("原本との照合を確認してください")
            try:
                raw = base64.b64decode(body.get("data", ""), validate=True)
            except Exception as exc:
                raise ValueError("ファイルを受信できませんでした") from exc
            questions = questionnaire_import.validate_confirmed_questions(body.get("questions"))
            summary = {
                "questions": questions,
                "questionCount": len(questions),
                "verified": True,
                "verifiedAt": str(body.get("verifiedAt", "")).strip(),
                "parserWarnings": body.get("parserWarnings", []),
            }
            return {"material": PROJECTS.add_questionnaire(project_id, filename, raw, summary)}
        if method == "POST" and parts[3:] == ["organize"]:
            project = PROJECTS.get_project(project_id)
            organized = evidence_engine.organize_project(project)
            PROJECTS.save_organization(project_id, organized)
            return {"organization": organized}
        if method == "POST" and parts[3:] == ["analyses"]:
            return {"analysis": PROJECTS.save_analysis(
                project_id, body.get("question", ""), body.get("result", {}))}
        if method == "POST" and parts[3:] == ["research-plans"]:
            result = questionnaire.generate_consolidated_questionnaire(
                body.get("objective", ""), body.get("target", ""),
                body.get("candidates", []), body.get("duration", 7))
            saved = PROJECTS.save_research_plan(project_id, result)
            return {"researchPlan": result, "saved": saved}

    # ---- 調査計画・セッション ----
    if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "survey-plans"]:
        return {"surveyPlan": SESSIONS.get_survey_plan(parts[2])}
    if len(parts) >= 3 and parts[:2] == ["api", "sessions"]:
        session_id = parts[2]
        if method == "GET" and len(parts) == 3:
            return {"session": SESSIONS.get_session(session_id)}
        if method == "PUT" and parts[3:] == ["dataset"]:
            return {"session": SESSIONS.set_dataset(session_id, body.get("datasetMaterialId", ""))}
        if method == "POST" and parts[3:] == ["messages"]:
            session, exchange = SESSIONS.post_message(session_id, body.get("message", ""))
            return {"session": session, "exchange": exchange}
        if method == "POST" and parts[3:] == ["analyze-from-message"]:
            session, exchange = SESSIONS.analyze_from_message(session_id, body.get("message", ""))
            return {"session": session, "exchange": exchange}
        if method == "POST" and parts[3:] == ["confirm-question"]:
            return {"session": SESSIONS.confirm_question(session_id, body.get("brief", body))}
        if method == "POST" and parts[3:] == ["analyze"]:
            return {"session": SESSIONS.analyze(session_id)}
        if method == "POST" and parts[3:] == ["survey-plans"]:
            return {"surveyPlan": SESSIONS.create_survey_plan(session_id)}
        if method == "POST" and parts[3:] == ["column-mapping"]:
            return {"session": SESSIONS.save_column_mapping(session_id, body.get("mapping", {}))}
        if method == "PATCH" and parts[3:] == ["ui-state"]:
            return {"session": SESSIONS.set_ui_state(session_id, body)}
        if method == "POST" and parts[3:] == ["archive"]:
            return {"session": SESSIONS.archive(session_id)}

    # ---- データ・分析・設問 ----
    if method == "POST" and path == "/api/datasets":
        filename = Path(str(body.get("name", ""))).name
        if not filename:
            raise ValueError("ファイル名がありません")
        try:
            raw = base64.b64decode(body.get("data", ""), validate=True)
        except Exception as exc:
            raise ValueError("ファイルを受信できませんでした") from exc
        return {"dataset": DATASETS.register(filename, raw)}
    if method == "POST" and path == "/api/analysis/plan":
        dataset_id = body.get("datasetId", "")
        df = DATASETS.get(dataset_id)
        return {"plan": analysis_engine.build_plan(
            df, DATASETS.meta(dataset_id), body.get("question", ""))}
    if method == "POST" and path == "/api/analysis/run":
        dataset_id = body.get("datasetId", "")
        df = DATASETS.get(dataset_id)
        result = analysis_engine.run_analysis(df, DATASETS.meta(dataset_id), body.get("plan", {}))
        if body.get("projectId"):
            PROJECTS.save_analysis(body["projectId"], body.get("plan", {}).get("question", ""), result)
        return {"result": result}
    if method == "POST" and path == "/api/questionnaire/generate":
        return {"questionnaire": questionnaire.generate_questionnaire(
            body.get("objective", ""), body.get("target", ""), body.get("duration", 5),
            body.get("known", ""), body.get("hypothesis", ""),
            body.get("researchMethod", "アンケート"))}
    if method == "POST" and path == "/api/questionnaire/check":
        return {"qualityChecks": questionnaire.check_questions(
            body.get("questions", []), body.get("objective", ""))}
    if method == "POST" and path == "/api/hypotheses/suggest":
        return {"suggestion": questionnaire.suggest_hypotheses(
            body.get("objective", ""), body.get("target", ""), body.get("known", ""))}

    raise ValueError(f"APIが見つかりません: {method} {path}")


def handle_json(method: str, path: str, body_json: str) -> str:
    """JS から呼ぶ入口。server.py と同じHTTPステータスの割り当てで JSON を返す。"""
    try:
        body = json.loads(body_json) if body_json else {}
    except Exception:
        body = {}
    try:
        return json.dumps({"status": 200, "data": handle(method, path, body)},
                          ensure_ascii=False, default=str)
    except session_store.ConflictError as exc:
        return json.dumps({"status": 409, "data": {"error": str(exc)}}, ensure_ascii=False)
    except session_store.MappingNeeded as exc:
        return json.dumps({"status": 422, "data": {"error": str(exc), "mappingNeeded": exc.items}},
                          ensure_ascii=False, default=str)
    except (ValueError, KeyError) as exc:
        return json.dumps({"status": 400, "data": {"error": str(exc).strip("'\"")}},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": 500, "data": {"error": f"処理中にエラーが発生しました: {exc}"}},
                          ensure_ascii=False)
