"""リマインダー管理サービス"""
import logging

from src.db import get_connection, row_to_dict

logger = logging.getLogger(__name__)


def get_active_reminder_contents_with_conn(conn) -> list[str]:
    """有効なリマインダーのcontent一覧を取得する（conn共有版）。

    Returns:
        [content, ...]
    """
    rows = conn.execute(
        "SELECT content FROM reminders WHERE active = 1"
    ).fetchall()
    return [r["content"] for r in rows]


def add_reminder(content: str) -> dict:
    """リマインダーを追加する。

    Args:
        content: リマインダーの内容（空文字不可）

    Returns:
        作成されたリマインダー情報
    """
    if not content or not content.strip():
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "content must not be empty",
            }
        }

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO reminders (content) VALUES (?)",
            (content,),
        )
        reminder_id = cursor.lastrowid
        conn.commit()

        return {"reminder_id": reminder_id}

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


def list_reminders() -> dict:
    """リマインダー一覧を取得する。

    Returns:
        リマインダー一覧とtotal_count
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reminders ORDER BY id"
        ).fetchall()

        reminders = []
        for row in rows:
            reminder = row_to_dict(row)
            reminders.append({
                "reminder_id": reminder["id"],
                "content": reminder["content"],
                "active": reminder["active"],
                "created_at": reminder["created_at"],
            })

        return {
            "reminders": reminders,
            "total_count": len(reminders),
        }

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()


def update_reminder(reminder_id: int, content: str | None = None, active: bool | None = None) -> dict:
    """リマインダーを更新する。

    Args:
        reminder_id: リマインダーID
        content: 新しい内容（optional）
        active: 有効/無効フラグ（True/False、optional）

    Returns:
        更新されたリマインダー情報
    """
    if content is None and active is None:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "At least one of content or active must be provided",
            }
        }

    if content is not None and not content.strip():
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "content must not be empty",
            }
        }

    if active is not None and not isinstance(active, bool):
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "active must be True or False",
            }
        }

    conn = get_connection()
    try:
        # 存在チェック
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Reminder with id {reminder_id} not found",
                }
            }

        # 動的SQL構築
        set_parts = []
        values = []

        if content is not None:
            set_parts.append("content = ?")
            values.append(content)

        if active is not None:
            set_parts.append("active = ?")
            values.append(active)

        set_clause = ", ".join(set_parts)
        values.append(reminder_id)

        conn.execute(
            f"UPDATE reminders SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        conn.commit()

        # 更新後のリマインダーを取得
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if not row:
            raise Exception("Failed to retrieve updated reminder")

        reminder = row_to_dict(row)
        return {
            "reminder_id": reminder["id"],
            "content": reminder["content"],
            "active": reminder["active"],
            "created_at": reminder["created_at"],
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
