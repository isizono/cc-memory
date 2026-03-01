"""議論トピック管理サービス"""
import sqlite3
from typing import Optional
from src.db import execute_insert, execute_query, row_to_dict
from src.services.embedding_service import build_embedding_text, generate_and_store_embedding


def add_topic(
    subject_id: int,
    title: str,
    description: str,
    parent_topic_id: Optional[int] = None,
) -> dict:
    """
    新しい議論トピックを追加する。

    Args:
        subject_id: サブジェクトID
        title: トピックのタイトル
        description: トピックの説明（必須）
        parent_topic_id: 親トピックのID（未指定なら最上位トピック）

    Returns:
        作成されたトピック情報
    """
    try:
        topic_id = execute_insert(
            "INSERT INTO discussion_topics (subject_id, title, description, parent_topic_id) VALUES (?, ?, ?, ?)",
            (subject_id, title, description, parent_topic_id),
        )

        # embedding生成（失敗してもtopic作成には影響しない）
        generate_and_store_embedding("topic", topic_id, build_embedding_text(title, description))

        # 作成したトピックを取得
        rows = execute_query(
            "SELECT * FROM discussion_topics WHERE id = ?", (topic_id,)
        )
        if rows:
            topic = row_to_dict(rows[0])
            return {
                "topic_id": topic["id"],
                "subject_id": topic["subject_id"],
                "title": topic["title"],
                "description": topic["description"],
                "parent_topic_id": topic["parent_topic_id"],
                "created_at": topic["created_at"],
            }
        else:
            raise Exception("Failed to retrieve created topic")

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
    if limit < 1:
        return {
            "error": {
                "code": "INVALID_PARAMETER",
                "message": "limit must be >= 1",
            }
        }

    try:
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

            # parent_id → ancestorsチェーンを構築
            # depth=0が各起点（直親）、depth>0がその祖先
            chains: dict[int, list[dict]] = {}
            for arow in ancestor_rows:
                a = row_to_dict(arow)
                aid = a["id"]
                if a["depth"] == 0:
                    chains.setdefault(aid, []).insert(0, {"id": aid, "title": a["title"]})
                else:
                    # depth>0の祖先を、起点のチェーンに追加
                    for start_id in list(chains.keys()):
                        chain = chains[start_id]
                        # このancestorの子がチェーンの末尾にいるか確認
                        if chain and chain[-1]["id"] == a.get("_child_id", None):
                            chain.append({"id": aid, "title": a["title"]})

            # 再帰CTEの結果からチェーンを再構築（よりシンプルなアプローチ）
            # 各parent_idを起点として、parent_topic_idを辿ってチェーンを構築
            ancestors_map = {}
            for pid in parent_ids:
                chain = []
                current_id = pid
                visited = set()
                depth = 0
                while current_id is not None and depth < 5 and current_id not in visited:
                    visited.add(current_id)
                    found = None
                    for arow in ancestor_rows:
                        a = row_to_dict(arow)
                        if a["id"] == current_id:
                            found = a
                            break
                    if found:
                        chain.append({"id": found["id"], "title": found["title"]})
                        current_id = found["parent_topic_id"]
                        depth += 1
                    else:
                        break
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
