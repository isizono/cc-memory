"""エンティティ間リレーション管理サービス"""
import logging
import sqlite3

from src.db import get_connection
from src.services.tag_service import (
    get_entity_tags_batch,
)

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"topic", "activity", "material"}
VALID_RELATION_TYPES = {"related", "depends_on"}


def _validate_entity_type(entity_type: str) -> dict | None:
    """エンティティタイプをバリデーションする。不正な場合はエラーdictを返す。"""
    if entity_type not in VALID_ENTITY_TYPES:
        return {
            "error": {
                "code": "INVALID_ENTITY_TYPE",
                "message": f"Invalid entity type: '{entity_type}'. Must be one of {sorted(VALID_ENTITY_TYPES)}",
            }
        }
    return None


def _validate_targets(source_type: str, targets: list[dict]) -> dict | None:
    """targetsのバリデーション。不正な場合はエラーdictを返す。"""
    if not targets:
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "targets must not be empty",
            }
        }
    for target in targets:
        if "type" not in target or "ids" not in target:
            return {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Each target must have 'type' and 'ids' fields",
                }
            }
        err = _validate_entity_type(target["type"])
        if err:
            return err
        if not isinstance(target["ids"], list) or not target["ids"]:
            return {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"'ids' for type '{target['type']}' must be a non-empty list",
                }
            }
        # material同士のリレーションは非サポート
        if source_type == "material" and target["type"] == "material":
            return {
                "error": {
                    "code": "UNSUPPORTED_RELATION",
                    "message": "material-to-material relations are not supported",
                }
            }
    return None


def _get_insert_params(source_type: str, source_id: int, target_type: str, target_id: int):
    """source/targetの組み合わせから、適切なテーブルとINSERTパラメータを返す。

    Returns:
        (table_name, columns, values) or None（自己参照の場合）
    """
    # 自己参照チェック
    if source_type == target_type and source_id == target_id:
        return None

    if source_type == "topic" and target_type == "topic":
        # 正規化: id_1 < id_2
        id_1, id_2 = min(source_id, target_id), max(source_id, target_id)
        return ("topic_relations", "(topic_id_1, topic_id_2)", (id_1, id_2))
    elif source_type == "topic" and target_type == "activity":
        return ("topic_activity_relations", "(topic_id, activity_id)", (source_id, target_id))
    elif source_type == "activity" and target_type == "topic":
        return ("topic_activity_relations", "(topic_id, activity_id)", (target_id, source_id))
    elif source_type == "activity" and target_type == "activity":
        # 正規化: id_1 < id_2
        id_1, id_2 = min(source_id, target_id), max(source_id, target_id)
        return ("activity_relations", "(activity_id_1, activity_id_2)", (id_1, id_2))
    elif source_type == "topic" and target_type == "material":
        return ("topic_material_relations", "(topic_id, material_id)", (source_id, target_id))
    elif source_type == "material" and target_type == "topic":
        return ("topic_material_relations", "(topic_id, material_id)", (target_id, source_id))
    elif source_type == "activity" and target_type == "material":
        return ("activity_material_relations", "(activity_id, material_id)", (source_id, target_id))
    elif source_type == "material" and target_type == "activity":
        return ("activity_material_relations", "(activity_id, material_id)", (target_id, source_id))
    else:
        raise ValueError(f"Unexpected type combination: {source_type}/{target_type}")


