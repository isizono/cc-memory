"""check-inサービス"""
import logging
import sqlite3

from src.db import get_connection, row_to_dict
from src.services import activity_service
from src.services.material_service import get_materials_by_relation_with_conn
from src.services.relation_service import _get_map_with_conn
from src.services.tag_service import (
    collect_tag_notes_for_injection,
    get_entity_tags,
)

logger = logging.getLogger(__name__)

# 1次 decisions の展開上限
DECISIONS_FULL_LIMIT = 15


def _get_direct_relations(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> dict[str, list[int]]:
    """relations_viewから直接関連エンティティのIDをtype別に取得する。

    Returns:
        {"topic": [id, ...], "activity": [id, ...]}
    """
    rows = conn.execute(
        "SELECT target_type, target_id FROM relations_view WHERE source_type = ? AND source_id = ?",
        (entity_type, entity_id),
    ).fetchall()

    result: dict[str, list[int]] = {"topic": [], "activity": []}
    for row in rows:
        target_type = row["target_type"]
        if target_type in result:
            result[target_type].append(row["target_id"])
    return result


def _get_topics_info(conn: sqlite3.Connection, topic_ids: list[int]) -> list[dict]:
    """複数トピックの基本情報を取得する。"""
    if not topic_ids:
        return []
    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"SELECT id, title FROM discussion_topics WHERE id IN ({placeholders})",
        tuple(topic_ids),
    ).fetchall()
    return [{"id": row["id"], "title": row["title"]} for row in rows]


def _get_activities_overview(conn: sqlite3.Connection, activity_ids: list[int]) -> list[dict]:
    """複数アクティビティの概要を取得する（1次展開用）。"""
    if not activity_ids:
        return []
    placeholders = ",".join("?" * len(activity_ids))
    rows = conn.execute(
        f"SELECT id, title, status FROM activities WHERE id IN ({placeholders})",
        tuple(activity_ids),
    ).fetchall()
    return [{"id": row["id"], "title": row["title"], "status": row["status"]} for row in rows]


def _get_decisions_from_topics(conn: sqlite3.Connection, topic_ids: list[int]) -> list[dict]:
    """複数トピックの非pinnedのdecisionsを横断取得し、新しい順にフラット化する。

    上位DECISIONS_FULL_LIMIT件はid+title。pinnedは除外される（別途取得）。
    """
    if not topic_ids:
        return []
    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"""
        SELECT id, decision
        FROM decisions
        WHERE topic_id IN ({placeholders}) AND pinned = 0 AND retracted_at IS NULL
        ORDER BY id DESC
        LIMIT {DECISIONS_FULL_LIMIT}
        """,
        tuple(topic_ids),
    ).fetchall()

    decisions = []
    for row in rows:
        decisions.append({"id": row["id"], "title": row["decision"]})
    return decisions


def _count_decisions_from_topics(conn: sqlite3.Connection, topic_ids: list[int]) -> int:
    """複数トピックのdecisionsの総件数を取得する（pinned含む、retracted除外、coverage分母用）。"""
    if not topic_ids:
        return 0
    placeholders = ",".join("?" * len(topic_ids))
    row = conn.execute(
        f"SELECT COUNT(*) FROM decisions WHERE topic_id IN ({placeholders}) AND retracted_at IS NULL",
        tuple(topic_ids),
    ).fetchone()
    return row[0] if row else 0


