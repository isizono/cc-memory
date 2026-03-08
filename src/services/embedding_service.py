"""Embeddingサービス: embedding_serverへのHTTPクライアント + vec_index操作"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from sqlite_vec import serialize_float32

from src.db import execute_query, get_connection

logger = logging.getLogger(__name__)

# サーバー接続設定
PORT = 52836
SERVER_URL = f"http://localhost:{PORT}"

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)

# グローバル状態
_server_initialized = False
_backfill_done = False


def _is_server_running() -> bool:
    """GET /health でサーバーの生存確認を行う。"""
    try:
        req = urllib.request.Request(f"{SERVER_URL}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_server() -> bool:
    """embedding_server.pyをdetachedプロセスとして起動する。成功でTrue。"""
    server_path = os.path.join(os.path.dirname(__file__), "embedding_server.py")
    try:
        subprocess.Popen(
            [sys.executable, server_path],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=_PROJECT_ROOT,
        )
    except OSError as e:
        logger.warning(f"Failed to start embedding server: {e}")
        return False
    logger.info("Embedding server process started")
    return True


def _ensure_server_running() -> bool:
    """ヘルスチェック→起動→待機のフロー。成功でTrue、タイムアウトでFalse。"""
    if _is_server_running():
        return True
    if not _start_server():
        return False
    # 最大30秒待機（0.5秒間隔 × 60回）
    for _ in range(60):
        time.sleep(0.5)
        if _is_server_running():
            logger.info("Embedding server is ready")
            return True
    logger.warning("Embedding server failed to start within 30 seconds")
    return False


def _encode_batch(texts: list[str], prefix: str) -> Optional[list[list[float]]]:
    """POST /encode にバッチリクエストを送信する。

    Args:
        texts: エンコードするテキストのリスト（prefix付与はサーバー側で行う）
        prefix: "document" or "query"

    Returns:
        embeddingのリスト、失敗時はNone
    """
    try:
        data = json.dumps({"texts": texts, "prefix": prefix}).encode("utf-8")
        req = urllib.request.Request(
            f"{SERVER_URL}/encode",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["embeddings"]
    except Exception as e:
        logger.warning(f"encode_batch failed: {e}")
        global _server_initialized
        _server_initialized = False
        return None


def _ensure_initialized() -> bool:
    """サーバー起動確認とバックフィルを一度だけ実行する。"""
    global _server_initialized, _backfill_done
    if _server_initialized:
        return True
    running = _ensure_server_running()
    if running:
        _server_initialized = True
        if not _backfill_done:
            backfill_embeddings()
            _backfill_done = True
    return running


def build_embedding_text(*fields: Optional[str]) -> str:
    """embeddingテキストを構築する。None/空文字列は除外してスペース結合。"""
    return " ".join(f for f in fields if f)


def encode_document(text: str) -> Optional[list[float]]:
    """ドキュメント用embedding生成。"""
    if not _ensure_initialized():
        return None
    result = _encode_batch([text], "document")
    if result is None:
        return None
    return result[0]


def encode_query(text: str) -> Optional[list[float]]:
    """クエリ用embedding生成。"""
    if not _ensure_initialized():
        return None
    result = _encode_batch([text], "query")
    if result is None:
        return None
    return result[0]


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
    if not _is_server_running():
        return 0

    # リソースタイプごとのクエリ（バッチ推論のためにグループ化）
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
        "log": """
            SELECT si.id, dl.title, dl.content
            FROM search_index si
            INNER JOIN discussion_logs dl ON si.source_id = dl.id
            LEFT JOIN vec_index vi ON si.id = vi.rowid
            WHERE si.source_type = 'log' AND vi.rowid IS NULL
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
                    texts.append(text)  # prefix付与はサーバー側で行う

            if not texts:
                continue

            try:
                embeddings = _encode_batch(texts, "document")
                if embeddings is None:
                    continue
                for search_index_id, embedding in zip(ids, embeddings):
                    _insert_embedding_row(conn, search_index_id, embedding)
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
