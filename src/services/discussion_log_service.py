"""議論ログ管理サービス"""
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


def add_log(
    topic_id: int,
    title: Optional[str] = None,
    content: str = "",
    tags: Optional[list[str]] = None,
) -> dict:
    """
    トピックに議論ログ（1やりとり）を追加する。

    Args:
        topic_id: 対象トピックのID
        title: ログのタイトル。省略時はcontentの先頭行から自動生成される
        content: 議論内容（マークダウン可）
        tags: 追加タグ（optional）。省略時はtopicのタグを継承

    Returns:
        作成されたログ情報
    """
    if not title or not title.strip():
        # titleが未指定・空の場合、contentから自動生成を試みる
        first_line = content.strip().split('\n', 1)[0].strip()
        title = first_line[:50] if len(first_line) > 50 else first_line
        if not title:
            return {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "title and content cannot both be empty"
                }
            }

    # タグのバリデーション（tagsが指定された場合のみ）
    parsed_tags = None
    if tags is not None:
        parsed_tags = validate_and_parse_tags(tags)
        if isinstance(parsed_tags, dict):
            return parsed_tags

    conn = get_connection()
    try:
        # ログをINSERT
        cursor = conn.execute(
            "INSERT INTO discussion_logs (topic_id, title, content) VALUES (?, ?, ?)",
            (topic_id, title, content),
        )
        log_id = cursor.lastrowid

        # タグをリンク（指定された場合のみ）
        if parsed_tags:
            tag_ids = ensure_tag_ids(conn, parsed_tags)
            link_tags(conn, "log_tags", "log_id", log_id, tag_ids)

        conn.commit()

        # 有効タグを取得（topic_tags UNION log_tags）
        effective_tags = get_effective_tags(conn, "log", log_id)

        # embedding生成（失敗してもlog作成には影響しない）
        generate_and_store_embedding("log", log_id, build_embedding_text(title, content))

        return {
            "log_id": log_id,
            "topic_id": topic_id,
            "title": title,
            "content": content,
            "tags": effective_tags,
            "created_at": row_to_dict(
                conn.execute("SELECT created_at FROM discussion_logs WHERE id = ?", (log_id,)).fetchone()
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


def get_logs(
    topic_id: int,
    start_id: Optional[int] = None,
    limit: int = 30,
) -> dict:
    """
    指定トピックの議論ログを取得する。

    Args:
        topic_id: 対象トピックのID
        start_id: 取得開始位置のログID（ページネーション用）
        limit: 取得件数上限（最大30件）

    Returns:
        議論ログ一覧（各logにtags付き）
    """
    conn = get_connection()
    try:
        # limitを30件に制限
        limit = min(limit, 30)

        if start_id is None:
            rows = conn.execute(
                """
                SELECT * FROM discussion_logs
                WHERE topic_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM discussion_logs
                WHERE topic_id = ? AND id >= ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (topic_id, start_id, limit),
            ).fetchall()

        # バッチでタグ取得
        tags_map = get_effective_tags_batch(conn, "log", topic_id)

        logs = []
        for row in rows:
            log = row_to_dict(row)
            logs.append({
                "id": log["id"],
                "topic_id": log["topic_id"],
                "title": log["title"],
                "content": log["content"],
                "tags": tags_map.get(log["id"], []),
                "created_at": log["created_at"],
            })

        return {"logs": logs}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()