def _get_delete_params(source_type: str, source_id: int, target_type: str, target_id: int):
    """source/targetの組み合わせから、適切なテーブルとDELETE条件を返す。

    Returns:
        (table_name, where_clause, values) or None（自己参照の場合）

    Raises:
        ValueError: 不正なtype組み合わせ（バリデーション済みなら到達しない）
    """
    # 自己参照チェック（_get_insert_paramsと対称）
    if source_type == target_type and source_id == target_id:
        return None

    if source_type == "topic" and target_type == "topic":
        id_1, id_2 = min(source_id, target_id), max(source_id, target_id)
        return ("topic_relations", "topic_id_1 = ? AND topic_id_2 = ?", (id_1, id_2))
    elif source_type == "topic" and target_type == "activity":
        return ("topic_activity_relations", "topic_id = ? AND activity_id = ?", (source_id, target_id))
    elif source_type == "activity" and target_type == "topic":
        return ("topic_activity_relations", "topic_id = ? AND activity_id = ?", (target_id, source_id))
    elif source_type == "activity" and target_type == "activity":
        id_1, id_2 = min(source_id, target_id), max(source_id, target_id)
        return ("activity_relations", "activity_id_1 = ? AND activity_id_2 = ?", (id_1, id_2))
    elif source_type == "topic" and target_type == "material":
        return ("topic_material_relations", "topic_id = ? AND material_id = ?", (source_id, target_id))
    elif source_type == "material" and target_type == "topic":
        return ("topic_material_relations", "topic_id = ? AND material_id = ?", (target_id, source_id))
    elif source_type == "activity" and target_type == "material":
        return ("activity_material_relations", "activity_id = ? AND material_id = ?", (source_id, target_id))
    elif source_type == "material" and target_type == "activity":
        return ("activity_material_relations", "activity_id = ? AND material_id = ?", (target_id, source_id))
    else:
        raise ValueError(f"Unexpected type combination: {source_type}/{target_type}")


def _has_dependency_path(conn: sqlite3.Connection, from_id: int, to_id: int) -> bool:
    """DFSでfrom_idからto_idへのdepends_on経路が存在するか判定する。

    activity_dependenciesテーブルを辿り、from_id → ... → to_id の到達可能性をチェックする。
    循環依存検出に使用: 新たに dependent→dependency を追加する前に、
    dependency→dependent への既存経路があればサイクルになる。
    """
    visited: set[int] = set()
    stack = [from_id]
    while stack:
        current = stack.pop()
        if current == to_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            "SELECT dependency_id FROM activity_dependencies WHERE dependent_id = ?",
            (current,),
        ).fetchall()
        for row in rows:
            stack.append(row["dependency_id"])
    return False


def _add_depends_on_with_conn(conn: sqlite3.Connection, source_id: int, target_ids: list[int]) -> int:
    """depends_onリレーションをactivity_dependenciesテーブルに追加する。

    循環依存を検出した場合はValueErrorを送出する。

    Args:
        conn: DB接続
        source_id: 依存元（dependent）のアクティビティID
        target_ids: 依存先（dependency）のアクティビティIDリスト

    Returns:
        追加件数

    Raises:
        ValueError: 循環依存が検出された場合
    """
    added = 0
    for target_id in target_ids:
        # 自己参照はCHECK制約で弾かれるが、明示的にスキップ
        if source_id == target_id:
            continue

        # 循環チェック: target_id → source_id への経路が既に存在すればサイクル
        if _has_dependency_path(conn, target_id, source_id):
            raise ValueError(
                f"Circular dependency detected: adding {source_id}→{target_id} "
                f"would create a cycle"
            )

        conn.execute(
            "INSERT OR IGNORE INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
            (source_id, target_id),
        )
        if conn.execute("SELECT changes()").fetchone()[0] > 0:
            added += 1
    return added


def _add_relation_with_conn(conn: sqlite3.Connection, source_type: str, source_id: int, targets: list[dict]) -> int:
    """conn共有版: リレーションを追加する。追加件数を返す。"""
    added = 0
    for target in targets:
        target_type = target["type"]
        for target_id in target["ids"]:
            params = _get_insert_params(source_type, source_id, target_type, target_id)
            if params is None:
                # 自己参照はスキップ
                continue
            table, columns, values = params
            placeholders = ", ".join("?" for _ in values)
            conn.execute(
                f"INSERT OR IGNORE INTO {table} {columns} VALUES ({placeholders})",
                values,
            )
            # INSERT OR IGNOREの場合、重複時はchanges()=0
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                added += 1
    return added


def _remove_relation_with_conn(conn: sqlite3.Connection, source_type: str, source_id: int, targets: list[dict]) -> int:
    """conn共有版: リレーションを削除する。削除件数を返す。"""
    removed = 0
    for target in targets:
        target_type = target["type"]
        for target_id in target["ids"]:
            params = _get_delete_params(source_type, source_id, target_type, target_id)
            if params is None:
                continue
            table, where_clause, values = params
            conn.execute(
                f"DELETE FROM {table} WHERE {where_clause}",
                values,
            )
            removed += conn.execute("SELECT changes()").fetchone()[0]
    return removed


