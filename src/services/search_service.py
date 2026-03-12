"""FTS5 + ベクトル ハイブリッド検索サービス"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlite_vec import serialize_float32

from src.db import execute_query, get_connection, row_to_dict
from src.services import embedding_service
from src.services.tag_service import (
    get_entity_tags,
    get_effective_tags,
    parse_tag,
)

logger = logging.getLogger(__name__)

SEARCHABLE_TYPES = {'topic', 'decision', 'activity', 'log'}
VALID_TYPES = SEARCHABLE_TYPES | {'material'}

GET_BY_IDS_MAX = 20

TYPE_TO_TABLE = {
    'topic': 'discussion_topics',
    'decision': 'decisions',
    'activity': 'activities',
    'log': 'discussion_logs',
    'material': 'materials',
}

# snippetソースの対応表: type → (テーブル名, カラム名)
SNIPPET_SOURCE = {
    'topic': ('discussion_topics', 'description'),
    'decision': ('decisions', 'decision'),
    'activity': ('activities', 'description'),
    'log': ('discussion_logs', 'content'),
}

SNIPPET_MAX_LEN = 200

# RRFパラメータ
RRF_K = 60
RRF_W_FTS = 1.0
RRF_W_VEC = 1.0

# Recency boost パラメータ
# 半年(182日)で約0.80倍、1年(365日)で約0.66倍
RECENCY_DECAY_RATE = 0.0014


def _escape_fts5_query(keyword: str) -> str:
    """FTS5クエリ用のエスケープ処理。ダブルクォートで囲む。"""
    # ダブルクォート内のダブルクォートは2つ重ねてエスケープ
    escaped = keyword.replace('"', '""')
    return f'"{escaped}"'


def _attach_snippets(results: list[dict]) -> None:
    """検索結果にsnippetを付与する（in-place）。

    typeごとにバッチクエリでsnippetソースを取得し、先頭SNIPPET_MAX_LEN文字を
    snippetフィールドとして付与する。
    logのtitleが空の場合はcontentの先頭50文字をフォールバック表示する。
    """
    # typeごとにグループ化
    by_type: dict[str, list[dict]] = {}
    for item in results:
        by_type.setdefault(item["type"], []).append(item)

    for type_name, items in by_type.items():
        if type_name not in SNIPPET_SOURCE:
            for item in items:
                item["snippet"] = ""
            continue
        table, column = SNIPPET_SOURCE[type_name]
        ids = [item["id"] for item in items]
        placeholders = ",".join("?" * len(ids))
        rows = execute_query(
            f"SELECT id, {column} FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        )
        snippet_map = {r["id"]: (r[column] or "")[:SNIPPET_MAX_LEN] for r in rows}
        for item in items:
            item["snippet"] = snippet_map.get(item["id"], "")

        # log: titleが空の場合にcontentの先頭50文字をフォールバック
        if type_name == "log":
            for item in items:
                if not item["title"]:
                    item["title"] = snippet_map.get(item["id"], "")[:50]


def _resolve_tag_ids_readonly(conn, tag_strings: list[str]) -> list[int]:
    """タグ文字列からtag_idを取得（SELECT ONLY、新規作成しない）。

    存在しないタグが含まれる場合、そのタグは無視される。
    全タグが存在しない場合は空リストを返す。
    エイリアスタグの場合はcanonical側のIDを返す。
    """
    tag_ids = []
    for tag_str in tag_strings:
        ns, name = parse_tag(tag_str)
        row = conn.execute(
            "SELECT id, canonical_id FROM tags WHERE namespace = ? AND name = ?",
            (ns, name)
        ).fetchone()
        if row:
            effective_id = row["canonical_id"] if row["canonical_id"] is not None else row["id"]
            tag_ids.append(effective_id)
    return tag_ids


def _build_tag_filter_cte(tag_ids: list[int]) -> tuple[str, list]:
    """タグフィルタ用のCTE SQLとパラメータを構築する。

    Returns:
        (cte_sql, params) のタプル。cte_sqlは "WITH tag_filtered AS (...)" の形式。
    """
    n_tags = len(tag_ids)
    placeholders = ",".join("?" * n_tags)

    cte_sql = f"""
    WITH tag_filtered AS (
        -- topic (直接タグ)
        SELECT 'topic' AS source_type, topic_id AS source_id FROM (
            SELECT tt.topic_id, tt.tag_id
            FROM topic_tags tt
            WHERE tt.tag_id IN ({placeholders})
        ) GROUP BY topic_id HAVING COUNT(DISTINCT tag_id) = ?

        UNION ALL
        -- activity (直接タグ)
        SELECT 'activity', activity_id FROM (
            SELECT at.activity_id, at.tag_id
            FROM activity_tags at
            WHERE at.tag_id IN ({placeholders})
        ) GROUP BY activity_id HAVING COUNT(DISTINCT tag_id) = ?

        UNION ALL
        -- decision (UNION継承)
        SELECT 'decision', decision_id FROM (
            SELECT d.id AS decision_id, tt.tag_id
            FROM decisions d JOIN topic_tags tt ON tt.topic_id = d.topic_id
            WHERE tt.tag_id IN ({placeholders})
            UNION
            SELECT dt.decision_id, dt.tag_id
            FROM decision_tags dt WHERE dt.tag_id IN ({placeholders})
        ) GROUP BY decision_id HAVING COUNT(DISTINCT tag_id) = ?

        UNION ALL
        -- log (UNION継承)
        SELECT 'log', log_id FROM (
            SELECT dl.id AS log_id, tt.tag_id
            FROM discussion_logs dl JOIN topic_tags tt ON tt.topic_id = dl.topic_id
            WHERE tt.tag_id IN ({placeholders})
            UNION
            SELECT lt.log_id, lt.tag_id
            FROM log_tags lt WHERE lt.tag_id IN ({placeholders})
        ) GROUP BY log_id HAVING COUNT(DISTINCT tag_id) = ?
    )
    """

    # パラメータ: 各セクションに tag_ids + n_tags を渡す
    params: list = []
    # topic
    params.extend(tag_ids)
    params.append(n_tags)
    # activity
    params.extend(tag_ids)
    params.append(n_tags)
    # decision (2つのIN句)
    params.extend(tag_ids)
    params.extend(tag_ids)
    params.append(n_tags)
    # log (2つのIN句)
    params.extend(tag_ids)
    params.extend(tag_ids)
    params.append(n_tags)

    return cte_sql, params


def _fts_search(
    keywords: list[str],
    tag_ids: Optional[list[int]],
    type_filter: Optional[str],
    limit: int,
    keyword_mode: str = "and",
) -> list[dict]:
    """FTS5検索。結果はBM25ランク順のリスト。"""
    # OR時: 3文字以上のキーワードだけでFTS5クエリを組む（2文字はフィルタ除外）
    if keyword_mode == "or":
        fts_keywords = [kw for kw in keywords if len(kw) >= 3]
        if not fts_keywords:
            return []
        escaped_parts = [_escape_fts5_query(kw) for kw in fts_keywords]
        escaped_keyword = " OR ".join(escaped_parts)
    else:
        escaped_parts = [_escape_fts5_query(kw) for kw in keywords]
        escaped_keyword = " AND ".join(escaped_parts)

    if tag_ids:
        cte_sql, cte_params = _build_tag_filter_cte(tag_ids)
        query = f"""
        {cte_sql}
        SELECT
          si.source_type AS type,
          si.source_id AS id,
          si.title
        FROM search_index_fts
        JOIN search_index si ON si.id = search_index_fts.rowid
        JOIN tag_filtered tf ON tf.source_type = si.source_type AND tf.source_id = si.source_id
        WHERE search_index_fts MATCH ?
          AND (? IS NULL OR si.source_type = ?)
        ORDER BY bm25(search_index_fts, 5.0, 1.0)
        LIMIT ?
        """
        params = (*cte_params, escaped_keyword, type_filter, type_filter, limit)
    else:
        query = """
        SELECT
          si.source_type AS type,
          si.source_id AS id,
          si.title
        FROM search_index_fts
        JOIN search_index si ON si.id = search_index_fts.rowid
        WHERE search_index_fts MATCH ?
          AND (? IS NULL OR si.source_type = ?)
        ORDER BY bm25(search_index_fts, 5.0, 1.0)
        LIMIT ?
        """
        params = (escaped_keyword, type_filter, type_filter, limit)

    rows = execute_query(query, params)
    results = []
    for row in rows:
        r = row_to_dict(row)
        results.append({
            "type": r["type"],
            "id": r["id"],
            "title": r["title"],
        })
    return results


def _vector_search(
    keywords: list[str],
    tag_ids: Optional[list[int]],
    type_filter: Optional[str],
    limit: int,
    keyword_mode: str = "and",
) -> Optional[list[dict]]:
    """ベクトル検索。ベクトル検索無効時はNoneを返す。"""
    try:
        if keyword_mode == "or" and len(keywords) > 1:
            # OR時: 各キーワードで個別にベクトル検索し、結果をマージ
            merged: dict[tuple, dict] = {}  # key: (type, id)
            for kw in keywords:
                query_embedding = embedding_service.encode_query(kw)
                if query_embedding is None:
                    continue

                blob = serialize_float32(query_embedding)
                vec_rows = execute_query(
                    "SELECT rowid, distance FROM vec_index WHERE embedding MATCH ? AND k = ?",
                    (blob, limit),
                )
                if not vec_rows:
                    continue

                vec_data = {}
                for row in vec_rows:
                    r = row_to_dict(row)
                    vec_data[r["rowid"]] = r["distance"]

                rowids = list(vec_data.keys())
                rowid_placeholders = ",".join("?" * len(rowids))

                if tag_ids:
                    cte_sql, cte_params = _build_tag_filter_cte(tag_ids)
                    query = f"""
                    {cte_sql}
                    SELECT id, source_type, source_id, title
                    FROM search_index
                    WHERE id IN ({rowid_placeholders})
                      AND (? IS NULL OR source_type = ?)
                      AND EXISTS (
                        SELECT 1 FROM tag_filtered tf
                        WHERE tf.source_type = search_index.source_type
                          AND tf.source_id = search_index.source_id
                      )
                    """
                    params = (*cte_params, *rowids, type_filter, type_filter)
                else:
                    query = f"""
                    SELECT id, source_type, source_id, title
                    FROM search_index
                    WHERE id IN ({rowid_placeholders})
                      AND (? IS NULL OR source_type = ?)
                    """
                    params = (*rowids, type_filter, type_filter)

                filter_rows = execute_query(query, params)
                for row in filter_rows:
                    r = row_to_dict(row)
                    key = (r["source_type"], r["source_id"])
                    distance = vec_data[r["id"]]
                    if key not in merged or distance < merged[key]["distance"]:
                        merged[key] = {
                            "type": r["source_type"],
                            "id": r["source_id"],
                            "title": r["title"],
                            "distance": distance,
                        }

            if not merged:
                return None
            results = list(merged.values())
            results.sort(key=lambda x: x["distance"])
            return results
        else:
            # AND時: 従来通り（スペース結合して1 embedding）
            combined_keyword = " ".join(keywords)
            query_embedding = embedding_service.encode_query(combined_keyword)
            if query_embedding is None:
                return None

            blob = serialize_float32(query_embedding)

            # vec_indexからKNN取得（タグフィルタ不可なので多めに取得）
            # limitはsearch()側でlimit*5に拡大済み
            vec_rows = execute_query(
                "SELECT rowid, distance FROM vec_index WHERE embedding MATCH ? AND k = ?",
                (blob, limit),
            )

            if not vec_rows:
                return []

            vec_data = {}
            for row in vec_rows:
                r = row_to_dict(row)
                vec_data[r["rowid"]] = r["distance"]

            rowids = list(vec_data.keys())
            rowid_placeholders = ",".join("?" * len(rowids))

            if tag_ids:
                cte_sql, cte_params = _build_tag_filter_cte(tag_ids)
                query = f"""
                {cte_sql}
                SELECT id, source_type, source_id, title
                FROM search_index
                WHERE id IN ({rowid_placeholders})
                  AND (? IS NULL OR source_type = ?)
                  AND EXISTS (
                    SELECT 1 FROM tag_filtered tf
                    WHERE tf.source_type = search_index.source_type
                      AND tf.source_id = search_index.source_id
                  )
                """
                params = (*cte_params, *rowids, type_filter, type_filter)
            else:
                query = f"""
                SELECT id, source_type, source_id, title
                FROM search_index
                WHERE id IN ({rowid_placeholders})
                  AND (? IS NULL OR source_type = ?)
                """
                params = (*rowids, type_filter, type_filter)

            filter_rows = execute_query(query, params)

            results = []
            for row in filter_rows:
                r = row_to_dict(row)
                results.append({
                    "type": r["source_type"],
                    "id": r["source_id"],
                    "title": r["title"],
                    "distance": vec_data[r["id"]],
                })

            # distance順でソート（小さいほど類似度が高い）
            results.sort(key=lambda x: x["distance"])
            return results[:limit]

    except (ValueError, RuntimeError, OSError):
        logger.warning("Vector search failed, falling back to FTS-only", exc_info=True)
        return None


def _apply_recency_boost(results: list[dict], now: datetime | None = None) -> None:
    """RRFスコアにrecency boost（時間減衰）を適用する（in-place）。

    recency_factor = 1 / (1 + age_days * RECENCY_DECAY_RATE)
    をスコアに乗算し、スコア降順で再ソートする。
    """
    if not results:
        return

    if now is None:
        now = datetime.now(timezone.utc)

    # typeごとにcreated_atをバッチ取得
    by_type: dict[str, list[dict]] = {}
    for item in results:
        by_type.setdefault(item["type"], []).append(item)

    for type_name, items in by_type.items():
        table = TYPE_TO_TABLE.get(type_name)
        if not table:
            continue
        ids = [item["id"] for item in items]
        placeholders = ",".join("?" * len(ids))
        rows = execute_query(
            f"SELECT id, created_at FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        )
        created_map = {r["id"]: r["created_at"] for r in rows}
        for item in items:
            created_str = created_map.get(item["id"])
            if created_str:
                created = datetime.fromisoformat(created_str).replace(tzinfo=timezone.utc)
                age_days = max(0, (now - created).days)
                recency_factor = 1.0 / (1.0 + age_days * RECENCY_DECAY_RATE)
                item["score"] *= recency_factor

    # スコア降順で再ソート
    results.sort(key=lambda x: x["score"], reverse=True)


def _rrf_merge(
    fts_results: list[dict],
    vec_results: list[dict],
    limit: int,
) -> list[dict]:
    """RRF（Reciprocal Rank Fusion）でFTS5結果とベクトル結果を統合する。"""
    scores: dict[tuple, dict] = {}  # key: (type, id)

    # FTS5結果にRRFスコアを付与（1始まりランク）
    for rank, item in enumerate(fts_results, start=1):
        key = (item["type"], item["id"])
        scores[key] = {
            "type": item["type"],
            "id": item["id"],
            "title": item["title"],
            "score": RRF_W_FTS / (RRF_K + rank),
        }

    # ベクトル結果のRRFスコアを加算（1始まりランク）
    for rank, item in enumerate(vec_results, start=1):
        key = (item["type"], item["id"])
        vec_score = RRF_W_VEC / (RRF_K + rank)
        if key in scores:
            scores[key]["score"] += vec_score
        else:
            scores[key] = {
                "type": item["type"],
                "id": item["id"],
                "title": item["title"],
                "score": vec_score,
            }

    # RRFスコア降順でソートし、上位limit件を返す
    merged = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return merged[:limit]


def search(
    keyword: str | list[str],
    tags: Optional[list[str]] = None,
    type_filter: Optional[str] = None,
    limit: int = 10,
    keyword_mode: str = "and",
) -> dict:
    """
    キーワードで横断検索する。

    FTS5 trigramとベクトル検索のハイブリッド。RRFスコアで統合・ランキング。
    2文字以上のキーワードを指定する。
    配列で複数キーワードを渡すとAND検索（すべてを含む結果のみ返す）。
    keyword_mode="or"でOR検索（いずれかを含む結果を返す）。
    3文字以上: FTS5 + ベクトル検索のハイブリッド。
    2文字: ベクトル検索のみ（ベクトル検索無効時はエラー）。
    tagsでフィルタリング可能（AND結合）。未指定で全件検索。
    詳細情報が必要な場合は get_by_id(type, id) で取得する。

    Args:
        keyword: 検索キーワード（2文字以上）。配列で複数指定時はAND検索
        tags: タグフィルタ（AND条件。未指定=全件検索）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'activity', 'log'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）
        keyword_mode: キーワード結合モード（"and" または "or"。デフォルト "and"）

    Returns:
        検索結果一覧（type, id, title, score, snippet）
        snippetは各typeの対応するソースカラムの先頭200文字。
    """
    # keyword_modeバリデーション
    if keyword_mode not in ("and", "or"):
        return {
            "error": {
                "code": "INVALID_KEYWORD_MODE",
                "message": f"Invalid keyword_mode: {keyword_mode}. Must be 'and' or 'or'"
            }
        }

    # 正規化: str → list[str]
    if isinstance(keyword, str):
        keywords = [keyword.strip()]
    else:
        keywords = [k.strip() for k in keyword]

    # 空配列チェック
    if not keywords:
        return {
            "error": {
                "code": "KEYWORD_TOO_SHORT",
                "message": "keyword must be at least 2 characters"
            }
        }

    # バリデーション: 各要素2文字以上
    for kw in keywords:
        if len(kw) < 2:
            return {
                "error": {
                    "code": "KEYWORD_TOO_SHORT",
                    "message": "keyword must be at least 2 characters"
                }
            }

    if type_filter is not None and type_filter not in SEARCHABLE_TYPES:
        return {
            "error": {
                "code": "INVALID_TYPE_FILTER",
                "message": f"Invalid type_filter: {type_filter}. Must be one of {sorted(SEARCHABLE_TYPES)}"
            }
        }

    limit = max(1, min(limit, 50))

    try:
        # タグフィルタの解決
        tag_ids = None
        if tags:
            conn = get_connection()
            try:
                tag_ids = _resolve_tag_ids_readonly(conn, tags)
                # 指定タグの一部でもDBに存在しない場合、ANDフィルタは必ず空結果
                if len(tag_ids) < len(tags):
                    return {"results": [], "total_count": 0, "search_methods_used": []}
            finally:
                conn.close()

        # RRFで両ソースをマージした後にlimitで切るため、各ソースからlimitより多めに取得する
        fetch_limit = limit * 5

        # 使用された検索手法を追跡
        methods_used: list[str] = []

        # FTS5検索判定
        min_len = min(len(kw) for kw in keywords)
        fts_results = []
        if keyword_mode == "or":
            # OR時: 3文字以上のキーワードが1つでもあればFTSを使う
            if any(len(kw) >= 3 for kw in keywords):
                fts_results = _fts_search(keywords, tag_ids, type_filter, fetch_limit, keyword_mode)
                methods_used.append("fts5")
        else:
            # AND時（現行通り）: 全キーワードが3文字以上の場合のみ
            if min_len >= 3:
                fts_results = _fts_search(keywords, tag_ids, type_filter, fetch_limit, keyword_mode)
                methods_used.append("fts5")

        # ベクトル検索
        vec_results = _vector_search(keywords, tag_ids, type_filter, fetch_limit, keyword_mode)
        if vec_results is not None:
            methods_used.append("vector")

        # 2文字キーワード + ベクトル検索無効 → エラー
        # OR時: 3文字以上が1つでもあればFTSで検索できるのでエラーにしない
        fts_available = (
            any(len(kw) >= 3 for kw in keywords) if keyword_mode == "or"
            else min_len >= 3
        )
        if not fts_available and vec_results is None:
            return {
                "error": {
                    "code": "KEYWORD_TOO_SHORT",
                    "message": "keyword must be at least 3 characters when vector search is unavailable"
                }
            }

        # RRF統合（recency boost前なのでfetch_limitで多めに保持）
        effective_vec = vec_results if vec_results is not None else []
        results = _rrf_merge(fts_results, effective_vec, fetch_limit)

        _apply_recency_boost(results)

        # recency boost後にlimit件に切り詰め
        results = results[:limit]

        _attach_snippets(results)

        return {
            "results": results,
            "total_count": len(results),
            "search_methods_used": methods_used,
        }

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }


def _format_row(type_name: str, data: dict, tags: list[str]) -> dict:
    """typeに応じたレスポンス整形"""
    if type_name == 'topic':
        return {
            "id": data["id"],
            "title": data["title"],
            "description": data["description"],
            "tags": tags,
            "created_at": data["created_at"],
        }
    elif type_name == 'decision':
        return {
            "id": data["id"],
            "topic_id": data["topic_id"],
            "decision": data["decision"],
            "reason": data["reason"],
            "tags": tags,
            "created_at": data["created_at"],
        }
    elif type_name == 'activity':
        return {
            "id": data["id"],
            "title": data["title"],
            "description": data["description"],
            "status": data["status"],
            "tags": tags,
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }
    elif type_name == 'log':
        title = data["title"]
        if not title:
            title = data["content"][:50]
        return {
            "id": data["id"],
            "topic_id": data["topic_id"],
            "title": title,
            "content": data["content"],
            "tags": tags,
            "created_at": data["created_at"],
        }
    elif type_name == 'material':
        return {
            "material_id": data["id"],
            "activity_id": data["activity_id"],
            "title": data["title"],
            "tags": tags,
            "created_at": data["created_at"],
        }
    return data


def get_by_id(type: str, id: int, conn=None) -> dict:
    """
    search結果の詳細情報を取得する。

    searchツールで得られたtype + idの組み合わせを指定して、
    元データの完全な情報を取得する。

    Args:
        type: データ種別（'topic', 'decision', 'activity', 'log', 'material'）
        id: データのID
        conn: 既存のDB接続（省略時は内部で新規作成・クローズ）

    Returns:
        指定した種別に応じた詳細情報
    """
    if type not in VALID_TYPES:
        return {
            "error": {
                "code": "INVALID_TYPE",
                "message": f"Invalid type: {type}. Must be one of {sorted(VALID_TYPES)}"
            }
        }

    table = TYPE_TO_TABLE[type]

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
        if not row:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"{type} with id {id} not found"
                }
            }

        # タグ取得: topic/activityはget_entity_tags、decision/logはget_effective_tags、materialはタグなし
        if type == 'topic':
            tags = get_entity_tags(conn, "topic_tags", "topic_id", id)
        elif type == 'activity':
            tags = get_entity_tags(conn, "activity_tags", "activity_id", id)
        elif type == 'decision':
            tags = get_effective_tags(conn, "decision", id)
        elif type == 'log':
            tags = get_effective_tags(conn, "log", id)
        elif type == 'material':
            tags = []
        else:
            tags = []

        return {"type": type, "data": _format_row(type, row_to_dict(row), tags)}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
    finally:
        if own_conn:
            conn.close()


def get_by_ids(items: list[dict]) -> dict:
    """
    複数のtype+idペアをバッチ取得する。

    Args:
        items: [{type: str, id: int}, ...] のリスト（最大20件）

    Returns:
        {"results": [get_by_idの結果, ...]}
    """
    if not items:
        return {"results": []}

    if len(items) > GET_BY_IDS_MAX:
        return {
            "error": {
                "code": "TOO_MANY_ITEMS",
                "message": f"Maximum {GET_BY_IDS_MAX} items allowed, got {len(items)}"
            }
        }

    conn = get_connection()
    try:
        results = []
        for item in items:
            item_type = item.get("type")
            item_id = item.get("id")
            if item_type is None or item_id is None:
                results.append({
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Each item must have 'type' and 'id' fields"
                    }
                })
                continue
            result = get_by_id(item_type, item_id, conn=conn)
            results.append(result)

        return {"results": results}
    finally:
        conn.close()
