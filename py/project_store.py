"""Survey Insight Copilotのローカル永続化。外部送信は行わない。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = APP_DIR / "runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
DB_PATH = RUNTIME_DIR / "survey-insight.db"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ProjectStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    decision_question TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS organizations (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_plans (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT '',
                    material_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    messages_json TEXT NOT NULL DEFAULT '[]',
                    response_json TEXT NOT NULL DEFAULT 'null',
                    brief_json TEXT NOT NULL DEFAULT 'null',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conversation_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "material_id" not in conversation_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN material_id TEXT NOT NULL DEFAULT ''")
            project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            for column in ("partner", "background", "current_objective", "primary_dataset_material_id"):
                if column not in project_columns:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    def create_conversation(self, message: str, dialogue: dict[str, Any], project_id: str = "", material_id: str = "") -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            raise ValueError("会話内容を入力してください")
        conversation_id, now = _id("chat"), _now()
        title = message[:36] + ("…" if len(message) > 36 else "")
        messages = [
            {"role": "assistant", "text": "いま、どんなことが気になっていますか？ うまく整理できていなくても、そのまま書いてください。"},
            {"role": "user", "text": message},
            {"role": "assistant", "text": str(dialogue.get("reply", ""))},
        ]
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO conversations (id,project_id,material_id,title,messages_json,response_json,brief_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (conversation_id, str(project_id or ""), str(material_id or ""), title, json.dumps(messages, ensure_ascii=False),
                 json.dumps(dialogue, ensure_ascii=False), "null", now, now),
            )
        return self.get_conversation(conversation_id)

    def list_conversations(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT id,project_id,material_id,title,messages_json,brief_json,created_at,updated_at "
                "FROM conversations ORDER BY updated_at DESC LIMIT ?", (max(1, min(int(limit), 100)),)
            ).fetchall()
        return [{
            "id": row["id"], "projectId": row["project_id"], "materialId": row["material_id"], "title": row["title"],
            "messageCount": len(json.loads(row["messages_json"] or "[]")),
            "confirmed": json.loads(row["brief_json"] or "null") is not None,
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        } for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row:
            raise KeyError("会話が見つかりません")
        return {
            "id": row["id"], "projectId": row["project_id"], "materialId": row["material_id"], "title": row["title"],
            "messages": json.loads(row["messages_json"] or "[]"),
            "response": json.loads(row["response_json"] or "null"),
            "brief": json.loads(row["brief_json"] or "null"),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def append_conversation(self, conversation_id: str, message: str, dialogue: dict[str, Any]) -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            raise ValueError("会話内容を入力してください")
        now = _now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT messages_json FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row:
                conn.rollback()
                raise KeyError("会話が見つかりません")
            messages = json.loads(row["messages_json"] or "[]") + [
                {"role": "user", "text": message},
                {"role": "assistant", "text": str(dialogue.get("reply", ""))},
            ]
            conn.execute(
                "UPDATE conversations SET messages_json=?,response_json=?,brief_json='null',updated_at=? WHERE id=?",
                (json.dumps(messages, ensure_ascii=False), json.dumps(dialogue, ensure_ascii=False), now, conversation_id),
            )
            conn.commit()
        return self.get_conversation(conversation_id)

    def save_conversation_brief(self, conversation_id: str, brief: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT response_json FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row:
                conn.rollback()
                raise KeyError("会話が見つかりません")
            response = json.loads(row["response_json"] or "null") or {}
            merged_brief = dict(response.get("brief") or {})
            merged_brief.update(brief)
            response["brief"] = merged_brief
            conn.execute(
                "UPDATE conversations SET brief_json=?,response_json=?,updated_at=? WHERE id=?",
                (json.dumps(merged_brief, ensure_ascii=False), json.dumps(response, ensure_ascii=False), now, conversation_id),
            )
            conn.commit()
        return self.get_conversation(conversation_id)

    def create_project(self, title: str, decision_question: str, target: str = "") -> dict[str, Any]:
        title, decision_question, target = title.strip(), decision_question.strip(), target.strip()
        if not title:
            raise ValueError("プロジェクト名を入力してください")
        if len(decision_question) < 5:
            raise ValueError("このプロジェクトで判断したいことを入力してください")
        project_id, now = _id("pj"), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO projects (id,title,decision_question,target,created_at,updated_at,"
                "partner,background,current_objective,primary_dataset_material_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project_id, title, decision_question, target, now, now, "", "", "", ""),
            )
        return self.get_project(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("""
                SELECT p.*,
                    (SELECT COUNT(*) FROM materials m WHERE m.project_id=p.id) AS material_count,
                    (SELECT COUNT(*) FROM analyses a WHERE a.project_id=p.id) AS analysis_count
                FROM projects p ORDER BY p.updated_at DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise KeyError("プロジェクトが見つかりません")
            materials = conn.execute("SELECT * FROM materials WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
            analyses = conn.execute("SELECT * FROM analyses WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
            organization = conn.execute("SELECT payload_json FROM organizations WHERE project_id=?", (project_id,)).fetchone()
            plans = conn.execute("SELECT * FROM research_plans WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        result = dict(project)
        result["materials"] = [self._material(row) for row in materials]
        result["analyses"] = [{**dict(row), "result": json.loads(row["result_json"])} for row in analyses]
        result["organization"] = json.loads(organization["payload_json"]) if organization else None
        result["researchPlans"] = [{**dict(row), "payload": json.loads(row["payload_json"])} for row in plans]
        return result

    def _material(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json") or "{}")
        return item

    def add_note(self, project_id: str, kind: str, title: str, content: str) -> dict[str, Any]:
        self.get_project(project_id)
        title, content = title.strip(), content.strip()
        if not title or len(content) < 3:
            raise ValueError("資料名と内容を入力してください")
        material_id, now = _id("mat"), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO materials (id,project_id,kind,title,content,created_at) VALUES (?,?,?,?,?,?)",
                (material_id, project_id, kind or "学生の気づき", title, content, now),
            )
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return next(item for item in self.get_project(project_id)["materials"] if item["id"] == material_id)

    def add_dataset(self, project_id: str, filename: str, raw: bytes, summary: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        material_id, now = _id("data"), _now()
        safe_name = Path(filename).name
        path = UPLOAD_DIR / f"{material_id}-{safe_name}"
        path.write_bytes(raw)
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                INSERT INTO materials (id,project_id,kind,title,filename,file_path,summary_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (material_id, project_id, "アンケートデータ", safe_name, safe_name, str(path), json.dumps(summary, ensure_ascii=False), now))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return next(item for item in self.get_project(project_id)["materials"] if item["id"] == material_id)

    def add_questionnaire(self, project_id: str, filename: str, raw: bytes, summary: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        material_id, now = _id("form"), _now()
        safe_name = Path(filename).name
        path = UPLOAD_DIR / f"{material_id}-{safe_name}"
        path.write_bytes(raw)
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                INSERT INTO materials (id,project_id,kind,title,filename,file_path,summary_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                material_id, project_id, "アンケート設問票", safe_name, safe_name, str(path),
                json.dumps(summary, ensure_ascii=False), now,
            ))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return next(item for item in self.get_project(project_id)["materials"] if item["id"] == material_id)

    def save_analysis(self, project_id: str, question: str, result: dict[str, Any]) -> dict[str, Any]:
        analysis_id, now = _id("ana"), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO analyses VALUES (?,?,?,?,?)", (analysis_id, project_id, question, json.dumps(result, ensure_ascii=False), now))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return {"id": analysis_id, "created_at": now}

    def save_organization(self, project_id: str, payload: dict[str, Any]) -> None:
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                INSERT INTO organizations VALUES (?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """, (project_id, json.dumps(payload, ensure_ascii=False), now))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))

    def save_research_plan(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id, now = _id("plan"), _now()
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO research_plans VALUES (?,?,?,?)", (plan_id, project_id, json.dumps(payload, ensure_ascii=False), now))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return {"id": plan_id, "created_at": now}

    def get_material(self, project_id: str, material_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        for material in project["materials"]:
            if material["id"] == material_id:
                return material
        raise KeyError("指定したデータはこのプロジェクトに属していません")

    def update_project(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        fields: dict[str, str] = {}
        if "title" in patch:
            title = str(patch["title"]).strip()
            if not title:
                raise ValueError("プロジェクト名を入力してください")
            fields["title"] = title
        if "partner" in patch:
            fields["partner"] = str(patch["partner"]).strip()
        if "background" in patch:
            background = str(patch["background"]).strip()
            if len(background) > 2000:
                raise ValueError("背景は2000文字以内にしてください")
            fields["background"] = background
        if "currentObjective" in patch:
            fields["current_objective"] = str(patch["currentObjective"]).strip()
        if "primaryDatasetMaterialId" in patch:
            material_id = str(patch["primaryDatasetMaterialId"]).strip()
            if material_id:
                material = self.get_material(project_id, material_id)
                if material["kind"] != "アンケートデータ":
                    raise ValueError("アンケートデータのみ主データに設定できます")
            fields["primary_dataset_material_id"] = material_id
        if not fields:
            raise ValueError("更新する項目がありません")
        now = _now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with closing(self._connect()) as conn, conn:
            conn.execute(f"UPDATE projects SET {assignments}, updated_at=? WHERE id=?", (*fields.values(), now, project_id))
        return self.get_project(project_id)

    DEFAULT_PROJECT_TITLE = "熱海DMO DR3"

    def ensure_default_project(self) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT id FROM projects WHERE title=? ORDER BY created_at LIMIT 1", (self.DEFAULT_PROJECT_TITLE,)
            ).fetchone()
        if row:
            return self.get_project(row["id"])
        return self.create_project(
            self.DEFAULT_PROJECT_TITLE,
            "熱海を検討したが訪問しなかった学生の障壁を明らかにする",
            "熱海を検討した大学生",
        )

    def get_primary_project(self) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT id FROM projects ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row:
            return self.get_project(row["id"])
        return self.ensure_default_project()

    def list_raw_conversations(self) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn, conn:
            return conn.execute("SELECT * FROM conversations ORDER BY created_at").fetchall()
