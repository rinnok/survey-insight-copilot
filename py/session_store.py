"""分析セッション（会話・問い・分析結果・UI状態）の永続化。

セッションは同一IDで会話・確定した問い・決定的分析の出力・画面状態をまとめて保存する。
LLMは`research_dialogue.build_session_reply`を通じてのみ関与し、数値計算は行わない。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import analysis_engine
import atami_dr3
import branching_survey
import declared_survey
import project_store
import research_dialogue

MappingNeeded = declared_survey.MappingNeeded

_now = project_store._now
_id = project_store._id


class ConflictError(Exception):
    """既存の分析結果を暗黙に破棄する操作を拒否する。"""


MIGRATION_LEGACY_CONVERSATIONS = "legacy-conversations-v1"
DEFAULT_SESSION_TITLE = "新しい分析"
TITLE_LIMIT = 32


def _derive_title(text: str) -> str:
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return ""
    return text[:TITLE_LIMIT] + ("…" if len(text) > TITLE_LIMIT else "")


class SessionStore:
    def __init__(self, projects: project_store.ProjectStore, db_path: Path | None = None) -> None:
        self.projects = projects
        self.db_path = db_path or projects.db_path
        self._init_schema()
        self._migrate_legacy()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS analysis_sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'consulting',
                    dataset_material_id TEXT NOT NULL DEFAULT '',
                    brief_json TEXT NOT NULL DEFAULT 'null',
                    analysis_json TEXT NOT NULL DEFAULT 'null',
                    ui_state_json TEXT NOT NULL DEFAULT '{}',
                    model_meta_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    structured_json TEXT NOT NULL DEFAULT 'null',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS _migrations (
                    id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS survey_plans (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    source_session_id TEXT NOT NULL DEFAULT '',
                    contract_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            session_columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_sessions)").fetchall()}
            if "survey_plan_id" not in session_columns:
                conn.execute("ALTER TABLE analysis_sessions ADD COLUMN survey_plan_id TEXT NOT NULL DEFAULT ''")
            if "column_mapping_json" not in session_columns:
                conn.execute("ALTER TABLE analysis_sessions ADD COLUMN column_mapping_json TEXT NOT NULL DEFAULT '{}'")

    # --- migration -----------------------------------------------------

    def _migrate_legacy(self) -> None:
        with closing(self._connect()) as conn, conn:
            done = conn.execute(
                "SELECT 1 FROM _migrations WHERE id=?", (MIGRATION_LEGACY_CONVERSATIONS,)
            ).fetchone()
        if done:
            return
        default_project = self.projects.ensure_default_project()
        rows = self.projects.list_raw_conversations()
        now = _now()
        with closing(self._connect()) as conn, conn:
            for row in rows:
                messages = json.loads(row["messages_json"] or "[]")
                brief = json.loads(row["brief_json"] or "null")
                status = "question_confirmed" if brief else "consulting"
                project_id = row["project_id"] or default_project["id"]
                try:
                    self.projects.get_project(project_id)
                except KeyError:
                    project_id = default_project["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO analysis_sessions "
                    "(id,project_id,title,status,dataset_material_id,brief_json,analysis_json,"
                    "ui_state_json,model_meta_json,archived_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"], project_id, row["title"], status, row["material_id"] or "",
                        json.dumps(brief, ensure_ascii=False) if brief else "null",
                        "null", "{}", "{}", "", row["created_at"], row["updated_at"],
                    ),
                )
                for message in messages:
                    role = "user" if message.get("role") == "user" else "assistant"
                    conn.execute(
                        "INSERT INTO session_messages (id,session_id,role,content,structured_json,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (_id("msg"), row["id"], role, str(message.get("text", "")), "null", row["created_at"]),
                    )
            conn.execute("INSERT INTO _migrations (id, applied_at) VALUES (?, ?)", (MIGRATION_LEGACY_CONVERSATIONS, now))

    # --- reads -----------------------------------------------------------

    def _row(self, session_id: str) -> sqlite3.Row:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM analysis_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError("セッションが見つかりません")
        return row

    def _messages(self, session_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM session_messages WHERE session_id=? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [
            {
                "id": r["id"], "role": r["role"], "content": r["content"],
                "structured": json.loads(r["structured_json"] or "null"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self._row(session_id)
        analysis = json.loads(row["analysis_json"] or "null")
        if (
            isinstance(analysis, dict)
            and analysis.get("engine") == "atami_dr3"
            and row["dataset_material_id"]
        ):
            try:
                snapshot = branching_survey.load_snapshot("atami-form-snapshot.v1.json")
                existing = analysis.get("step1", {}).get("denominatorContract") or {}
                existing_revision = existing.get("snapshot", {}).get("revision")
                if existing_revision != snapshot["revision"]:
                    material = self.projects.get_material(row["project_id"], row["dataset_material_id"])
                    file_path = Path(material.get("file_path", ""))
                    if not file_path.exists():
                        raise OSError("分析元データが見つかりません")
                    frame = analysis_engine.read_table(material["filename"], file_path.read_bytes())
                    analysis["step1"]["denominatorContract"] = (
                        branching_survey.build_denominator_contract(frame, snapshot)
                    )
                    with closing(self._connect()) as conn, conn:
                        conn.execute(
                            "UPDATE analysis_sessions SET analysis_json=? WHERE id=?",
                            (json.dumps(analysis, ensure_ascii=False), session_id),
                        )
            except (ValueError, KeyError, OSError):
                # A legacy result remains readable even when its source file is unavailable.
                pass
        dataset = None
        if row["dataset_material_id"]:
            filename = ""
            try:
                material = self.projects.get_material(row["project_id"], row["dataset_material_id"])
                filename = material.get("filename", "")
            except KeyError:
                filename = ""
            dataset = {"materialId": row["dataset_material_id"], "filename": filename}
        return {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "status": row["status"],
            "brief": json.loads(row["brief_json"] or "null"),
            "messages": self._messages(session_id),
            "analysis": analysis,
            "uiState": json.loads(row["ui_state_json"] or "{}"),
            "dataset": dataset,
            "surveyPlanId": row["survey_plan_id"] or None,
            "modelMeta": json.loads(row["model_meta_json"] or "{}"),
            "archivedAt": row["archived_at"] or None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_recent(self, project_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT id,title,status,ui_state_json,updated_at FROM analysis_sessions "
                "WHERE project_id=? AND archived_at='' ORDER BY updated_at DESC LIMIT ?",
                (project_id, max(1, min(int(limit), 50))),
            ).fetchall()
        result = []
        for r in rows:
            ui_state = json.loads(r["ui_state_json"] or "{}")
            result.append({
                "id": r["id"], "title": r["title"], "status": r["status"],
                "activeStep": ui_state.get("activeStep", 1), "updatedAt": r["updated_at"],
            })
        return result

    # --- writes ------------------------------------------------------------

    def _append_message(self, session_id: str, role: str, content: str, structured: dict[str, Any] | None = None) -> str:
        msg_id, now = _id("msg"), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO session_messages (id,session_id,role,content,structured_json,created_at) VALUES (?,?,?,?,?,?)",
                (msg_id, session_id, role, content, json.dumps(structured, ensure_ascii=False) if structured is not None else "null", now),
            )
            conn.execute("UPDATE analysis_sessions SET updated_at=? WHERE id=?", (now, session_id))
        return msg_id

    def create_session(self, project_id: str, title: str = "", dataset_material_id: str = "",
                       survey_plan_id: str = "") -> dict[str, Any]:
        project = self.projects.get_project(project_id)
        material_id = str(dataset_material_id or "").strip()
        survey_plan_id = str(survey_plan_id or "").strip()
        brief_json, status = "null", "consulting"
        if survey_plan_id:
            plan = self.get_survey_plan(survey_plan_id)
            if plan["projectId"] != project_id:
                raise KeyError("指定した調査計画はこのプロジェクトに属していません")
            # 回収分析セッションは対話を経ないため、契約から問いを確定済み扱いにする
            brief_json = json.dumps({
                "route": "declared_survey",
                "objective": plan["contract"]["title"],
                "target": "発行した調査の回答者",
                "decision": plan["contract"].get("purpose", ""),
            }, ensure_ascii=False)
            status = "question_confirmed"
        else:
            material_id = material_id or str(project.get("primary_dataset_material_id") or "").strip()
        if material_id:
            material = self.projects.get_material(project_id, material_id)
            if material["kind"] != "アンケートデータ":
                raise ValueError("指定したデータはアンケートデータではありません")
        session_id, now = _id("ses"), _now()
        default_title = f"回収データ分析：{plan['contract']['title']}"[:TITLE_LIMIT] if survey_plan_id else DEFAULT_SESSION_TITLE
        title = str(title or "").strip() or default_title
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO analysis_sessions "
                "(id,project_id,title,status,dataset_material_id,brief_json,analysis_json,"
                "ui_state_json,model_meta_json,archived_at,created_at,updated_at,survey_plan_id,column_mapping_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, project_id, title, status, material_id, brief_json, "null", "{}", "{}", "", now, now, survey_plan_id, "{}"),
            )
        return self.get_session(session_id)

    # --- survey plans（design-survey-loop-v1.md §5・§8） --------------------

    def create_survey_plan(self, session_id: str) -> dict[str, Any]:
        row = self._row(session_id)
        analysis = json.loads(row["analysis_json"] or "null")
        if not analysis or row["status"] not in ("analyzed", "needs_validation"):
            raise ValueError("分析済みのセッションからのみ調査を発行できます")
        if not (analysis.get("next") or {}).get("survey"):
            raise ValueError("このセッションの分析には次の調査案がありません。施策仮説ルートで分析してください")
        plan_id, now = _id("svy"), _now()
        contract = atami_dr3.survey_contract()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO survey_plans (id,project_id,source_session_id,contract_json,created_at) VALUES (?,?,?,?,?)",
                (plan_id, row["project_id"], session_id, json.dumps(contract, ensure_ascii=False), now),
            )
            conn.execute(
                "UPDATE analysis_sessions SET status='needs_validation', updated_at=? WHERE id=?",
                (now, session_id),
            )
        self._append_message(session_id, "system_event", "比較調査を発行しました", {"event": "survey_published", "surveyPlanId": plan_id})
        return self.get_survey_plan(plan_id)

    def get_survey_plan(self, plan_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM survey_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise KeyError("調査計画が見つかりません")
        return {
            "id": row["id"], "projectId": row["project_id"],
            "sourceSessionId": row["source_session_id"],
            "contract": json.loads(row["contract_json"]),
            "createdAt": row["created_at"],
        }

    def save_column_mapping(self, session_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        row = self._row(session_id)
        if not row["survey_plan_id"]:
            raise ValueError("このセッションは回収データ分析ではありません")
        current = json.loads(row["column_mapping_json"] or "{}")
        current.update({k: v for k, v in (mapping or {}).items() if v})
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET column_mapping_json=?, updated_at=? WHERE id=?",
                (json.dumps(current, ensure_ascii=False), now, session_id),
            )
        return self.get_session(session_id)

    def set_dataset(self, session_id: str, material_id: str) -> dict[str, Any]:
        row = self._row(session_id)
        if row["status"] == "analyzed":
            raise ConflictError("分析済みのセッションはデータを変更できません。新しいセッションを作成してください")
        material_id = str(material_id or "").strip()
        if not material_id:
            raise ValueError("使用するデータを指定してください")
        material = self.projects.get_material(row["project_id"], material_id)
        if material["kind"] != "アンケートデータ":
            raise ValueError("指定したデータはアンケートデータではありません")
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET dataset_material_id=?, updated_at=? WHERE id=?",
                (material_id, now, session_id),
            )
        return self.get_session(session_id)

    def post_message(self, session_id: str, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self._row(session_id)
        text = str(message or "").strip()
        if not text:
            raise ValueError("メッセージを入力してください")
        project = self.projects.get_project(row["project_id"])
        history = self._messages(session_id)
        reply = research_dialogue.build_session_reply(project, text, history)
        user_message_id = self._append_message(session_id, "user", text)
        assistant_message_id = self._append_message(session_id, "assistant", reply["reply"], {
            "briefCandidate": reply["briefCandidate"],
            "missingInformation": reply["missingInformation"],
            "readyToConfirm": reply["readyToConfirm"],
            "model": reply["model"],
        })
        now = _now()
        title_update = ""
        if not history and row["title"] == DEFAULT_SESSION_TITLE:
            title_update = _derive_title(text)
        with closing(self._connect()) as conn, conn:
            if title_update:
                conn.execute(
                    "UPDATE analysis_sessions SET title=?, brief_json=?, model_meta_json=?, updated_at=? WHERE id=?",
                    (title_update, json.dumps(reply["briefCandidate"], ensure_ascii=False), json.dumps(reply["model"], ensure_ascii=False), now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE analysis_sessions SET brief_json=?, model_meta_json=?, updated_at=? WHERE id=?",
                    (json.dumps(reply["briefCandidate"], ensure_ascii=False), json.dumps(reply["model"], ensure_ascii=False), now, session_id),
                )
        exchange = {
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
            "briefCandidate": reply["briefCandidate"],
            "missingInformation": reply["missingInformation"],
            "readyToConfirm": reply["readyToConfirm"],
            "model": reply["model"],
        }
        return self.get_session(session_id), exchange

    def analyze_from_message(self, session_id: str, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """確定済みの相談内容を使い、実行指示から分析結果まで一度に進める。"""
        row = self._row(session_id)
        text = str(message or "").strip()
        if not text:
            raise ValueError("メッセージを入力してください")
        if row["status"] not in ("consulting", "question_confirmed", "needs_validation"):
            raise ConflictError("このセッションはすでに分析済みです")

        current = json.loads(row["brief_json"] or "null") or {}
        cleaned = research_dialogue.validate_brief(current)
        self._append_message(session_id, "user", text, {"workflowAction": "run_analysis"})

        if row["status"] == "consulting":
            self.confirm_question(session_id, cleaned)

        refreshed = self._row(session_id)
        if not refreshed["dataset_material_id"]:
            reply = "分析に使うアンケートデータを選択してください。選択後、この設問構成のまま分析できます。"
            assistant_id = self._append_message(
                session_id, "assistant", reply,
                {"workflowAction": "select_dataset", "model": {
                    "provider": "workflow", "model": "local-router", "usedFallback": False,
                }},
            )
            return self.get_session(session_id), {
                "assistantMessageId": assistant_id,
                "autoAction": "select_dataset",
                "model": {"provider": "workflow", "model": "local-router", "usedFallback": False},
            }

        analyzed = self.analyze(session_id)
        reply = "分析が完了しました。選択した設問と比較結果を表示します。"
        assistant_id = self._append_message(
            session_id, "assistant", reply,
            {"workflowAction": "analysis_completed", "model": {
                "provider": "workflow", "model": "local-analysis-engine", "usedFallback": False,
            }},
        )
        return self.get_session(session_id), {
            "assistantMessageId": assistant_id,
            "autoAction": "analysis_completed",
            "model": {"provider": "workflow", "model": "local-analysis-engine", "usedFallback": False},
            "analysisRoute": (analyzed.get("analysis") or {}).get("route", ""),
        }

    def confirm_question(self, session_id: str, brief_patch: dict[str, Any] | None) -> dict[str, Any]:
        row = self._row(session_id)
        current = json.loads(row["brief_json"] or "null") or {}
        merged = {**current, **{k: v for k, v in (brief_patch or {}).items() if v not in (None, "")}}
        cleaned = research_dialogue.validate_brief({
            "route": merged.get("route", ""),
            "objective": merged.get("objective", ""),
            "target": merged.get("target", ""),
            "decision": merged.get("decision", ""),
            "analysisHint": merged.get("analysisHint", ""),
        })
        if merged.get("analysisHint"):
            cleaned["analysisHint"] = str(merged["analysisHint"]).strip()
        now = _now()
        title_update = _derive_title(cleaned["objective"])
        with closing(self._connect()) as conn, conn:
            if title_update:
                conn.execute(
                    "UPDATE analysis_sessions SET title=?, brief_json=?, status='question_confirmed', updated_at=? WHERE id=?",
                    (title_update, json.dumps(cleaned, ensure_ascii=False), now, session_id),
                )
            else:
                conn.execute(
                    "UPDATE analysis_sessions SET brief_json=?, status='question_confirmed', updated_at=? WHERE id=?",
                    (json.dumps(cleaned, ensure_ascii=False), now, session_id),
                )
        self._append_message(session_id, "system_event", "問いを確定しました", {"event": "question_confirmed", "brief": cleaned})
        return self.get_session(session_id)

    def analyze(self, session_id: str) -> dict[str, Any]:
        row = self._row(session_id)
        if row["status"] not in ("question_confirmed", "needs_validation"):
            raise ValueError("問いを確定してから分析してください")
        material_id = row["dataset_material_id"]
        if not material_id:
            raise ValueError("分析に使うアンケートデータを選択してください")
        material = self.projects.get_material(row["project_id"], material_id)
        file_path = Path(material.get("file_path", "")) if material.get("file_path") else None
        if not file_path or not file_path.exists():
            raise ValueError("元データが見つかりません。データを選び直してください")
        raw = file_path.read_bytes()
        df = analysis_engine.read_table(material["filename"], raw)
        if row["survey_plan_id"]:
            return self._analyze_declared(row, material, raw, df)
        brief = json.loads(row["brief_json"] or "null") or {}
        route = brief.get("route", "")
        if route not in ("atami_conversion", "atami_policy_test"):
            raise ValueError("問いに対応する分析ルートが確定していません")
        result = atami_dr3.analyze(df, material["filename"], route)
        result["quality"]["verdict"] = "canonical" if result["quality"]["canonical"] else "invalid"
        analysis_json = {
            "schemaVersion": 1,
            "engine": "atami_dr3",
            "route": route,
            "generatedAt": _now(),
            "dataset": {
                "materialId": material_id,
                "filename": material["filename"],
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "quality": result["quality"],
            "step1": {
                "counts": result["counts"],
                "funnel": result["funnel"],
                "evidence": result["evidence"],
                "denominatorContract": result.get("denominatorContract"),
            },
            "step2": {
                "segmentOverall": result["segmentOverall"],
                "claims": result.get("claims", {"canSay": [], "cannotSay": []}),
            },
            "step3": {"comparison": result["comparison"]},
            "next": {"plans": result.get("plans", []), "survey": result.get("survey")},
        }
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET analysis_json=?, status='analyzed', updated_at=? WHERE id=?",
                (json.dumps(analysis_json, ensure_ascii=False), now, session_id),
            )
        self._append_message(session_id, "system_event", "分析を実行しました", {"event": "analyzed"})
        return self.get_session(session_id)

    def _analyze_declared(self, row: sqlite3.Row, material: dict[str, Any],
                          raw: bytes, df) -> dict[str, Any]:
        """回収データを契約と照合し、valid_new / invalid を判定して集計する。"""
        plan = self.get_survey_plan(row["survey_plan_id"])
        contract = plan["contract"]
        manual = json.loads(row["column_mapping_json"] or "{}")
        mapping = declared_survey.match_columns(contract, [str(c) for c in df.columns], manual)
        checks = declared_survey.structural_check(contract, df, mapping)
        failed = [c for c in checks if not c["ok"]]
        if failed:
            details = "／".join(f"{c['label']}（{c['detail']}）" if c["detail"] else c["label"] for c in failed)
            raise ValueError(f"回収データが契約と一致しません: {details}")
        output = declared_survey.analyze(contract, df, mapping)
        analysis_json = {
            "schemaVersion": 2,
            "engine": "declared_survey_v1",
            "surveyPlanId": plan["id"],
            "generatedAt": _now(),
            "dataset": {
                "materialId": row["dataset_material_id"],
                "filename": material["filename"],
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "quality": {
                "verdict": "valid_new", "canonical": False, "checks": checks,
                "message": "基準外データです。構造チェックに合格したため、件数と分母のみの集計を表示します。",
            },
            "results": output["results"],
            "claims": output["claims"],
            "freeTextCount": output["freeTextCount"],
        }
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET analysis_json=?, status='analyzed', updated_at=? WHERE id=?",
                (json.dumps(analysis_json, ensure_ascii=False), now, row["id"]),
            )
        self._append_message(row["id"], "system_event", "回収データを分析しました", {"event": "analyzed"})
        return self.get_session(row["id"])

    def set_ui_state(self, session_id: str, patch: dict[str, Any] | None) -> dict[str, Any]:
        row = self._row(session_id)
        current = json.loads(row["ui_state_json"] or "{}")
        current.update(patch or {})
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET ui_state_json=?, updated_at=? WHERE id=?",
                (json.dumps(current, ensure_ascii=False), now, session_id),
            )
        return self.get_session(session_id)

    def archive(self, session_id: str) -> dict[str, Any]:
        self._row(session_id)
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE analysis_sessions SET archived_at=?, updated_at=? WHERE id=?", (now, now, session_id)
            )
        return self.get_session(session_id)
