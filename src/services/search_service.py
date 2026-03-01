"""FTS5 + ベクトル ハイブリッド検索サービス"""
import logging
from typing import Optional

from sqlite_vec import serialize_float32

from src.db import execute_query, row_to_dict
from src.services import embedding_service

logger = logging.getLogger(__name__)

VALID_TYPES = {'topic', 'decision', 'task'}

TYPE_TO_TABLE = {
    'topic': 'discussion_topics',
    'decision': 'decisions',
    'task': 'tasks',
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


def _fts_search(
    keyword: str,
    subject_id: int,
    type_filter: Optional[str],
    limit: int,
) -> list[dict]:
    """FTS5検索。結果はBM25ランク順のリスト。"""
    escaped_keyword = _escape_fts5_query(keyword)
    rows = execute_query(
        """
        SELECT
          si.source_type AS type,
          si.source_id AS id,
          si.title
        FROM search_index_fts
        JOIN search_index si ON si.id = search_index_fts.rowid
        WHERE search_index_fts MATCH ?
          AND si.subject_id = ?
          AND (? IS NULL OR si.source_type = ?)
        -- bm25() returns negative values; ascending = most relevant first
        ORDER BY bm25(search_index_fts, 5.0, 1.0)
        LIMIT ?
        """,
        (escaped_keyword, subject_id, type_filter, type_filter, limit),
    )
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
    subject_id: int,
    type_filter: Optional[str],
    limit: int,
) -> Optional[list[dict]]:
    """ベクトル検索。ベクトル検索無効時はNoneを返す。"""
    try:
        query_embedding = embedding_service.encode_query(keyword)
        if query_embedding is None:
            return None

        blob = serialize_float32(query_embedding)

        # vec_indexからKNN取得
        vec_rows = execute_query(
            "SELECT rowid, distance FROM vec_index WHERE embedding MATCH ? AND k = ?",
            (blob, limit),
        )

        if not vec_rows:
            return []

        # search_indexと突き合わせてsubject_id/type_filterでフィルタリング
        vec_data = {}
        for row in vec_rows:
            r = row_to_dict(row)
            vec_data[r["rowid"]] = r["distance"]

        rowids = list(vec_data.keys())
        placeholders = ",".join("?" * len(rowids))

        filter_rows = execute_query(
            f"""
            SELECT id, source_type, source_id, title
            FROM search_index
            WHERE id IN ({placeholders})
              AND subject_id = ?
              AND (? IS NULL OR source_type = ?)
            """,
            (*rowids, subject_id, type_filter, type_filter),
        )

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
        return results

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
    subject_id: int,
    keyword: str,
    type_filter: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """
    サブジェクト内をキーワードで横断検索する。

    FTS5 trigramとベクトル検索のハイブリッド。RRFスコアで統合・ランキング。
    2文字以上のキーワードを指定する。
    3文字以上: FTS5 + ベクトル検索のハイブリッド。
    2文字: ベクトル検索のみ（ベクトル検索無効時はエラー）。
    詳細情報が必要な場合は get_by_id(type, id) で取得する。

    Args:
        subject_id: サブジェクトID
        keyword: 検索キーワード（2文字以上）
        type_filter: 検索対象の絞り込み（'topic', 'decision', 'task'。未指定で全種類）
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
        # RRFで両ソースをマージした後にlimitで切るため、各ソースからlimitより多めに取得する
        fetch_limit = limit * 5

        # FTS5検索: 3文字以上の場合のみ
        fts_results = []
        if len(keyword) >= 3:
            fts_results = _fts_search(keyword, subject_id, type_filter, fetch_limit)

        # ベクトル検索
        vec_results = _vector_search(keyword, subject_id, type_filter, fetch_limit)

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

        return {"results": results, "total_count": len(results)}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }


def _format_row(type_name: str, data: dict) -> dict:
    """typeに応じたレスポンス整形"""
    if type_name == 'topic':
        return {
            "id": data["id"],
            "subject_id": data["subject_id"],
            "title": data["title"],
            "description": data["description"],
            "parent_topic_id": data["parent_topic_id"],
            "created_at": data["created_at"],
        }
    elif type_name == 'decision':
        return {
            "id": data["id"],
            "topic_id": data["topic_id"],
            "decision": data["decision"],
            "reason": data["reason"],
            "created_at": data["created_at"],
        }
    elif type_name == 'task':
        return {
            "id": data["id"],
            "subject_id": data["subject_id"],
            "title": data["title"],
            "description": data["description"],
            "status": data["status"],
            "topic_id": data["topic_id"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }
    return data


def get_by_id(type: str, id: int) -> dict:
    """
    search結果の詳細情報を取得する。

    searchツールで得られたtype + idの組み合わせを指定して、
    元データの完全な情報を取得する。

    Args:
        type: データ種別（'topic', 'decision', 'task'）
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

    try:
        rows = execute_query(f"SELECT * FROM {table} WHERE id = ?", (id,))
        if not rows:
            return {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"{type} with id {id} not found"
                }
            }

        return {"type": type, "data": _format_row(type, row_to_dict(rows[0]))}

    except Exception as e:
        return {
            "error": {
                "code": "DATABASE_ERROR",
                "message": str(e),
            }
        }
