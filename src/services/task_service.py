"""タスク管理サービス"""
import logging
import sqlite3
from typing import Optional

from src.db import execute_query, get_connection, row_to_dict
from src.db_base import BaseDBService
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding

logger = logging.getLogger(__name__)

# DB格納可能なステータス値
REAL_STATUSES = {"pending", "in_progress", "completed"}
# "active"エイリアスが展開されるステータス
ACTIVE_STATUSES = ("in_progress", "pending")
# get_tasks用（エイリアス含む）
VALID_STATUSES = REAL_STATUSES | {"active"}


class TaskDBService(BaseDBService):
    """タスクのDB操作を管理するサービス"""

    table_name = "tasks"


# グローバルインスタンス
_task_db = TaskDBService()


def _task_to_response(task: dict) -> dict:
    """タスクデータをAPIレスポンス形式に変換"""
    return {
        "task_id": task["id"],
        "subject_id": task["subject_id"],
        "title": task["title"],
        "description": task["description"],
        "status": task["status"],
        "topic_id": task["topic_id"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }


def add_task(subject_id: int, title: str, description: str, topic_id: Optional[int] = None) -> dict:
    """
    タスクを作成してIDを返す

    Args:
        subject_id: サブジェクトID
        title: タスクのタイトル
        description: タスクの説明
        topic_id: 関連トピックID（optional）

    Returns:
        作成されたタスク情報
    """
    try:
        insert_data = {
            'subject_id': subject_id,
            'title': title,
            'description': description,
            'status': 'pending',
        }
        if topic_id is not None:
            insert_data['topic_id'] = topic_id

        task_id = _task_db._execute_insert(insert_data)

        # embedding生成（失敗してもtask作成には影響しない）
        generate_and_store_embedding("task", task_id, build_embedding_text(title, description))

        # 作成したタスクを取得
        task = _task_db._get_by_id(task_id)
        if not task:
            raise Exception("Failed to retrieve created task")
        return _task_to_response(task)

    except sqlite3.IntegrityError as e:
        return {
            "error": {
                "code": "CONSTRAINT_VIOLATION",
                "message": str(e),
            }
        }
    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }


def get_tasks(subject_id: int, status: str = "active", limit: int = 5) -> dict:
    """
    タスク一覧を取得（statusでフィルタリング）

    Args:
        subject_id: サブジェクトID
        status: フィルタするステータス（active/pending/in_progress/completed、デフォルト: active）
                "active"はpending+in_progressの両方を返すエイリアス
        limit: 取得件数上限（デフォルト: 5）

    Returns:
        タスク一覧とtotal_count
    """
    if limit < 1:
        return {
            "error": {
                "code": "INVALID_PARAMETER",
                "message": f"limit must be positive, got {limit}",
            }
        }

    if status not in VALID_STATUSES:
        return {
            "error": {
                "code": "INVALID_STATUS",
                "message": f"Invalid status: {status}. Must be one of {sorted(VALID_STATUSES)}",
            }
        }

    try:
        # WHERE句・ORDER BY句・パラメータをステータスに応じて組み立て
        if status == "active":
            placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
            where_clause = f"status IN ({placeholders})"
            where_params = (subject_id, *ACTIVE_STATUSES)
            order_clause = "CASE status WHEN 'in_progress' THEN 0 ELSE 1 END, updated_at DESC"
        else:
            where_clause = "status = ?"
            where_params = (subject_id, status)
            order_clause = "created_at ASC, id ASC"

        # 1. total_count取得（LIMITなし）
        count_rows = execute_query(
            f"SELECT COUNT(*) as count FROM tasks WHERE subject_id = ? AND {where_clause}",
            where_params,
        )
        total_count = count_rows[0]["count"]

        # 2. LIMIT付きでデータ取得
        rows = execute_query(
            f"""
            SELECT * FROM tasks
            WHERE subject_id = ? AND {where_clause}
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (*where_params, limit),
        )

        tasks = []
        for row in rows:
            task = row_to_dict(row)
            tasks.append({
                "id": task["id"],
                "subject_id": task["subject_id"],
                "title": task["title"],
                "description": (task["description"] or "")[:100],
                "status": task["status"],
                "topic_id": task["topic_id"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
            })

        return {"tasks": tasks, "total_count": total_count}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }


def update_task(
    task_id: int,
    new_status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    topic_id: Optional[int] = None,
) -> dict:
    """
    タスクを更新する（ステータス、タイトル、説明、関連トピックを変更可能）

    Args:
        task_id: タスクID
        new_status: 新しいステータス（optional）
        title: 新しいタイトル（optional）
        description: 新しい説明（optional）
        topic_id: 関連トピックID（optional）

    Returns:
        更新されたタスク情報
    """
    # 最低1つのオプショナルパラメータが必要
    if new_status is None and title is None and description is None and topic_id is None:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "At least one of new_status, title, description, or topic_id must be provided",
            }
        }

    # ステータスバリデーション
    if new_status is not None and new_status not in REAL_STATUSES:
        return {
            "error": {
                "code": "INVALID_STATUS",
                "message": f"Invalid status: {new_status}. Must be one of {sorted(REAL_STATUSES)}",
            }
        }

    # 空文字バリデーション
    if title is not None and title.strip() == "":
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "title must not be empty",
            }
        }

    if description is not None and description.strip() == "":
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "description must not be empty",
            }
        }

    conn = get_connection()
    try:
        # 現在のタスク情報を取得
        cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Task with id {task_id} not found",
                }
            }

        # 動的SQL構築: 指定されたフィールドのみUPDATEする
        set_parts = []
        values = []

        if new_status is not None:
            set_parts.append("status = ?")
            values.append(new_status)

        if title is not None:
            set_parts.append("title = ?")
            values.append(title)

        if description is not None:
            set_parts.append("description = ?")
            values.append(description)

        if topic_id is not None:
            set_parts.append("topic_id = ?")
            values.append(topic_id)

        set_parts.append("updated_at = CURRENT_TIMESTAMP")

        set_clause = ", ".join(set_parts)
        values.append(task_id)

        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            tuple(values),
        )

        conn.commit()

        # 更新後のタスクを取得
        cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            raise Exception("Failed to retrieve updated task")

        # title/descriptionが変更された場合、embeddingを再生成
        if title is not None or description is not None:
            updated = row_to_dict(row)
            generate_and_store_embedding(
                "task", task_id,
                build_embedding_text(updated["title"], updated["description"]),
            )

        return _task_to_response(row_to_dict(row))

    except sqlite3.IntegrityError as e:
        conn.rollback()
        return {
            "error": {
                "code": "CONSTRAINT_VIOLATION",
                "message": str(e),
            }
        }
    except Exception as e:
        conn.rollback()
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()