def add_relation(source_type: str, source_id: int, targets: list[dict], relation_type: str = "related") -> dict:
    """リレーションを追加する。

    Args:
        source_type: 起点エンティティのタイプ（"topic", "activity", or "material"）
        source_id: 起点エンティティのID
        targets: ターゲットリスト [{"type": "topic", "ids": [1, 2]}, ...]
        relation_type: リレーションタイプ（"related" or "depends_on"）。
            "depends_on" はactivity同士のみ有効で、循環依存を検出した場合はエラーを返す。

    Returns:
        成功時: {"added": int}
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    if relation_type not in VALID_RELATION_TYPES:
        return {
            "error": {
                "code": "INVALID_RELATION_TYPE",
                "message": f"Invalid relation_type: '{relation_type}'. Must be one of {sorted(VALID_RELATION_TYPES)}",
            }
        }

    err = _validate_entity_type(source_type)
    if err:
        return err
    err = _validate_targets(source_type, targets)
    if err:
        return err

    # depends_onはactivity→activityのみ
    if relation_type == "depends_on":
        if source_type != "activity":
            return {
                "error": {
                    "code": "INVALID_RELATION_TYPE",
                    "message": "depends_on relation is only valid for activity→activity",
                }
            }
        for target in targets:
            if target["type"] != "activity":
                return {
                    "error": {
                        "code": "INVALID_RELATION_TYPE",
                        "message": "depends_on relation is only valid for activity→activity",
                    }
                }

    conn = get_connection()
    try:
        if relation_type == "depends_on":
            added = 0
            for target in targets:
                added += _add_depends_on_with_conn(conn, source_id, target["ids"])
        else:
            added = _add_relation_with_conn(conn, source_type, source_id, targets)
        conn.commit()
        return {"added": added}
    except ValueError as e:
        conn.rollback()
        logger.warning(f"add_relation rejected: {e}")
        return {"error": {"code": "CIRCULAR_DEPENDENCY", "message": str(e)}}
    except sqlite3.IntegrityError as e:
        conn.rollback()
        logger.error(f"add_relation failed: {e}")
        return {"error": {"code": "CONSTRAINT_VIOLATION", "message": str(e)}}
    except Exception as e:
        conn.rollback()
        logger.error(f"add_relation failed: {e}")
        return {"error": {"code": "ADD_RELATION_FAILED", "message": str(e)}}
    finally:
        conn.close()


def remove_relation(source_type: str, source_id: int, targets: list[dict]) -> dict:
    """リレーションを削除する。

    Args:
        source_type: 起点エンティティのタイプ（"topic" or "activity"）
        source_id: 起点エンティティのID
        targets: ターゲットリスト [{"type": "topic", "ids": [1, 2]}, ...]

    Returns:
        成功時: {"removed": int}
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    err = _validate_entity_type(source_type)
    if err:
        return err
    err = _validate_targets(source_type, targets)
    if err:
        return err

    conn = get_connection()
    try:
        removed = _remove_relation_with_conn(conn, source_type, source_id, targets)
        conn.commit()
        return {"removed": removed}
    except Exception as e:
        conn.rollback()
        logger.error(f"remove_relation failed: {e}")
        return {"error": {"code": "REMOVE_RELATION_FAILED", "message": str(e)}}
    finally:
        conn.close()


