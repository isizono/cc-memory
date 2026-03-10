"""議論トピック管理サービス"""
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


def add_topic(
    title: str,
    description: str,
    tags: list[str],
) -> dict:
    """
    新しい議論トピックを追加する。

    Args:
        title: トピックのタイトル
        description: トピックの説明（必須）
        tags: タグ配列（必須、1個以上）

    Returns:
        作成されたトピック情報
    """
    # タグのバリデーション
    parsed_tags = validate_and_parse_tags(tags, required=True)
    if isinstance(parsed_tags, dict):
        return parsed_tags

    conn = get_connection()
    try:
        # トピックをINSERT
        cursor = conn.execute(
            "INSERT INTO discussion_topics (title, description) VALUES (?, ?)",
            (title, description),
        )
        topic_id = cursor.lastrowid

        # タグをリンク
        tag_ids = ensure_tag_ids(conn, parsed_tags)
        link_tags(conn, "topic_tags", "topic_id", topic_id, tag_ids)

        conn.commit()

        # タグを取得
        tag_strings = get_entity_tags(conn, "topic_tags", "topic_id", topic_id)

        # 作成したトピックを取得
        cursor = conn.execute(
            "SELECT * FROM discussion_topics WHERE id = ?", (topic_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise Exception("Failed to retrieve created topic")

        topic = row_to_dict(row)

        # embedding生成（失敗してもtopic作成には影響しない）
        generate_and_store_embedding("topic", topic_id, build_embedding_text(title, description))

        return {
            "topic_id": topic["id"],
            "title": topic["title"],
            "description": topic["description"],
            "tags": tag_strings,
            "created_at": topic["created_at"],
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


def get_topics(
    tags: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """
    トピックを新しい順に取得する（ページネーション付き）。

    Args:
        tags: タグ配列（optional。指定時はAND条件でフィルタ、未指定時は全件）
        limit: 取得件数（デフォルト10）
        offset: スキップ件数（デフォルト0）

    Returns:
        トピック一覧（total_count付き）
    """
    # タグのバリデーション（tags指定時のみ）
    parsed_tags = None
    if tags is not None:
        parsed_tags = validate_and_parse_tags(tags, required=True)
        if isinstance(parsed_tags, dict):
            return parsed_tags

    try:
        if limit < 1:
            return {
                "error": {
                    "code": "INVALID_PARAMETER",
                    "message": "limit must be >= 1",
                }
            }
        if offset < 0:
            return {
                "error": {
                    "code": "INVALID_PARAMETER",
                    "message": "offset must be >= 0",
                }
            }

        conn = get_connection()
        try:
            # タグフィルタでtopic_idsを絞り込む（tags指定時のみ）
            topic_ids = None
            if parsed_tags is not None:
                tag_ids = resolve_tag_ids(conn, parsed_tags)
                if not tag_ids or len(tag_ids) < len(parsed_tags):
                    return {"topics": [], "total_count": 0}
                placeholders = ",".join("?" * len(tag_ids))

                topic_ids_rows = conn.execute(
                    f"""
                    SELECT topic_id FROM topic_tags
                    WHERE tag_id IN ({placeholders})
                    GROUP BY topic_id
                    HAVING COUNT(DISTINCT tag_id) = ?
                    """,
                    (*tag_ids, len(tag_ids)),
                ).fetchall()

                topic_ids = [row["topic_id"] for row in topic_ids_rows]

                if not topic_ids:
                    return {"topics": [], "total_count": 0}

            # クエリ組み立て
            if topic_ids is not None:
                id_placeholders = ",".join("?" * len(topic_ids))
                where_clause = f"WHERE id IN ({id_placeholders})"
                where_params = list(topic_ids)
            else:
                where_clause = ""
                where_params = []

            count_row = conn.execute(
                f"SELECT COUNT(*) as count FROM discussion_topics {where_clause}",
                where_params,
            ).fetchone()
            total_count = count_row["count"]

            rows = conn.execute(
                f"""
                SELECT * FROM discussion_topics
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*where_params, limit, offset),
            ).fetchall()

            # バッチでタグ取得
            fetched_ids = [row["id"] for row in rows]
            tags_map = get_entity_tags_batch(conn, "topic_tags", "topic_id", fetched_ids)

            topics = []
            for row in rows:
                topic = row_to_dict(row)
                topics.append({
                    "id": topic["id"],
                    "title": topic["title"],
                    "description": topic["description"],
                    "tags": tags_map.get(topic["id"], []),
                    "created_at": topic["created_at"],
                })

            return {"topics": topics, "total_count": total_count}

        finally:
            conn.close()

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
