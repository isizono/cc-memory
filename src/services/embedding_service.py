"""Embeddingサービス: ruri-v3-70mモデルによるベクトル生成とvec_index操作"""
import logging
from typing import Optional

from sqlite_vec import serialize_float32

from src.db import execute_query, get_connection

logger = logging.getLogger(__name__)

# 定数
DOC_PREFIX = "検索文書: "
QUERY_PREFIX = "検索クエリ: "
MODEL_NAME = "cl-nagoya/ruri-v3-70m"

# グローバル状態（遅延ロード用）
_model = None
_model_load_failed = False
_backfill_done = False


def _load_model():
    """モデルを遅延ロードする。失敗時はフラグを立てて以降Noneを返す。"""
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if _model_load_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        logger.info(f"Embedding model loaded: {MODEL_NAME}")
        return _model
    except Exception as e:
        _model_load_failed = True
        logger.warning(f"Failed to load embedding model: {e}")
        return None


def _ensure_initialized():
    """モデルのロードとバックフィルを一度だけ実行する。"""
    global _backfill_done
    model = _load_model()
    if model is not None and not _backfill_done:
        backfill_embeddings()
        _backfill_done = True
    return model


def build_embedding_text(*fields: Optional[str]) -> str:
    """embeddingテキストを構築する。None/空文字列は除外してスペース結合。"""
    return " ".join(f for f in fields if f)


def encode_document(text: str) -> Optional[list[float]]:
    """ドキュメント用embedding生成。prefix付き。"""
    model = _ensure_initialized()
    if model is None:
        return None
    prefixed = DOC_PREFIX + text
    embedding = model.encode(prefixed)
    return embedding.tolist()


def encode_query(text: str) -> Optional[list[float]]:
    """クエリ用embedding生成。prefix付き。"""
    model = _ensure_initialized()
    if model is None:
        return None
    prefixed = QUERY_PREFIX + text
    embedding = model.encode(prefixed)
    return embedding.tolist()


def generate_and_store_embedding(source_type: str, source_id: int, text: str) -> None:
    """search_indexからIDを取得してembeddingを生成・保存する。失敗してもraiseしない。"""
    if not text:
        return
    try:
        rows = execute_query(
            "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
        if rows:
            search_index_id = rows[0]["id"]
            embedding = encode_document(text)
            if embedding is not None:
                existing = execute_query(
                    "SELECT rowid FROM vec_index WHERE rowid = ?",
                    (search_index_id,),
                )
                if existing:
                    update_embedding(search_index_id, embedding)
                else:
                    insert_embedding(search_index_id, embedding)
    except Exception as e:
        logger.warning(f"Failed to generate embedding for {source_type} {source_id}: {e}")


def _insert_embedding_row(conn, search_index_id: int, embedding: list[float]) -> None:
    """vec_indexに1行UPSERT（DELETE+INSERT）する（コミットは呼び出し側の責任）。"""
    blob = serialize_float32(embedding)
    conn.execute("DELETE FROM vec_index WHERE rowid = ?", (search_index_id,))
    conn.execute(
        "INSERT INTO vec_index(rowid, embedding) VALUES (?, ?)",
        (search_index_id, blob),
    )


def insert_embedding(search_index_id: int, embedding: list[float]) -> None:
    """vec_indexにembeddingをINSERTする。"""
    conn = get_connection()
    try:
        _insert_embedding_row(conn, search_index_id, embedding)
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to insert embedding for search_index_id={search_index_id}: {e}")
    finally:
        conn.close()


def update_embedding(search_index_id: int, embedding: list[float]) -> None:
    """vec_indexのembeddingを更新する（DELETE+INSERT）。"""
    conn = get_connection()
    try:
        _insert_embedding_row(conn, search_index_id, embedding)
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to update embedding for search_index_id={search_index_id}: {e}")
    finally:
        conn.close()


def backfill_embeddings() -> int:
    """search_indexにあってvec_indexにないレコードのembeddingを一括生成する。

    Returns: 生成したembedding数
    """
    model = _load_model()
    if model is None:
        return 0

    # リソースタイプごとのクエリ（バッチ推論のためにグループ化）
    # テキスト構築はPython側のbuild_embedding_textで統一
    type_queries = {
        "topic": """
            SELECT si.id, dt.title, dt.description
            FROM search_index si
            INNER JOIN discussion_topics dt ON si.source_id = dt.id
            LEFT JOIN vec_index vi ON si.id = vi.rowid
            WHERE si.source_type = 'topic' AND vi.rowid IS NULL
        """,
        "decision": """
            SELECT si.id, d.decision, d.reason
            FROM search_index si
            INNER JOIN decisions d ON si.source_id = d.id
            LEFT JOIN vec_index vi ON si.id = vi.rowid
            WHERE si.source_type = 'decision' AND vi.rowid IS NULL
        """,
        "task": """
            SELECT si.id, t.title, t.description
            FROM search_index si
            INNER JOIN tasks t ON si.source_id = t.id
            LEFT JOIN vec_index vi ON si.id = vi.rowid
            WHERE si.source_type = 'task' AND vi.rowid IS NULL
        """,
    }

    conn = get_connection()
    try:
        total = 0
        for source_type, query in type_queries.items():
            rows = conn.execute(query).fetchall()
            if not rows:
                continue

            ids = []
            texts = []
            for row in rows:
                text = build_embedding_text(row[1], row[2])
                if text:
                    ids.append(row[0])
                    texts.append(DOC_PREFIX + text)

            if not texts:
                continue

            try:
                embeddings = model.encode(texts)
                for search_index_id, embedding in zip(ids, embeddings):
                    _insert_embedding_row(conn, search_index_id, embedding.tolist())
                    total += 1
            except Exception as e:
                logger.warning(f"Failed to backfill {source_type} embeddings: {e}")
                continue

        conn.commit()
        logger.info(f"Backfilled {total} embeddings")
        return total
    except Exception as e:
        logger.warning(f"Embedding backfill failed: {e}")
        return 0
    finally:
        conn.close()
