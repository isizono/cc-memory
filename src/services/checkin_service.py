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
    """複数トピックのdecisionsを横断取得し、新しい順にフラット化する。

    上位DECISIONS_FULL_LIMIT件はid+title、超過分はid+titleのみ。
    """
    if not topic_ids:
        return []
    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"""
        SELECT id, decision
        FROM decisions
        WHERE topic_id IN ({placeholders})
        ORDER BY id DESC
        LIMIT {DECISIONS_FULL_LIMIT}
        """,
        tuple(topic_ids),
    ).fetchall()

    decisions = []
    for row in rows:
        decisions.append({"id": row["id"], "title": row["decision"]})
    return decisions


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

    関連情報（tag_notes, materials, decisions, catalog）を集約取得し、
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
        check-in結果（activity, related_topics, related_activities, tag_notes,
        materials, recent_decisions, catalog, summary）
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

        # 3. tag_notes収集
        tag_notes = collect_tag_notes_for_injection(conn, tags, always_inject_namespaces=["intent"]) or []

        # 4. materials取得（リレーション経由、カタログ形式、共有コネクション使用）
        materials = get_materials_by_relation_with_conn(conn, activity_id)

        # 5. recent_decisions取得（関連topic横断、フラット15件）
        recent_decisions = _get_decisions_from_topics(conn, direct["topic"])

        # 6. 2次カタログ取得（depth 1-2）
        catalog = _get_map_with_conn(conn, "activity", activity_id, min_depth=1, max_depth=2)

        # 7. status自動更新（in_progress以外ならin_progressに変更）
        # NOTE: update_activityは内部で別コネクションを使用する（既存APIの制約）。
        # check_inのトランザクションとは独立してコミットされる。
        if activity["status"] != "in_progress":
            update_result = activity_service.update_activity(activity_id, new_status="in_progress")
            if "error" in update_result:
                logger.warning(
                    "Failed to update activity %d status: %s",
                    activity_id,
                    update_result["error"],
                )
            else:
                activity["status"] = "in_progress"

        # 8. summary生成
        summary = _build_summary(activity, tags)

        # 戻り値組み立て
        result = {
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

        result["tag_notes"] = tag_notes
        result["materials"] = materials
        result["recent_decisions"] = recent_decisions
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