def _get_map_with_conn(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    min_depth: int = 0,
    max_depth: int = 2,
) -> list[dict]:
    """conn共有版: 再帰CTEでリレーショングラフを走査し、到達可能エンティティを返す。"""
    rows = conn.execute(
        """
        WITH RECURSIVE reachable(entity_type, entity_id, depth) AS (
            SELECT ?, ?, 0
            UNION
            SELECT r.target_type, r.target_id, re.depth + 1
            FROM reachable re
            JOIN relations_view r
              ON r.source_type = re.entity_type AND r.source_id = re.entity_id
            WHERE re.depth < ?
        )
        SELECT DISTINCT entity_type, entity_id, MIN(depth) AS depth
        FROM reachable
        WHERE depth >= ?
        GROUP BY entity_type, entity_id
        """,
        (entity_type, entity_id, max_depth, min_depth),
    ).fetchall()

    # エンティティのタイプ別にIDを収集
    topic_ids = [row["entity_id"] for row in rows if row["entity_type"] == "topic"]
    activity_ids = [row["entity_id"] for row in rows if row["entity_type"] == "activity"]
    material_ids = [row["entity_id"] for row in rows if row["entity_type"] == "material"]

    # タイトルをバッチ取得
    topic_titles = {}
    if topic_ids:
        placeholders = ",".join("?" * len(topic_ids))
        title_rows = conn.execute(
            f"SELECT id, title FROM discussion_topics WHERE id IN ({placeholders})",
            tuple(topic_ids),
        ).fetchall()
        topic_titles = {r["id"]: r["title"] for r in title_rows}

    activity_titles = {}
    if activity_ids:
        placeholders = ",".join("?" * len(activity_ids))
        title_rows = conn.execute(
            f"SELECT id, title FROM activities WHERE id IN ({placeholders})",
            tuple(activity_ids),
        ).fetchall()
        activity_titles = {r["id"]: r["title"] for r in title_rows}

    material_titles = {}
    if material_ids:
        placeholders = ",".join("?" * len(material_ids))
        title_rows = conn.execute(
            f"SELECT id, title FROM materials WHERE id IN ({placeholders})",
            tuple(material_ids),
        ).fetchall()
        material_titles = {r["id"]: r["title"] for r in title_rows}

    # タグをバッチ取得
    topic_tags_map = get_entity_tags_batch(conn, "topic_tags", "topic_id", topic_ids) if topic_ids else {}
    activity_tags_map = get_entity_tags_batch(conn, "activity_tags", "activity_id", activity_ids) if activity_ids else {}
    material_tags_map = get_entity_tags_batch(conn, "material_tags", "material_id", material_ids) if material_ids else {}

    # 存在するIDのセットを構築（存在しないIDを除外するため）
    existing_ids = set()
    existing_ids.update(("topic", tid) for tid in topic_titles)
    existing_ids.update(("activity", aid) for aid in activity_titles)
    existing_ids.update(("material", mid) for mid in material_titles)

    # カタログ構築（存在しないエンティティは除外）
    entities = []
    for row in rows:
        etype = row["entity_type"]
        eid = row["entity_id"]
        depth = row["depth"]

        if (etype, eid) not in existing_ids:
            continue

        if etype == "topic":
            title = topic_titles[eid]
            tags = topic_tags_map.get(eid, [])
        elif etype == "activity":
            title = activity_titles[eid]
            tags = activity_tags_map.get(eid, [])
        elif etype == "material":
            title = material_titles[eid]
            tags = material_tags_map.get(eid, [])
        else:
            continue

        entities.append({
            "type": etype,
            "id": eid,
            "title": title,
            "tags": tags,
            "depth": depth,
        })

    # depth順、同depth内はtype→id順でソート
    entities.sort(key=lambda e: (e["depth"], e["type"], e["id"]))

    return entities


def get_map(entity_type: str, entity_id: int, min_depth: int = 0, max_depth: int = 2) -> dict:
    """リレーショングラフを走査し、到達可能エンティティのカタログを返す。

    Args:
        entity_type: 起点エンティティのタイプ（"topic" or "activity"）
        entity_id: 起点エンティティのID
        min_depth: 最小深度（デフォルト: 0）
        max_depth: 最大深度（デフォルト: 2）

    Returns:
        成功時: {"entities": [...], "total_count": int}
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    err = _validate_entity_type(entity_type)
    if err:
        return err

    if min_depth < 0:
        return {
            "error": {
                "code": "INVALID_PARAMETER",
                "message": "min_depth must be >= 0",
            }
        }
    if max_depth < min_depth:
        return {
            "error": {
                "code": "INVALID_PARAMETER",
                "message": "max_depth must be >= min_depth",
            }
        }
    if max_depth > 10:
        return {
            "error": {
                "code": "INVALID_PARAMETER",
                "message": "max_depth must be <= 10",
            }
        }

    conn = get_connection()
    try:
        entities = _get_map_with_conn(conn, entity_type, entity_id, min_depth, max_depth)
        return {
            "entities": entities,
            "total_count": len(entities),
        }
    except Exception as e:
        logger.error(f"get_map failed: {e}")
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        conn.close()
