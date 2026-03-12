"""タグ管理ユーティリティ"""
import sqlite3
from typing import Optional, Union

from src.db import execute_query, get_connection, row_to_dict

VALID_NAMESPACES = {'', 'domain', 'intent'}

# resolve_tags 定数
MERGE_THRESHOLD = 0.15  # コサイン距離。これ未満なら統合
KNN_K = 10              # KNN検索の取得数（namespace後フィルタ前）。
                        # namespace別タグ数が偏る場合、フィルタ後の候補が0件になりうる。
                        # タグ総数が増加したら値の引き上げを検討すること。

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
      "intent:design"     -> ("intent", "design")
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
    エイリアスタグの場合はcanonical側のIDを返す。
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
        f"SELECT id, namespace, name, canonical_id FROM tags WHERE {placeholders}",
        flat_params,
    ).fetchall()
    id_map = {}
    for row in rows:
        effective_id = row["canonical_id"] if row["canonical_id"] is not None else row["id"]
        id_map[(row["namespace"], row["name"])] = effective_id
    return [id_map[(ns, name)] for ns, name in parsed_tags if (ns, name) in id_map]


def ensure_tag_ids(conn: sqlite3.Connection, parsed_tags: list[tuple[str, str]]) -> list[int]:
    """タグをINSERT OR IGNOREし、idのリストを返す。

    connを受け取り、呼び出し元のトランザクション内で動作する。
    エイリアスタグの場合はcanonical側のIDを返す。
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
        f"SELECT id, namespace, name, canonical_id FROM tags WHERE {placeholders}",
        flat_params,
    ).fetchall()
    id_map = {}
    for row in rows:
        effective_id = row["canonical_id"] if row["canonical_id"] is not None else row["id"]
        id_map[(row["namespace"], row["name"])] = effective_id
    return [id_map[(ns, name)] for ns, name in parsed_tags]


def resolve_tags(
    tags: list[str],
    force_new_tags: bool = False,
) -> tuple[list[int], list[dict]] | dict:
    """タグを解決する（あいまいマッチ付き）。

    処理フロー（タグ1つあたり）:
    1. パース: validate_and_parse_tags() を使用。namespace/name を lower().strip() で正規化
    2. 完全一致チェック: SELECT id FROM tags WHERE namespace=? AND name=?
    3. force_new_tags=True → 完全一致がなければ新規タグINSERT + embedding生成（KNN検索スキップ）
    4. KNN検索: tag_vec に対して embedding MATCH → namespace 後フィルタ
    5. 閾値判定: distance < MERGE_THRESHOLD → 既存タグIDに統合。なし → 新規作成 + embedding生成

    Args:
        tags: タグ文字列のリスト
        force_new_tags: Trueの場合、KNN検索をスキップして新規作成する
                        （ただし完全一致がある場合は既存IDを使用する）

    Returns:
        成功時: (tag_ids, merged_tags)
            - tag_ids: 解決済みタグIDリスト
            - merged_tags: [{"input": "hooks", "merged_to": "hook", "distance": 0.05}, ...]
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    from src.services.embedding_service import (
        generate_and_store_tag_embedding,
        search_similar_tags,
    )

    # 1. パース + 正規化 + バリデーション
    # validate_and_parse_tags は正規化前にnamespace検証するため、
    # resolve_tags では自前で parse_tag → lower/strip正規化 → バリデーション を行う
    normalized = []
    seen = set()
    for tag_str in tags:
        tag_str = tag_str.strip()
        if not tag_str:
            continue
        ns, name = parse_tag(tag_str)
        # 正規化: lower().strip()
        ns = ns.lower().strip()
        name = name.lower().strip()

        if ns not in VALID_NAMESPACES:
            return {"error": {
                "code": "INVALID_TAG_NAMESPACE",
                "message": f"Invalid namespace '{ns}' in tag '{tag_str}'. "
                           f"Allowed: {sorted(VALID_NAMESPACES)}"
            }}
        if not name:
            return {"error": {
                "code": "INVALID_TAG_NAME",
                "message": f"Tag name must not be empty in '{tag_str}'"
            }}

        key = (ns, name)
        if key not in seen:
            seen.add(key)
            normalized.append(key)

    if not normalized:
        return ([], [])

    conn = get_connection()
    try:
        resolved_ids: list[int] = []
        merged_tags: list[dict] = []
        seen_ids: set[int] = set()

        for ns, name in normalized:
            # 入力タグの表示用文字列
            input_tag_str = f"{ns}:{name}" if ns else name

            # 2. 完全一致チェック（canonical解決付き）
            row = conn.execute(
                "SELECT id, canonical_id FROM tags WHERE namespace = ? AND name = ?",
                (ns, name),
            ).fetchone()

            if row:
                tag_id = row["canonical_id"] if row["canonical_id"] is not None else row["id"]
                if tag_id not in seen_ids:
                    resolved_ids.append(tag_id)
                    seen_ids.add(tag_id)
                continue

            # 3. force_new_tags=True → KNN検索スキップ、新規作成
            # NOTE: ループ内で中間commit()している。generate_and_store_tag_embedding()が
            # 別コネクションを開くため、未コミットの行は参照できない制約による。
            # このため複数タグ処理の途中でエラーが発生した場合、前半のINSERTは
            # rollbackされない（アトミック性を犠牲にしている）。
            if force_new_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (namespace, name) VALUES (?, ?)",
                    (ns, name),
                )
                new_row = conn.execute(
                    "SELECT id FROM tags WHERE namespace = ? AND name = ?",
                    (ns, name),
                ).fetchone()
                tag_id = new_row["id"]
                if tag_id not in seen_ids:
                    resolved_ids.append(tag_id)
                    seen_ids.add(tag_id)
                conn.commit()
                # embedding生成（失敗してもエラーにしない）
                generate_and_store_tag_embedding(tag_id, name)
                continue

            # 4. KNN検索
            similar = search_similar_tags(name, k=KNN_K)

            # namespace後フィルタ + 閾値判定
            best_match = None
            for candidate_id, distance in similar:
                if distance >= MERGE_THRESHOLD:
                    continue
                # candidateのnamespaceを確認
                candidate_row = conn.execute(
                    "SELECT namespace, name FROM tags WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
                if candidate_row and candidate_row["namespace"] == ns:
                    best_match = (candidate_id, candidate_row["name"], distance)
                    break  # distance順なので最初のマッチがベスト

            if best_match:
                # 5a. 統合
                match_id, match_name, distance = best_match
                if match_id not in seen_ids:
                    resolved_ids.append(match_id)
                    seen_ids.add(match_id)
                merged_to_str = f"{ns}:{match_name}" if ns else match_name
                merged_tags.append({
                    "input": input_tag_str,
                    "merged_to": merged_to_str,
                    "distance": round(distance, 4),
                })
            else:
                # 5b. 新規作成 + embedding生成
                conn.execute(
                    "INSERT OR IGNORE INTO tags (namespace, name) VALUES (?, ?)",
                    (ns, name),
                )
                new_row = conn.execute(
                    "SELECT id FROM tags WHERE namespace = ? AND name = ?",
                    (ns, name),
                ).fetchone()
                tag_id = new_row["id"]
                if tag_id not in seen_ids:
                    resolved_ids.append(tag_id)
                    seen_ids.add(tag_id)
                conn.commit()
                # embedding生成（失敗してもエラーにしない）
                generate_and_store_tag_embedding(tag_id, name)

        conn.commit()
        return (resolved_ids, merged_tags)

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


def get_effective_tags_batch_by_ids(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_ids: list[int],
) -> dict[int, list[str]]:
    """複数entity(decision/log)の有効タグ（topic_tags UNION entity_tags）を一括取得する。

    get_effective_tagsのバッチ版。entity_idのリストを受け取り、
    各entityの有効タグをまとめて返す。

    Returns: {entity_id: ["tag1", "tag2", ...], ...}
    """
    if not entity_ids:
        return {}
    entity_table = _ENTITY_TABLE[entity_type]
    junction_table = f"{entity_type}_tags"
    id_column = f"{entity_type}_id"

    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""
        SELECT e.id AS entity_id, t.namespace, t.name
        FROM {entity_table} e
        JOIN topic_tags tt ON tt.topic_id = e.topic_id
        JOIN tags t ON t.id = tt.tag_id
        WHERE e.id IN ({placeholders})

        UNION

        SELECT et.{id_column} AS entity_id, t.namespace, t.name
        FROM {junction_table} et
        JOIN tags t ON t.id = et.tag_id
        WHERE et.{id_column} IN ({placeholders})
        """,
        (*entity_ids, *entity_ids),
    ).fetchall()

    # entity_idごとにグルーピング
    groups: dict[int, list] = {}
    for row in rows:
        eid = row["entity_id"]
        if eid not in groups:
            groups[eid] = []
        groups[eid].append(row)

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
        タグ一覧（id, namespace, name, tag, usage_count, notes）をusage_count降順で返す
    """
    try:
        rows = execute_query(
            """
            SELECT t.id, t.namespace, t.name, t.notes, t.canonical_id,
              ct.namespace AS canonical_namespace, ct.name AS canonical_name,
              (SELECT COUNT(*) FROM topic_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM activity_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM decision_tags WHERE tag_id = t.id) +
              (SELECT COUNT(*) FROM log_tags WHERE tag_id = t.id) AS usage_count
            FROM tags t
            LEFT JOIN tags AS ct ON t.canonical_id = ct.id
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

            # canonical文字列の構築
            canonical = None
            if r["canonical_id"] is not None:
                c_ns = r["canonical_namespace"]
                c_name = r["canonical_name"]
                canonical = f"{c_ns}:{c_name}" if c_ns else c_name

            tags.append({
                "tag": tag_str,
                "id": r["id"],
                "namespace": ns,
                "name": name,
                "usage_count": r["usage_count"],
                "notes": r["notes"],
                "canonical": canonical,
            })
        return {"tags": tags}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }


JUNCTION_TABLES = [
    ("topic_tags", "topic_id"),
    ("activity_tags", "activity_id"),
    ("decision_tags", "decision_id"),
    ("log_tags", "log_id"),
]


def update_tag(tag: str, notes: str | None = None, canonical: str | None = None) -> dict:
    """既存タグの notes（教訓・運用ルール）またはcanonical（エイリアス先）を更新する。

    Args:
        tag: タグ文字列（例: "domain:cc-memory", "hooks"）
        notes: 教訓・運用ルールのテキスト（全文置換）
        canonical: エイリアス先タグ文字列。設定するとtagがcanonicalのエイリアスになる。
                   ""（空文字）でエイリアス解除。上書き可能だが、旧canonical先に
                   付け替え済みの紐付けは戻らない。

    Returns:
        成功時: {"tag": str, "notes": str, "updated": True} (notes更新時)
                {"tag": str, "canonical": str | None, "updated": True} (canonical更新時)
        失敗時: {"error": {"code": ..., "message": ...}}
    """
    # バリデーション: 同時指定禁止
    if notes is not None and canonical is not None:
        return {
            "error": {
                "code": "CONFLICTING_PARAMS",
                "message": "Cannot specify both 'notes' and 'canonical'. Use separate calls.",
            }
        }

    # 少なくとも1つは指定必須
    if notes is None and canonical is None:
        return {
            "error": {
                "code": "MISSING_PARAMS",
                "message": "At least one of 'notes' or 'canonical' must be specified.",
            }
        }

    parsed = validate_and_parse_tags([tag])
    if isinstance(parsed, dict):
        return parsed
    namespace, name = parsed[0]

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, notes, canonical_id FROM tags WHERE namespace = ? AND name = ?",
            (namespace, name),
        ).fetchone()

        if not row:
            tag_display = f"{namespace}:{name}" if namespace else name
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Tag '{tag_display}' not found",
                }
            }

        tag_id = row["id"]
        tag_str = f"{namespace}:{name}" if namespace else name

        # --- notes 更新 ---
        if notes is not None:
            conn.execute(
                "UPDATE tags SET notes = ? WHERE id = ?",
                (notes, tag_id),
            )
            conn.commit()
            return {"tag": tag_str, "notes": notes, "updated": True}

        # --- canonical 更新 ---
        # canonical="" → エイリアス解除
        if canonical == "":
            conn.execute(
                "UPDATE tags SET canonical_id = NULL WHERE id = ?",
                (tag_id,),
            )
            conn.commit()
            return {"tag": tag_str, "canonical": None, "updated": True}

        # エイリアスタグにnotes有りの場合 → エラー（空文字もnotesなしとして扱う）
        if row["notes"]:
            return {
                "error": {
                    "code": "HAS_NOTES",
                    "message": f"Tag '{tag_str}' has notes. Remove notes before setting as alias.",
                }
            }

        # canonical先タグを解決
        parsed_canonical = validate_and_parse_tags([canonical])
        if isinstance(parsed_canonical, dict):
            return parsed_canonical
        c_namespace, c_name = parsed_canonical[0]

        c_row = conn.execute(
            "SELECT id, canonical_id FROM tags WHERE namespace = ? AND name = ?",
            (c_namespace, c_name),
        ).fetchone()

        if not c_row:
            c_display = f"{c_namespace}:{c_name}" if c_namespace else c_name
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Canonical tag '{c_display}' not found",
                }
            }

        canonical_id = c_row["id"]

        # 自分自身へのエイリアスは無意味なので禁止
        if canonical_id == tag_id:
            return {
                "error": {
                    "code": "CHAIN_NOT_ALLOWED",
                    "message": "Cannot set a tag as alias of itself.",
                }
            }

        # canonical先が既にエイリアス → 連鎖禁止
        if c_row["canonical_id"] is not None:
            return {
                "error": {
                    "code": "CHAIN_NOT_ALLOWED",
                    "message": "Canonical target is already an alias. Chains are not allowed.",
                }
            }

        # 自分が他タグのcanonical先になっている場合 → 連鎖禁止
        dependent = conn.execute(
            "SELECT id FROM tags WHERE canonical_id = ? LIMIT 1",
            (tag_id,),
        ).fetchone()
        if dependent:
            return {
                "error": {
                    "code": "CHAIN_NOT_ALLOWED",
                    "message": f"Tag '{tag_str}' is the canonical target of other aliases. "
                               "Remove those aliases first.",
                }
            }

        # canonical_id を設定
        conn.execute(
            "UPDATE tags SET canonical_id = ? WHERE id = ?",
            (canonical_id, tag_id),
        )

        # 紐付け付け替え: 中間テーブル4つ
        for table, entity_col in JUNCTION_TABLES:
            # 1. 重複する行を削除（canonical側IDが既に存在する場合）
            conn.execute(
                f"""
                DELETE FROM {table} WHERE {entity_col} IN (
                    SELECT a.{entity_col} FROM {table} a
                    INNER JOIN {table} b ON a.{entity_col} = b.{entity_col}
                    WHERE a.tag_id = ? AND b.tag_id = ?
                ) AND tag_id = ?
                """,
                (tag_id, canonical_id, tag_id),
            )
            # 2. 残りを付け替え
            conn.execute(
                f"UPDATE {table} SET tag_id = ? WHERE tag_id = ?",
                (canonical_id, tag_id),
            )

        conn.commit()

        c_tag_str = f"{c_namespace}:{c_name}" if c_namespace else c_name
        return {"tag": tag_str, "canonical": c_tag_str, "updated": True}

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


# ========================================
# 遭遇時注入（Tag Notes Injection）
# ========================================

# モジュールレベルのグローバル変数（MCPサーバープロセスのライフサイクル = セッション）
_injected_tags: set[str] = set()


def collect_tag_notes_for_injection(
    conn: sqlite3.Connection,
    tag_strings: list[str],
    always_inject_namespaces: list[str] | None = None,
) -> list[dict] | None:
    """未注入タグの notes を収集し、注入済みとしてマークする。

    Args:
        conn: DB接続
        tag_strings: タグ文字列リスト（例: ["domain:cc-memory", "intent:design"]）
        always_inject_namespaces: 常時注入するnamespaceのリスト（例: ["intent"]）。
            このnamespaceに属するタグは _injected_tags チェックをスキップし、
            毎回 notes を返す。_injected_tags には登録しない。

    Returns:
        notes があるタグの一覧。なければ None
        [{"tag": "domain:cc-memory", "notes": "..."}, ...]
    """
    always_ns = set(always_inject_namespaces) if always_inject_namespaces else set()

    # always_inject対象とそれ以外を分離（パース結果も保持）
    always_parsed = []
    normal_tags = []
    normal_parsed = []
    for t in tag_strings:
        ns, name = parse_tag(t)
        if ns in always_ns:
            always_parsed.append((ns, name))
        else:
            normal_tags.append(t)
            normal_parsed.append((ns, name))

    # 通常タグ: 未注入のもののみ
    new_normal = [
        (t, p) for t, p in zip(normal_tags, normal_parsed)
        if t not in _injected_tags
    ]

    # 通常タグをすべてマーク（notes の有無に関わらず）
    _injected_tags.update(t for t, _ in new_normal)

    # クエリ対象: new_normal + always（always_tagsは毎回クエリ）
    parsed = [p for _, p in new_normal] + always_parsed
    if not parsed:
        return None
    placeholders = " OR ".join(["(namespace = ? AND name = ?)"] * len(parsed))
    params = [v for pair in parsed for v in pair]
    rows = conn.execute(
        f"SELECT namespace, name, notes FROM tags WHERE ({placeholders}) AND notes IS NOT NULL",
        params
    ).fetchall()

    if not rows:
        return None

    results = []
    for row in rows:
        tag_str = f"{row['namespace']}:{row['name']}" if row["namespace"] else row["name"]
        results.append({"tag": tag_str, "notes": row["notes"]})

    return results if results else None
