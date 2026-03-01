"""Embeddingサービス: HTTPクライアント経由でembedding生成 + vec_index操作"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

from sqlite_vec import serialize_float32

from src.db import execute_query, get_connection

logger = logging.getLogger(__name__)

# サーバー接続定数
EMBEDDING_SERVER_HOST = "localhost"
EMBEDDING_SERVER_PORT = 52836
EMBEDDING_SERVER_URL = f"http://{EMBEDDING_SERVER_HOST}:{EMBEDDING_SERVER_PORT}"

# グローバル状態
_backfill_done = False


def _ensure_server() -> bool:
    """embeddingサーバーの起動を保証する。"""
    # 1. healthチェック
    try:
        req = urllib.request.Request(f"{EMBEDDING_SERVER_URL}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    # 2. サーバー起動
    logger.info("Starting embedding server...")
    log_dir = os.path.expanduser("~/.cache/cc-memory")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "embedding-server.log")

    server_module = os.path.join(os.path.dirname(__file__), "embedding_server.py")
    log_file = open(log_path, "a")
    try:
        subprocess.Popen(
            [sys.executable, server_module],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    finally:
        log_file.close()

    # 3. 起動待ち（最大30秒）
    for _ in range(60):
        time.sleep(0.5)
        try:
            req = urllib.request.Request(f"{EMBEDDING_SERVER_URL}/health")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    logger.info("Embedding server is ready")
                    return True
        except Exception:
            continue

    logger.warning("Embedding server startup timed out (30s)")
    return False


def _request_encode(texts: list[str], prefix: str) -> Optional[list[list[float]]]:
    """embeddingサーバーにencode要求を送信する。"""
    payload = json.dumps({"texts": texts, "prefix": prefix}).encode("utf-8")
    req = urllib.request.Request(
        f"{EMBEDDING_SERVER_URL}/encode",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["embeddings"]
        except urllib.error.URLError as e:
            if attempt == 0:
                logger.info(f"Encode request failed, retrying: {e}")
                if _ensure_server():
                    continue
            logger.info(f"Encode request failed after retry: {e}")
            return None
        except Exception as e:
            logger.warning(f"Encode response parse error: {e}")
            return None
    return None


def build_embedding_text(*fields: Optional[str]) -> str:
    """embeddingテキストを構築する。None/空文字列は除外してスペース結合。"""
    return " ".join(f for f in fields if f)


def encode_document(text: str) -> Optional[list[float]]:
    """ドキュメント用embedding生成。"""
    global _backfill_done
    if not _backfill_done:
        _backfill_done = True
        backfill_embeddings()

    result = _request_encode([text], "document")
    if result is not None:
        return result[0]
    return None


def encode_query(text: str) -> Optional[list[float]]:
    """クエリ用embedding生成。"""
    result = _request_encode([text], "query")
    if result is not None:
        return result[0]
    return None


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
    if not _ensure_server():
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
                    texts.append(text)

            if not texts:
                continue

            try:
                embeddings = _request_encode(texts, "document")
                if embeddings is None:
                    logger.warning(f"Failed to backfill {source_type} embeddings: server returned None")
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
