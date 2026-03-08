"""タグ管理ユーティリティ"""
import sqlite3
from typing import Optional, Union

from src.db import execute_query, row_to_dict


VALID_NAMESPACES = {'', 'domain', 'scope', 'mode'}

# Entity table mapping (for UNION inheritance queries)
_ENTITY_TABLE = {
    "decision": "decisions",
    "log": "discussion_logs",
}


def parse_tag(tag_str: str) -> tuple[str, str]:
    """タグ文字列を (namespace, name) に分離する。

    Returns: (namespace, name)

    例:
      "domain:cc-memory"  -> ("domain", "cc-memory")
      "hooks"             -> ("", "hooks")
      "mode:design"       -> ("mode", "design")
      "scope:parent-topic" -> ("scope", "parent-topic")
    """
    if ":" in tag_str:
        namespace, name = tag_str.split(":", 1)
        return (namespace, name)
    return ("", tag_str)


def validate_and_parse_tags(
    tags: list[str],
    required: bool = False,
) -> Union[list[tuple[str, str]], dict]:
    """タグ配列をバリデーション・パースする。

    Args:
        tags: タグ文字列の配列
        required: Trueの場合、有効タグが0個のときエラーにする

    Returns:
        成功時: [(namespace, name), ...] の重複排除済みリスト
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    if required and not tags:
        return {"error": {"code": "TAGS_REQUIRED", "message": "At least one tag is required"}}

    parsed = []
    seen = set()
    for tag_str in tags:
        tag_str = tag_str.strip()
        if not tag_str:
            continue
        namespace, name = parse_tag(tag_str)

        if namespace not in VALID_NAMESPACES:
            return {"error": {
                "code": "INVALID_TAG_NAMESPACE",
                "message": f"Invalid namespace '{namespace}' in tag '{tag_str}'. "
                           f"Allowed: {sorted(VALID_NAMESPACES)}"
            }}

        if not name.strip():
            return {"error": {
                "code": "INVALID_TAG_NAME",
                "message": f"Tag name must not be empty in '{tag_str}'"
            }}

        key = (namespace, name)
        if key not in seen:
            seen.add(key)
            parsed.append(key)

    if required and not parsed:
        return {"error": {"code": "TAGS_REQUIRED", "message": "At least one tag is required"}}

    return parsed


def resolve_tag_ids(conn: sqlite3.Connection, parsed_tags: list[tuple[str, str]]) -> list[int]:
    """既存タグのIDのみを返す（INSERT しない）。

    存在しないタグは結果に含まれない。
    呼び出し元で len(result) < len(parsed_tags) をチェックすることで
    部分マッチを検出できる。
    """
    if not parsed_tags:
        return []
    placeholders = " OR ".join(
        "(namespace = ? AND name = ?)" for _ in parsed_tags
    )
    flat_params = [v for pair in parsed_tags for v in pair]
    rows = conn.execute(
        f"SELECT id, namespace, name FROM tags WHERE {placeholders}",
        flat_params,
    ).fetchall()
    id_map = {(row["namespace"], row["name"]): row["id"] for row in rows}
    return [id_map[(ns, name)] for ns, name in parsed_tags if (ns, name) in id_map]


def ensure_tag_ids(conn: sqlite3.Connection, parsed_tags: list[tuple[str, str]]) -> list[int]:
    """タグをINSERT OR IGNOREし、idのリストを返す。

    connを受け取り、呼び出し元のトランザクション内で動作する。
    """
    if not parsed_tags:
        return []
    conn.executemany(
        "INSERT OR IGNORE INTO tags (namespace, name) VALUES (?, ?)",
        parsed_tags,
    )
    placeholders = " OR ".join(
        "(namespace = ? AND name = ?)" for _ in parsed_tags
    )
    flat_params = [v for pair in parsed_tags for v in pair]
    rows = conn.execute(
        f"SELECT id, namespace, name FROM tags WHERE {placeholders}",
        flat_params,
    ).fetchall()
    id_map = {(row["namespace"], row["name"]): row["id"] for row in rows}
    return [id_map[(ns, name)] for ns, name in parsed_tags]


def link_tags(
    conn: sqlite3.Connection,
    junction_table: str,
    entity_column: str,
    entity_id: int,
    tag_ids: list[int],
) -> None:
    """中間テーブルにタグを紐付ける。"""
    if not tag_ids:
        return
    conn.executemany(
        f"INSERT OR IGNORE INTO {junction_table} ({entity_column}, tag_id) VALUES (?, ?)",
        [(entity_id, tid) for tid in tag_ids],
    )


def format_tags(tag_rows) -> list[str]:
    """DB行をタグ文字列のリストに変換する。

    namespace付き: "namespace:name"、素タグ: "name"
    アルファベット順ソート。
    """
    tags = []
    for row in tag_rows:
        ns = row["namespace"]
        name = row["name"]
        if ns:
            tags.append(f"{ns}:{name}")
        else:
            tags.append(name)
    return sorted(tags)


def get_entity_tags(
    conn: sqlite3.Connection,
    junction_table: str,
    entity_column: str,
    entity_id: int,
) -> list[str]:
    """エンティティに紐づくタグ文字列リストを取得する。"""
    rows = conn.execute(
        f"""
        SELECT t.namespace, t.name
        FROM tags t
        JOIN {junction_table} jt ON t.id = jt.tag_id
        WHERE jt.{entity_column} = ?
        """,
        (entity_id,),
    ).fetchall()
    return format_tags(rows)


def get_entity_tags_batch(
    conn: sqlite3.Connection,
    junction_table: str,
    entity_column: str,
    entity_ids: list[int],
) -> dict[int, list[str]]:
    """複数エンティティに紐づくタグ文字列リストを一括取得する。

    Returns: {entity_id: ["tag1", "tag2", ...], ...}
    """
    if not entity_ids:
        return {}
    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""
        SELECT jt.{entity_column} AS entity_id, t.namespace, t.name
        FROM tags t
        JOIN {junction_table} jt ON t.id = jt.tag_id
        WHERE jt.{entity_column} IN ({placeholders})
        """,
        tuple(entity_ids),
    ).fetchall()

    groups: dict[int, list] = {}
    for row in rows:
        eid = row["entity_id"]
        if eid not in groups:
            groups[eid] = []
        groups[eid].append(row)

    return {eid: format_tags(tag_rows) for eid, tag_rows in groups.items()}


