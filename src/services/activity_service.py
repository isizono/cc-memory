"""アクティビティ管理サービス"""
import logging
import sqlite3
from typing import Optional

from src.db import get_connection, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding
from src.services.tag_service import (
    validate_and_parse_tags,
    ensure_tag_ids,
    resolve_tag_ids,
    link_tags,
    get_entity_tags,
    get_entity_tags_batch,
)

logger = logging.getLogger(__name__)

# get_activitiesでdescriptionを切り詰める上限文字数
ACTIVITY_DESC_MAX_LEN = 200
# DB格納可能なステータス値
REAL_STATUSES = {"pending", "in_progress", "completed"}
# "active"エイリアスが展開されるステータス
ACTIVE_STATUSES = ("in_progress", "pending")
# get_activities用（エイリアス含む）
VALID_STATUSES = REAL_STATUSES | {"active"}


def _activity_to_response(activity: dict, tags: list[str]) -> dict:
    """アクティビティデータをAPIレスポンス形式に変換"""
    return {
        "activity_id": activity["id"],
        "title": activity["title"],
        "description": activity["description"],
        "status": activity["status"],
        "tags": tags,
        "created_at": activity["created_at"],
        "updated_at": activity["updated_at"],
    }


def add_activity(title: str, description: str, tags: list[str]) -> dict:
    """
    アクティビティを作成してIDを返す

    Args:
        title: アクティビティのタイトル
        description: アクティビティの説明
        tags: タグ配列（必須、1個以上）

    Returns:
        作成されたアクティビティ情報
    """
    # タグのバリデーション
    parsed_tags = validate_and_parse_tags(tags, required=True)
    if isinstance(parsed_tags, dict):
        return parsed_tags

    conn = get_connection()
    try:
        # アクティビティをINSERT
        cursor = conn.execute(
            "INSERT INTO activities (title, description, status) VALUES (?, ?, ?)",
            (title, description, 'pending'),
        )
        activity_id = cursor.lastrowid

        # タグをリンク
        tag_ids = ensure_tag_ids(conn, parsed_tags)
        link_tags(conn, "activity_tags", "activity_id", activity_id, tag_ids)

        conn.commit()

        # タグを取得
        tag_strings = get_entity_tags(conn, "activity_tags", "activity_id", activity_id)

        # 作成したアクティビティを取得
        cursor = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,))
        row = cursor.fetchone()
        if not row:
            raise Exception("Failed to retrieve created activity")

        activity = row_to_dict(row)

        # embedding生成（失敗してもactivity作成には影響しない）
        generate_and_store_embedding("activity", activity_id, build_embedding_text(title, description))

        return _activity_to_response(activity, tag_strings)

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


