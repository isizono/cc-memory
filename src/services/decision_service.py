"""決定事項管理サービス"""
import sqlite3
from typing import Optional
from src.db import get_connection, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding
from src.services.tag_service import (
    validate_and_parse_tags,
    ensure_tag_ids,
    link_tags,
    get_effective_tags_batch,
    get_effective_tags_batch_by_ids,
)


def add_decisions(items: list[dict]) -> dict:
    """
    複数の決定事項を一括記録する（最大10件）。

    SAVEPOINT方式で各アイテムを個別に処理し、部分成功を許容する。
    embedding生成はcreated分のみ一括で行う。

    Args:
        items: 決定事項情報のリスト。各要素は以下のキーを持つ:
            - topic_id (int, 必須): 関連するトピックのID
            - decision (str, 必須): 決定内容
            - reason (str, 必須): 決定の理由
            - tags (list[str], optional): 追加タグ。省略時はtopicのタグを継承

    Returns:
        {created: [...], errors: [{index, error}]}
    """
    # バリデーション: 1 <= len(items) <= 10
    if not items:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "items must not be empty",
            }
        }
    if len(items) > 10:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "items must not exceed 10",
            }
        }

    created = []
    errors = []

    conn = get_connection()
    try:
        for i, item in enumerate(items):
            conn.execute(f"SAVEPOINT item_{i}")
            try:
                topic_id = item.get("topic_id")
                decision = item.get("decision", "")
                reason = item.get("reason", "")
                tags = item.get("tags")

                # タグのバリデーション（tagsが指定された場合のみ）
                parsed_tags = None
                if tags is not None:
                    parsed_tags = validate_and_parse_tags(tags)
                    if isinstance(parsed_tags, dict):
                        raise ValueError(parsed_tags["error"]["message"])

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

                conn.execute(f"RELEASE SAVEPOINT item_{i}")
                created.append({
                    "decision_id": decision_id,
                    "topic_id": topic_id,
                    "decision": decision,
                    "reason": reason,
                })

            except Exception as e:
                conn.execute(f"ROLLBACK TO SAVEPOINT item_{i}")
                conn.execute(f"RELEASE SAVEPOINT item_{i}")
                error_code = "CONSTRAINT_VIOLATION" if isinstance(e, sqlite3.IntegrityError) else "ITEM_ERROR"
                errors.append({
                    "index": i,
                    "error": {"code": error_code, "message": str(e)},
                })

        conn.commit()

        # created分の有効タグを一括取得
        if created:
            created_ids = [c["decision_id"] for c in created]
            tags_map = get_effective_tags_batch_by_ids(conn, "decision", created_ids)

            # created_atを一括取得
            placeholders = ",".join("?" * len(created_ids))
            rows = conn.execute(
                f"SELECT id, created_at FROM decisions WHERE id IN ({placeholders})",
                tuple(created_ids),
            ).fetchall()
            created_at_map = {row["id"]: row["created_at"] for row in rows}

            for c in created:
                c["tags"] = tags_map.get(c["decision_id"], [])
                c["created_at"] = created_at_map.get(c["decision_id"])

            # embedding一括生成（created分のみ。失敗してもエラーにしない）
            for c in created:
                tag_text = " ".join(c["tags"]) if c["tags"] else ""
                generate_and_store_embedding(
                    "decision", c["decision_id"],
                    build_embedding_text(c["decision"], c["reason"], tag_text),
                )

        return {"created": created, "errors": errors}

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
