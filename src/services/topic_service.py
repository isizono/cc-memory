"""議論トピック管理サービス"""
import sqlite3
from typing import Optional
from src.db import execute_query, get_connection, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding
from src.services.tag_service import (
    validate_and_parse_tags,
    ensure_tag_ids,
    link_tags,
    get_entity_tags,
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
    subject_id: int,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """
    サブジェクト内のトピックを新しい順に取得する（ページネーション付き）。

    各トピックにはancestorsフィールドが付与され、直親→祖先の順で
    最大5段までの階層コンテキストを提供する。

    Args:
        subject_id: サブジェクトID
        limit: 取得件数（デフォルト10）
        offset: スキップ件数（デフォルト0）

    Returns:
        トピック一覧（total_count付き）
    """
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
        # total_count取得
        count_rows = execute_query(
            "SELECT COUNT(*) as cnt FROM discussion_topics WHERE subject_id = ?",
            (subject_id,),
        )
        total_count = row_to_dict(count_rows[0])["cnt"]

        # トピック取得（新しい順）
        rows = execute_query(
            """
            SELECT * FROM discussion_topics
            WHERE subject_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (subject_id, limit, offset),
        )

        topics = []
        parent_ids = set()
        for row in rows:
            topic = row_to_dict(row)
            if topic["parent_topic_id"] is not None:
                parent_ids.add(topic["parent_topic_id"])
            topics.append({
                "id": topic["id"],
                "subject_id": topic["subject_id"],
                "title": topic["title"],
                "description": topic["description"],
                "parent_topic_id": topic["parent_topic_id"],
                "created_at": topic["created_at"],
            })

        # ancestors取得（再帰CTE）
        ancestors_map: dict[int, list[dict]] = {}
        if parent_ids:
            placeholders = ",".join("?" * len(parent_ids))
            ancestor_rows = execute_query(
                f"""
                WITH RECURSIVE ancestors AS (
                    SELECT id, title, parent_topic_id, 0 as depth
                    FROM discussion_topics WHERE id IN ({placeholders})
                    UNION ALL
                    SELECT dt.id, dt.title, dt.parent_topic_id, a.depth + 1
                    FROM discussion_topics dt
                    JOIN ancestors a ON dt.id = a.parent_topic_id
                    WHERE a.depth < 4
                )
                SELECT * FROM ancestors ORDER BY depth ASC
                """,
                tuple(parent_ids),
            )

            # CTE結果をlookup辞書に変換（1回の走査で完了）
            lookup: dict[int, dict] = {}
            for arow in ancestor_rows:
                a = row_to_dict(arow)
                if a["id"] not in lookup:
                    lookup[a["id"]] = a

            # 各parent_idからparent_topic_idチェーンを辿って構築
            ancestors_map = {}
            for pid in parent_ids:
                chain = []
                current_id = pid
                visited: set[int] = set()
                while current_id and current_id in lookup:
                    if current_id in visited:
                        break
                    visited.add(current_id)
                    node = lookup[current_id]
                    chain.append({"id": node["id"], "title": node["title"]})
                    current_id = node["parent_topic_id"]
                ancestors_map[pid] = chain

        # topicsにancestorsを付与し、parent_topic_idを除去
        result_topics = []
        for topic in topics:
            pid = topic.pop("parent_topic_id")
            topic["ancestors"] = ancestors_map.get(pid, []) if pid is not None else []
            result_topics.append(topic)

        return {"topics": result_topics, "total_count": total_count}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
