"""資材管理サービス"""
import logging
import sqlite3

from src.db import get_connection, row_to_dict

logger = logging.getLogger(__name__)


def _material_to_response(material: dict) -> dict:
    """資材データをAPIレスポンス形式に変換（全文含む）"""
    return {
        "material_id": material["id"],
        "activity_id": material["activity_id"],
        "title": material["title"],
        "content": material["content"],
        "created_at": material["created_at"],
    }


def _material_to_catalog(material: dict) -> dict:
    """資材データをカタログ形式に変換（全文なし）"""
    return {
        "material_id": material["id"],
        "activity_id": material["activity_id"],
        "title": material["title"],
        "created_at": material["created_at"],
    }


def add_material(activity_id: int, title: str, content: str) -> dict:
    """
    資材を追加する

    Args:
        activity_id: 紐づくアクティビティのID
        title: 資材のタイトル
        content: 資材の本文

    Returns:
        作成された資材情報
    """
    if not title or not title.strip():
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "title must not be empty",
            }
        }

    if not content or not content.strip():
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "content must not be empty",
            }
        }

    conn = get_connection()
    try:
        # FK検証: activity_idの存在チェック
        row = conn.execute(
            "SELECT id FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Activity with id {activity_id} not found",
                }
            }

        cursor = conn.execute(
            "INSERT INTO materials (activity_id, title, content) VALUES (?, ?, ?)",
            (activity_id, title, content),
        )
        material_id = cursor.lastrowid

        row = conn.execute(
            "SELECT * FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return {
                "error": {
                    "code": "DATABASE_ERROR",
                    "message": "Failed to retrieve created material",
                }
            }

        conn.commit()
        return _material_to_response(row_to_dict(row))

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


def get_materials_by_activity_with_conn(conn, activity_id: int) -> list[dict]:
    """
    アクティビティに紐づく資材一覧をカタログ形式で取得する（conn共有版）

    Args:
        conn: SQLiteコネクション
        activity_id: アクティビティのID

    Returns:
        資材カタログのリスト [{"id": int, "title": str, "created_at": str}, ...]
    """
    rows = conn.execute(
        "SELECT id, title, created_at FROM materials WHERE activity_id = ? ORDER BY created_at ASC",
        (activity_id,),
    ).fetchall()
    return [{"id": row["id"], "title": row["title"], "created_at": row["created_at"]} for row in rows]


def get_material(material_id: int) -> dict:
    """
    資材を全文取得する

    Args:
        material_id: 資材のID

    Returns:
        資材の全文情報
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Material with id {material_id} not found",
                }
            }

        return _material_to_response(row_to_dict(row))

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()


def list_materials(activity_id: int) -> dict:
    """
    アクティビティに紐づく資材のカタログ一覧を取得する

    Args:
        activity_id: アクティビティのID

    Returns:
        資材カタログ一覧（全文なし）
    """
    conn = get_connection()
    try:
        # activity_idの存在チェック
        row = conn.execute(
            "SELECT id FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Activity with id {activity_id} not found",
                }
            }

        rows = conn.execute(
            "SELECT * FROM materials WHERE activity_id = ? ORDER BY created_at",
            (activity_id,),
        ).fetchall()

        return {
            "activity_id": activity_id,
            "materials": [_material_to_catalog(row_to_dict(r)) for r in rows],
            "total_count": len(rows),
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
