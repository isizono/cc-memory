"""決定事項管理サービス"""
import sqlite3
from typing import Optional
from src.db import execute_insert, execute_query, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding


def add_decision(
    decision: str,
    reason: str,
    topic_id: int,
) -> dict:
    """
    決定事項を記録する。

    Args:
        decision: 決定内容
        reason: 決定の理由
        topic_id: 関連するトピックのID（必須）

    Returns:
        作成された決定事項情報
    """
    try:
        decision_id = execute_insert(
            "INSERT INTO decisions (topic_id, decision, reason) VALUES (?, ?, ?)",
            (topic_id, decision, reason),
        )

        # embedding生成（失敗してもdecision作成には影響しない）
        generate_and_store_embedding("decision", decision_id, build_embedding_text(decision, reason))

        # 作成した決定事項を取得
        rows = execute_query(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        )
        if rows:
            dec = row_to_dict(rows[0])
            return {
                "decision_id": dec["id"],
                "topic_id": dec["topic_id"],
                "decision": dec["decision"],
                "reason": dec["reason"],
                "created_at": dec["created_at"],
            }
        else:
            raise Exception("Failed to retrieve created decision")

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
        決定事項一覧
    """
    try:
        # limitを30件に制限
        limit = min(limit, 30)

        if start_id is None:
            rows = execute_query(
                """
                SELECT * FROM decisions
                WHERE topic_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, limit),
            )
        else:
            rows = execute_query(
                """
                SELECT * FROM decisions
                WHERE topic_id = ? AND id >= ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, start_id, limit),
            )

        decisions = []
        for row in rows:
            dec = row_to_dict(row)
            decisions.append({
                "id": dec["id"],
                "topic_id": dec["topic_id"],
                "decision": dec["decision"],
                "reason": dec["reason"],
                "created_at": dec["created_at"],
            })

        return {"decisions": decisions}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
