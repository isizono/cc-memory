"""決定事項管理サービス"""
import sqlite3
from typing import Optional
from src.db import get_connection, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding
from src.services.tag_service import (
    validate_and_parse_tags,
    ensure_tag_ids,
    link_tags,
    get_effective_tags,
    get_effective_tags_batch,
)


def add_decision(
    decision: str,
    reason: str,
    topic_id: int,
    tags: Optional[list[str]] = None,
) -> dict:
    """
    決定事項を記録する。

    Args:
        decision: 決定内容
        reason: 決定の理由
        topic_id: 関連するトピックのID（必須）
        tags: 追加タグ（optional）。省略時はtopicのタグを継承

    Returns:
        作成された決定事項情報
    """
    # タグのバリデーション（tagsが指定された場合のみ）
    parsed_tags = None
    if tags is not None:
        parsed_tags = validate_and_parse_tags(tags)
        if isinstance(parsed_tags, dict):
            return parsed_tags

    conn = get_connection()
    try:
        # decisionをINSERT
        cursor = conn.execute(
            "INSERT INTO decisions (topic_id, decision, reason) VALUES (?, ?, ?)",
            (topic_id, decision, reason),
        )
        decision_id = cursor.lastrowid

        # タグをリンク（指定された場合のみ）
        if parsed_tags:
            tag_ids = ensure_tag_ids(conn, parsed_tags)
            link_tags(conn, "decision_tags", "decision_id", decision_id, tag_ids)

        conn.commit()

        # 有効タグを取得（topic_tags UNION decision_tags）
        effective_tags = get_effective_tags(conn, "decision", decision_id)

        # embedding生成（失敗してもdecision作成には影響しない）
        generate_and_store_embedding("decision", decision_id, build_embedding_text(decision, reason))

        return {
            "decision_id": decision_id,
            "topic_id": topic_id,
            "decision": decision,
            "reason": reason,
            "tags": effective_tags,
            "created_at": row_to_dict(
                conn.execute("SELECT created_at FROM decisions WHERE id = ?", (decision_id,)).fetchone()
            )["created_at"],
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


def get_decisions(
    topic_id: int,
    start_id: Optional[int] = None,
    limit: int = 30,
) -> dict:
    """
    指定トピックに関連する決定事項を取得する。

    Args:
        topic_id: 対象トピックのID
        start_id: 取得開始位置の決定事項ID（ページネーション用）
        limit: 取得件数上限（最大30件）

    Returns:
        決定事項一覧（各decisionにtags付き）
    """
    conn = get_connection()
    try:
        # limitを30件に制限
        limit = min(limit, 30)

        # topic_nameを取得
        topic_row = conn.execute(
            "SELECT title FROM discussion_topics WHERE id = ?",
            (topic_id,),
        ).fetchone()
        topic_name = topic_row["title"] if topic_row else None

        if topic_name is None:
            return {
                "topic_id": topic_id,
                "topic_name": None,
                "decisions": [],
            }

        if start_id is None:
            rows = conn.execute(
                """
                SELECT * FROM decisions
                WHERE topic_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM decisions
                WHERE topic_id = ? AND id >= ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, start_id, limit),
            ).fetchall()

        # バッチでタグ取得
        tags_map = get_effective_tags_batch(conn, "decision", topic_id)

        decisions = []
        for row in rows:
            dec = row_to_dict(row)
            decisions.append({
                "id": dec["id"],
                "decision": dec["decision"],
                "reason": dec["reason"],
                "tags": tags_map.get(dec["id"], []),
                "created_at": dec["created_at"],
            })

        return {
            "topic_id": topic_id,
            "topic_name": topic_name,
            "decisions": decisions,
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