def get_effective_tags_batch(
    conn: sqlite3.Connection,
    entity_type: str,
    parent_topic_id: int,
) -> dict[int, list[str]]:
    """topic_id配下の全entity(decision/log)の有効タグを一括取得する。

    Returns: {entity_id: ["tag1", "tag2", ...], ...}
    """
    entity_table = _ENTITY_TABLE[entity_type]
    junction_table = f"{entity_type}_tags"
    id_column = f"{entity_type}_id"

    rows = conn.execute(
        f"""
        SELECT e.id AS entity_id, t.namespace, t.name
        FROM {entity_table} e
        JOIN topic_tags tt ON tt.topic_id = e.topic_id
        JOIN tags t ON t.id = tt.tag_id
        WHERE e.topic_id = ?

        UNION

        SELECT et.{id_column} AS entity_id, t.namespace, t.name
        FROM {junction_table} et
        JOIN tags t ON t.id = et.tag_id
        WHERE et.{id_column} IN (
            SELECT id FROM {entity_table} WHERE topic_id = ?
        )
        """,
        (parent_topic_id, parent_topic_id),
    ).fetchall()

    # entity_idごとにグルーピング
    groups: dict[int, list] = {}
    for row in rows:
        eid = row["entity_id"]
        if eid not in groups:
            groups[eid] = []
        groups[eid].append(row)

    # format_tagsで文字列配列に変換
    return {eid: format_tags(tag_rows) for eid, tag_rows in groups.items()}


def get_effective_tags(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> list[str]:
    """entity(decision/log)の有効タグ（topic_tags UNION entity_tags）を取得する。"""
    entity_table = _ENTITY_TABLE[entity_type]
    junction_table = f"{entity_type}_tags"
    id_column = f"{entity_type}_id"

    rows = conn.execute(
        f"""
        SELECT DISTINCT t.namespace, t.name
        FROM tags t
        WHERE t.id IN (
            SELECT tt.tag_id
            FROM topic_tags tt
            JOIN {entity_table} e ON e.topic_id = tt.topic_id
            WHERE e.id = ?

            UNION

            SELECT et.tag_id
            FROM {junction_table} et
            WHERE et.{id_column} = ?
        )
        """,
        (entity_id, entity_id),
    ).fetchall()
    return format_tags(rows)


def list_tags(namespace: Optional[str] = None) -> dict:
    """タグ一覧をusage_count付きで返す。

    Args:
        namespace: namespaceでフィルタ（未指定で全タグ）。
                   namespace=""で素タグ（namespaceなし）のみフィルタ。

    Returns:
        タグ一覧（id, namespace, name, tag, usage_count）をusage_count降順で返す
    """
    try:
        rows = execute_query(
            """
            SELECT t.id, t.namespace, t.name,
              (SELECT COUNT(*) FROM topic_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM task_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM decision_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM log_tags WHERE tag_id = t.id) AS usage_count
            FROM tags t
            WHERE (? IS NULL OR t.namespace = ?)
            ORDER BY usage_count DESC, t.name ASC
            """,
            (namespace, namespace),
        )
        tags = []
        for row in rows:
            r = row_to_dict(row)
            ns = r["namespace"]
            name = r["name"]
            tag_str = f"{ns}:{name}" if ns else name
            tags.append({
                "tag": tag_str,
                "id": r["id"],
                "namespace": ns,
                "name": name,
                "usage_count": r["usage_count"],
            })
        return {"tags": tags}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
