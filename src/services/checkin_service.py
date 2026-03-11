"""check-inサービス"""
import logging
import sqlite3

from src.db import get_connection, row_to_dict
from src.services import activity_service, decision_service
from src.services.material_service import get_materials_by_activity_with_conn
from src.services.tag_service import (
    collect_tag_notes_for_injection,
    get_entity_tags,
)

logger = logging.getLogger(__name__)


def _get_topic_id(conn, activity_id: int) -> int | None:
    """activitiesテーブルからtopic_idを取得する。

    topic_idカラムが存在しない場合やNULLの場合はNoneを返す。
    """
    try:
        row = conn.execute(
            "SELECT topic_id FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
        if row is None:
            return None
        return row["topic_id"]
    except sqlite3.OperationalError:
        # topic_idカラムが存在しない場合（migration 0010で削除済み）
        return None


def _get_topic_info(conn, topic_id: int) -> dict | None:
    """discussion_topicsからトピック情報を取得する。"""
    row = conn.execute(
        "SELECT id, title FROM discussion_topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "title": row["title"]}


def _extract_mode_tag(tags: list[str]) -> str:
    """タグリストからmode:プレフィックスのタグを抽出する。なければ「(未設定)」。"""
    for tag in tags:
        if tag.startswith("mode:"):
            return tag.split(":", 1)[1]
    return "(未設定)"


def _count_notes_lines(tag_notes: list[dict]) -> int:
    """tag_notesの合計行数を数える。"""
    total = 0
    for note in tag_notes:
        text = note["notes"]
        total += text.count("\n") + 1 if text else 0
    return total


def _build_summary(
    activity: dict,
    tags: list[str],
    tag_notes: list[dict],
    materials: list[dict],
) -> str:
    """summary文字列を生成する。

    フォーマット:
        check-in: タイトル
          notes: N件 (M行) | mode: xxx | 資材: N件
    """
    mode = _extract_mode_tag(tags)
    notes_count = len(tag_notes)
    notes_lines = _count_notes_lines(tag_notes)
    materials_count = len(materials)

    line1 = f"check-in: {activity['title']}"
    line2 = f"  notes: {notes_count}件 ({notes_lines}行) | mode: {mode} | 資材: {materials_count}件"

    return f"{line1}\n{line2}"


def check_in(activity_id: int) -> dict:
    """アクティビティにcheck-inする。

    関連情報（tag_notes, materials, decisions）を集約取得し、
    status自動更新とsummary生成を行う。

    statusがin_progress以外（pending, completed含む）の場合はin_progressに自動更新する。
    completedのアクティビティも再オープンされる（追加作業が発生したケースに対応）。

    tag_notesはセッション内初回遭遇時のみ注入される（_injected_tags管理）。
    同一セッションで同じタグを持つアクティビティに2回check-inすると、
    2回目のtag_notesは空になる。これはtag_service側の設計による意図的な動作。

    Args:
        activity_id: アクティビティID

    Returns:
        check-in結果（activity, topic, tag_notes, materials, recent_decisions, summary）
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

        # 2. topic取得（NULLなら省略）
        topic_id = _get_topic_id(conn, activity_id)
        topic_info = None
        if topic_id is not None:
            topic_info = _get_topic_info(conn, topic_id)

        # 3. tag_notes収集
        tag_notes = collect_tag_notes_for_injection(conn, tags) or []

        # 4. materials取得（カタログ形式、共有コネクション使用）
        materials = get_materials_by_activity_with_conn(conn, activity_id)

        # 5. recent_decisions取得（topic_idがある場合のみ）
        recent_decisions = []
        if topic_id is not None:
            decisions_result = decision_service.get_decisions(topic_id)
            if "error" in decisions_result:
                logger.warning(
                    "Failed to get decisions for topic %d: %s",
                    topic_id,
                    decisions_result["error"],
                )
            else:
                recent_decisions = [
                    {"id": d["id"], "title": d["decision"]}
                    for d in decisions_result.get("decisions", [])
                ]

        # 6. status自動更新（in_progress以外ならin_progressに変更）
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

        # 7. summary生成
        summary = _build_summary(activity, tags, tag_notes, materials)

        # 戻り値組み立て
        result = {
            "activity": {
                "id": activity["id"],
                "title": activity["title"],
                "status": activity["status"],
                "tags": tags,
            },
        }

        if topic_info is not None:
            result["topic"] = topic_info

        result["tag_notes"] = tag_notes
        result["materials"] = materials
        result["recent_decisions"] = recent_decisions
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