def _get_logs_catalog_from_topics(
    conn: sqlite3.Connection, topic_ids: list[int]
) -> tuple[dict | None, list[dict]]:
    """複数トピックの非pinnedのlogsを横断取得し、新しい順にフラット化する。

    最新1件はcontent付き、残りはid + titleのカタログとして返す。

    Returns:
        (latest_log, catalog): latest_logは最新1件(content付き)またはNone、
        catalogは残りのid+titleリスト
    """
    if not topic_ids:
        return None, []
    placeholders = ",".join("?" * len(topic_ids))
    params = tuple(topic_ids)

    # 最新1件: content付き
    latest_row = conn.execute(
        f"""
        SELECT id, title, content
        FROM discussion_logs
        WHERE topic_id IN ({placeholders}) AND pinned = 0 AND retracted_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()

    if not latest_row:
        return None, []

    latest_log = {"id": latest_row["id"], "title": latest_row["title"], "content": latest_row["content"]}

    # 残り: id + titleのみ
    catalog_rows = conn.execute(
        f"""
        SELECT id, title
        FROM discussion_logs
        WHERE topic_id IN ({placeholders}) AND pinned = 0 AND retracted_at IS NULL AND id != ?
        ORDER BY id DESC
        """,
        params + (latest_row["id"],),
    ).fetchall()

    catalog = [{"id": row["id"], "title": row["title"]} for row in catalog_rows]
    return latest_log, catalog


def _get_pinned_decisions_from_topics(conn: sqlite3.Connection, topic_ids: list[int]) -> list[dict]:
    """関連トピックのpinned decisionsをcontent付きで取得する。"""
    if not topic_ids:
        return []
    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"""
        SELECT id, decision, reason
        FROM decisions
        WHERE topic_id IN ({placeholders}) AND pinned = 1 AND retracted_at IS NULL
        ORDER BY id DESC
        """,
        tuple(topic_ids),
    ).fetchall()
    return [{"id": row["id"], "title": row["decision"], "reason": row["reason"]} for row in rows]


def _get_pinned_logs_from_topics(conn: sqlite3.Connection, topic_ids: list[int]) -> list[dict]:
    """関連トピックのpinned logsをcontent付きで取得する。"""
    if not topic_ids:
        return []
    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"""
        SELECT id, title, content
        FROM discussion_logs
        WHERE topic_id IN ({placeholders}) AND pinned = 1 AND retracted_at IS NULL
        ORDER BY id DESC
        """,
        tuple(topic_ids),
    ).fetchall()
    return [{"id": row["id"], "title": row["title"], "content": row["content"]} for row in rows]


def _get_pinned_materials_for_activity(conn: sqlite3.Connection, activity_id: int) -> list[dict]:
    """アクティビティに紐づくpinned materialsをcontent付きで取得する。"""
    rows = conn.execute(
        """
        SELECT m.id, m.title, m.content, m.source
        FROM materials m
        JOIN relations r ON r.source_type = 'activity' AND r.source_id = ?
                        AND r.target_type = 'material' AND r.target_id = m.id
        WHERE m.pinned = 1
        ORDER BY m.created_at ASC
        """,
        (activity_id,),
    ).fetchall()
    return [{"id": row["id"], "title": row["title"], "content": row["content"], "source": row["source"]} for row in rows]


def _extract_intent_tag(tags: list[str]) -> str:
    """タグリストからintent:プレフィックスのタグを抽出する。なければ「(未設定)」。"""
    for tag in tags:
        if tag.startswith("intent:"):
            return tag.split(":", 1)[1]
    return "(未設定)"


def _build_summary(
    activity: dict,
    tags: list[str],
) -> str:
    """summary文字列を生成する。

    フォーマット:
        check-in: タイトル
          intent: xxx
    """
    intent = _extract_intent_tag(tags)

    line1 = f"check-in: {activity['title']}"
    line2 = f"  intent: {intent}"

    return f"{line1}\n{line2}"


def check_in(activity_id: int) -> dict:
    """アクティビティにcheck-inする。

    関連情報（tag_notes, materials, decisions, logs catalog, catalog）を集約取得し、
    status自動更新とsummary生成を行う。

    リレーション対応:
    - 1次（直接関連）: 関連topicのdecisions（フラット15件、新しい順）+ 関連activityの概要
    - 2次: get_mapによるカタログ（id, type, title, tags）

    statusがin_progress以外（pending, completed含む）の場合はin_progressに自動更新する。
    completedのアクティビティも再オープンされる（追加作業が発生したケースに対応）。

    tag_notesの注入ルール:
    - 通常タグ: セッション内初回遭遇時のみ注入される（_injected_tags管理）。
      同一セッションで同じタグを持つアクティビティに2回check-inすると、
      2回目のtag_notesは空になる。
    - always_inject_namespaces対象タグ（例: intent:）: 毎回注入される。
      _injected_tagsによるフィルタをスキップし、check-inのたびにnotesを返す。

    Args:
        activity_id: アクティビティID

    Returns:
        check-in結果（coverage, activity, related_topics, related_activities, pinned,
        tag_notes, materials, recent_decisions, logs, catalog, summary）
    """
    conn = get_connection()
    try:
        # 1. activity取得
        row = conn.execute(
            "SELECT * FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
        if row is None:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Activity with id {activity_id} not found",
                }
            }

        activity = row_to_dict(row)
        tags = get_entity_tags(conn, "activity_tags", "activity_id", activity_id)

        # 2. 直接関連エンティティ取得（1次）
        direct = _get_direct_relations(conn, "activity", activity_id)

        # 2a. 関連トピック情報
        related_topics = _get_topics_info(conn, direct["topic"])

        # 2b. 関連アクティビティ概要
        related_activities = _get_activities_overview(conn, direct["activity"])

        # 2c. depends_on情報取得
        dep_rows = conn.execute(
            """SELECT a.id, a.title, a.status
               FROM activity_dependencies ad
               JOIN activities a ON a.id = ad.dependency_id
               WHERE ad.dependent_id = ?""",
            (activity_id,),
        ).fetchall()
        dependencies = [{"id": r["id"], "title": r["title"], "status": r["status"]} for r in dep_rows]

        # 3. tag_notes収集
        tag_notes = collect_tag_notes_for_injection(conn, tags, always_inject_namespaces=["intent"]) or []

        # 4. pinnedエンティティ取得（content付き）
        pinned_decisions = _get_pinned_decisions_from_topics(conn, direct["topic"])
        pinned_logs = _get_pinned_logs_from_topics(conn, direct["topic"])
        pinned_materials = _get_pinned_materials_for_activity(conn, activity_id)
        pinned_material_ids = {m["id"] for m in pinned_materials}

        # 5. materials取得（リレーション経由、カタログ形式、pinnedを除外）
        all_materials = get_materials_by_relation_with_conn(conn, activity_id)
        materials = [m for m in all_materials if m["id"] not in pinned_material_ids]

        # 6. recent_decisions取得（関連topic横断、フラット15件、pinnedを除外）
        recent_decisions = _get_decisions_from_topics(conn, direct["topic"])

        # 7. logs取得（最新1件はcontent付き、残りはカタログ、pinnedを除外）
        latest_log, logs_catalog = _get_logs_catalog_from_topics(conn, direct["topic"])

        # 8. coverage算出（pinned件数・最新ログを分子に加算）
        total_decisions = _count_decisions_from_topics(conn, direct["topic"])
        total_materials_row = conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source_type = 'activity' AND source_id = ? AND target_type = 'material'",
            (activity_id,),
        ).fetchone()
        total_materials = total_materials_row[0] if total_materials_row else 0
        total_logs = (1 if latest_log else 0) + len(logs_catalog) + len(pinned_logs)
        loaded_logs = len(pinned_logs) + (1 if latest_log else 0)

        coverage = {
            "decisions": f"{len(pinned_decisions) + len(recent_decisions)}/{total_decisions}",
            "materials": f"{len(pinned_materials) + len(materials)}/{total_materials}",
            "logs": f"{loaded_logs}/{total_logs}",
        }

        # 9. 2次カタログ取得（depth 1-2）
        catalog = _get_map_with_conn(conn, "activity", activity_id, min_depth=1, max_depth=2)

        # 10. status自動更新（in_progress以外ならin_progressに変更）
        # NOTE: update_activityは内部で別コネクションを使用する（既存APIの制約）。
        # check_inのトランザクションとは独立してコミットされる。
        if activity["status"] != "in_progress":
            update_result = activity_service.update_activity(activity_id, status="in_progress")
            if "error" in update_result:
                logger.warning(
                    "Failed to update activity %d status: %s",
                    activity_id,
                    update_result["error"],
                )
            else:
                activity["status"] = "in_progress"

        # 11. summary生成
        summary = _build_summary(activity, tags)

        # 戻り値組み立て（coverageをトップレベルの最初のキーに）
        result = {
            "coverage": coverage,
            "activity": {
                "id": activity["id"],
                "title": activity["title"],
                "description": activity["description"],
                "status": activity["status"],
                "tags": tags,
            },
        }

        if related_topics:
            if len(related_topics) == 1:
                result["topic"] = related_topics[0]
            result["related_topics"] = related_topics

        if related_activities:
            result["related_activities"] = related_activities

        if dependencies:
            result["dependencies"] = dependencies

        if pinned_decisions or pinned_logs or pinned_materials:
            result["pinned"] = {
                "decisions": pinned_decisions,
                "logs": pinned_logs,
                "materials": pinned_materials,
            }

        result["tag_notes"] = tag_notes
        result["materials"] = materials
        result["recent_decisions"] = recent_decisions
        result["latest_log"] = latest_log
        result["logs"] = logs_catalog
        if catalog:
            result["catalog"] = catalog
        result["summary"] = summary

        return result

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()