def get_activities(tags: list[str] | None = None, status: str = "active", limit: int = 5) -> dict:
    """
    アクティビティ一覧を取得（tagsでフィルタリング、statusでフィルタリング）

    Args:
        tags: タグ配列（optional。指定時はAND条件でフィルタ、未指定時は全件）
        status: フィルタするステータス（active/pending/in_progress/completed、デフォルト: active）
                "active"はpending+in_progressの両方を返すエイリアス
        limit: 取得件数上限（デフォルト: 5）

    Returns:
        アクティビティ一覧とtotal_count
    """
    # タグのバリデーション（tags指定時のみ）
    parsed_tags = None
    if tags is not None:
        parsed_tags = validate_and_parse_tags(tags, required=True)
        if isinstance(parsed_tags, dict):
            return parsed_tags

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

    conn = get_connection()
    try:
        # タグフィルタでactivity_idsを絞り込む（tags指定時のみ）
        activity_ids = None
        if parsed_tags is not None:
            tag_ids = resolve_tag_ids(conn, parsed_tags)
            if not tag_ids or len(tag_ids) < len(parsed_tags):
                return {"activities": [], "total_count": 0}
            tag_placeholders = ",".join("?" * len(tag_ids))

            activity_ids_rows = conn.execute(
                f"""
                SELECT activity_id FROM activity_tags
                WHERE tag_id IN ({tag_placeholders})
                GROUP BY activity_id
                HAVING COUNT(DISTINCT tag_id) = ?
                """,
                (*tag_ids, len(tag_ids)),
            ).fetchall()

            activity_ids = [row["activity_id"] for row in activity_ids_rows]

            if not activity_ids:
                return {"activities": [], "total_count": 0}

        # WHERE句・ORDER BY句・パラメータを組み立て
        conditions = []
        where_params = []

        if activity_ids is not None:
            id_placeholders = ",".join("?" * len(activity_ids))
            conditions.append(f"id IN ({id_placeholders})")
            where_params.extend(activity_ids)

        if status == "active":
            status_placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
            conditions.append(f"status IN ({status_placeholders})")
            where_params.extend(ACTIVE_STATUSES)
            order_clause = "CASE status WHEN 'in_progress' THEN 0 ELSE 1 END, updated_at DESC"
        else:
            conditions.append("status = ?")
            where_params.append(status)
            order_clause = "updated_at DESC, id DESC"

        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)
        else:
            where_clause = ""

        # 1. total_count取得（LIMITなし）
        count_row = conn.execute(
            f"SELECT COUNT(*) as count FROM activities {where_clause}",
            where_params,
        ).fetchone()
        total_count = count_row["count"]

        # 2. LIMIT付きでデータ取得
        rows = conn.execute(
            f"""
            SELECT * FROM activities
            {where_clause}
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (*where_params, limit),
        ).fetchall()

        # バッチでタグ取得
        fetched_ids = [row["id"] for row in rows]
        tags_map = get_entity_tags_batch(conn, "activity_tags", "activity_id", fetched_ids)

        activities = []
        for row in rows:
            activity = row_to_dict(row)
            activities.append({
                "id": activity["id"],
                "title": activity["title"],
                "description": (activity["description"] or "")[:ACTIVITY_DESC_MAX_LEN],
                "status": activity["status"],
                "tags": tags_map.get(activity["id"], []),
                "created_at": activity["created_at"],
                "updated_at": activity["updated_at"],
            })

        return {"activities": activities, "total_count": total_count}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()


def update_activity(
    activity_id: int,
    new_status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    アクティビティを更新する（ステータス、タイトル、説明、タグを変更可能）

    Args:
        activity_id: アクティビティID
        new_status: 新しいステータス（optional）
        title: 新しいタイトル（optional）
        description: 新しい説明（optional）
        tags: 新しいタグ配列（optional、指定時は全置換。1個以上必須）

    Returns:
        更新されたアクティビティ情報
    """
    # 最低1つのオプショナルパラメータが必要
    if new_status is None and title is None and description is None and tags is None:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "At least one of new_status, title, description, or tags must be provided",
            }
        }

    # タグのバリデーション（tags指定時のみ）
    parsed_tags = None
    if tags is not None:
        parsed_tags = validate_and_parse_tags(tags, required=True)
        if isinstance(parsed_tags, dict):
            return parsed_tags

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
        # 現在のアクティビティ情報を取得
        cursor = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,))
        row = cursor.fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Activity with id {activity_id} not found",
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

        # タグの全置換（tags指定時のみ）
        if parsed_tags is not None:
            conn.execute("DELETE FROM activity_tags WHERE activity_id = ?", (activity_id,))
            tag_ids = ensure_tag_ids(conn, parsed_tags)
            link_tags(conn, "activity_tags", "activity_id", activity_id, tag_ids)

        set_parts.append("updated_at = CURRENT_TIMESTAMP")

        set_clause = ", ".join(set_parts)
        values.append(activity_id)

        conn.execute(
            f"UPDATE activities SET {set_clause} WHERE id = ?",
            tuple(values),
        )

        conn.commit()

        # 更新後のアクティビティを取得
        cursor = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,))
        row = cursor.fetchone()
        if not row:
            raise Exception("Failed to retrieve updated activity")

        # title/descriptionが変更された場合、embeddingを再生成
        if title is not None or description is not None:
            updated = row_to_dict(row)
            generate_and_store_embedding(
                "activity", activity_id,
                build_embedding_text(updated["title"], updated["description"]),
            )

        # タグを取得
        tag_strings = get_entity_tags(conn, "activity_tags", "activity_id", activity_id)

        return _activity_to_response(row_to_dict(row), tag_strings)

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
