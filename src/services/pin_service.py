"""エンティティのpin/unpin管理サービス"""
import logging
import sqlite3

from src.db import get_connection

logger = logging.getLogger(__name__)

ENTITY_TABLE_MAP = {
    "decision": "decisions",
    "log": "discussion_logs",
    "material": "materials",
}


def update_pin(entity_type: str, entity_id: int, pinned: bool) -> dict:
    """エンティティのpinを切り替える。

    Args:
        entity_type: エンティティ種別 ("decision" | "log" | "material")
        entity_id: エンティティのID
        pinned: True=pin, False=unpin

    Returns:
        更新結果 {"entity_type": str, "entity_id": int, "pinned": bool}
        またはエラー {"error": {"code": str, "message": str}}
    """
    # entity_type検証
    if entity_type not in ENTITY_TABLE_MAP:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": f"Invalid entity_type: {entity_type}. Must be one of: {', '.join(sorted(ENTITY_TABLE_MAP.keys()))}",
            }
        }

    table = ENTITY_TABLE_MAP[entity_type]

    conn = get_connection()
    try:
        # 存在確認
        row = conn.execute(
            f"SELECT id FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"{entity_type} with id {entity_id} not found",
                }
            }

        # UPDATE実行
        conn.execute(
            f"UPDATE {table} SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, entity_id),
        )
        conn.commit()

        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "pinned": pinned,
        }

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
