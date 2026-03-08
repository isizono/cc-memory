"""FTS5 + ベクトル ハイブリッド検索サービス"""
import logging
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

VALID_TYPES = {'topic', 'decision', 'task', 'log'}

TYPE_TO_TABLE = {
    'topic': 'discussion_topics',
    'decision': 'decisions',
    'task': 'tasks',
    'log': 'discussion_logs',
}

# RRFパラメータ
RRF_K = 60
RRF_W_FTS = 1.0
RRF_W_VEC = 1.0


def _escape_fts5_query(keyword: str) -> str:
    """FTS5クエリ用のエスケープ処理。ダブルクォートで囲む。"""
    # ダブルクォート内のダブルクォートは2つ重ねてエスケープ
    escaped = keyword.replace('"', '""')
    return f'"{escaped}"'


def _resolve_tag_ids_readonly(conn, tag_strings: list[str]) -> list[int]:
    """タグ文字列からtag_idを取得（SELECT ONLY、新規作成しない）。

    存在しないタグが含まれる場合、そのタグは無視される。
    全タグが存在しない場合は空リストを返す。
    """
    tag_ids = []
    for tag_str in tag_strings:
        ns, name = parse_tag(tag_str)
        row = conn.execute(
            "SELECT id FROM tags WHERE namespace = ? AND name = ?",
            (ns, name)
        ).fetchone()
        if row:
            tag_ids.append(row[0])
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
        -- task (直接タグ)
        SELECT 'task', task_id FROM (
            SELECT tkt.task_id, tkt.tag_id
            FROM task_tags tkt
            WHERE tkt.tag_id IN ({placeholders})
        ) GROUP BY task_id HAVING COUNT(DISTINCT tag_id) = ?

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
    # task
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
    keyword: str,
    tag_ids: Optional[list[int]],
    type_filter: Optional[str],
    limit: int,
) -> list[dict]:
    """FTS5検索。結果はBM25ランク順のリスト。"""
    escaped_keyword = _escape_fts5_query(keyword)

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
    keyword: str,
    tag_ids: Optional[list[int]],
    type_filter: Optional[str],
    limit: int,
) -> Optional[list[dict]]:
    """ベクトル検索。ベクトル検索無効時はNoneを返す。"""
    try:
        query_embedding = embedding_service.encode_query(keyword)
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
    keyword: str,
    tags: Optional[list[str]] = None,
    type_filter: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    キーワードで横断検索する。

    FTS5 trigramとベクトル検索のハイブリッド。RRFスコアで統合・ランキング。
    2文字以上のキーワードを指定する。
    3文字以上: FTS5 + ベクトル検索のハイブリッド。
    2文字: ベクトル検索のみ（ベクトル検索無効時はエラー）。
    tagsでフィルタリング可能（AND結合）。未指定で全件検索。
    詳細情報が必要な場合は get_by_id(type, id) で取得する。

    Args:
        keyword: 検索キーワード（2文字以上）
        tags: タグフィルタ（AND条件。未指定=全件検索）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'task', 'log'。未指定で全種類）
        limit: 取得件数上限（デフォルト10件、最大50件）

    Returns:
        検索結果一覧（type, id, title, score）
    """
    # バリデーション
    keyword = keyword.strip()
    if len(keyword) < 2:
        return {
            "error": {
                "code": "KEYWORD_TOO_SHORT",
                "message": "keyword must be at least 2 characters"
            }
        }

    if type_filter is not None and type_filter not in VALID_TYPES:
        return {
            "error": {
                "code": "INVALID_TYPE_FILTER",
                "message": f"Invalid type_filter: {type_filter}. Must be one of {sorted(VALID_TYPES)}"
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
                    return {"results": [], "total_count": 0}
            finally:
                conn.close()

        # RRFで両ソースをマージした後にlimitで切るため、各ソースからlimitより多めに取得する
        fetch_limit = limit * 5

        # FTS5検索: 3文字以上の場合のみ
        fts_results = []
        if len(keyword) >= 3:
            fts_results = _fts_search(keyword, tag_ids, type_filter, fetch_limit)

        # ベクトル検索
        vec_results = _vector_search(keyword, tag_ids, type_filter, fetch_limit)

        # 2文字キーワード + ベクトル検索無効 → エラー
        if len(keyword) < 3 and vec_results is None:
            return {
                "error": {
                    "code": "KEYWORD_TOO_SHORT",
                    "message": "keyword must be at least 3 characters when vector search is unavailable"
                }
            }

        # RRF統合
        effective_vec = vec_results if vec_results is not None else []
        results = _rrf_merge(fts_results, effective_vec, limit)

        # titleが空のlogアイテムにcontentの先頭50文字をフォールバック表示
        for item in results:
            if item["type"] == "log" and not item["title"]:
                rows = execute_query(
                    "SELECT content FROM discussion_logs WHERE id = ?",
                    (item["id"],)
                )
                if rows:
                    content = rows[0]["content"]
                    item["title"] = content[:50]

        return {"results": results, "total_count": len(results)}

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
    elif type_name == 'task':
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
    return data


def get_by_id(type: str, id: int) -> dict:
    """
    search結果の詳細情報を取得する。

    searchツールで得られたtype + idの組み合わせを指定して、
    元データの完全な情報を取得する。

    Args:
        type: データ種別（'topic', 'decision', 'task', 'log'）
        id: データのID

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

        # タグ取得: topic/taskはget_entity_tags、decision/logはget_effective_tags
        if type == 'topic':
            tags = get_entity_tags(conn, "topic_tags", "topic_id", id)
        elif type == 'task':
            tags = get_entity_tags(conn, "task_tags", "task_id", id)
        elif type == 'decision':
            tags = get_effective_tags(conn, "decision", id)
        elif type == 'log':
            tags = get_effective_tags(conn, "log", id)
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
        conn.close()
