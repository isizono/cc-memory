"""embeddingサーバーのユニットテスト"""
import json
import threading
import urllib.request
import urllib.error

import numpy as np
import pytest

import src.services.embedding_server as srv
from http.server import ThreadingHTTPServer


EMBEDDING_DIM = 384


class MockModel:
    """sentence-transformersモデルのモック"""

    def encode(self, texts):
        return np.array([
            self._encode_single(t) for t in texts
        ])

    def _encode_single(self, text):
        np.random.seed(hash(text) % (2**32))
        return np.random.rand(EMBEDDING_DIM).astype(np.float32)


@pytest.fixture
def test_server(monkeypatch):
    """テスト用サーバーをランダムポートで起動する"""
    monkeypatch.setattr(srv, "_model", MockModel())

    server = ThreadingHTTPServer(("localhost", 0), srv.EmbeddingHandler)
    port = server.server_address[1]
    base_url = f"http://localhost:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield base_url

    server.shutdown()
    server.server_close()


def _get(base_url: str, path: str) -> tuple[int, dict]:
    """GETリクエストを送信してステータスコードとレスポンスボディを返す。"""
    req = urllib.request.Request(f"{base_url}{path}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read())
        return e.code, data


def _post(base_url: str, path: str, body: dict | bytes | None = None) -> tuple[int, dict]:
    """POSTリクエストを送信してステータスコードとレスポンスボディを返す。"""
    if isinstance(body, dict):
        payload = json.dumps(body).encode("utf-8")
    elif isinstance(body, bytes):
        payload = body
    else:
        payload = b""
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read())
        return e.code, data


# ========================================
# /health エンドポイント
# ========================================


def test_health_endpoint(test_server):
    """GET /health → 200 {"status": "ok"}"""
    status, data = _get(test_server, "/health")
    assert status == 200
    assert data == {"status": "ok"}


# ========================================
# /encode エンドポイント: 正常系
# ========================================


def test_encode_document(test_server):
    """POST /encode with prefix="document" → 200 embeddings"""
    status, data = _post(test_server, "/encode", {
        "texts": ["テスト文書"],
        "prefix": "document",
    })
    assert status == 200
    assert "embeddings" in data
    assert len(data["embeddings"]) == 1
    assert len(data["embeddings"][0]) == EMBEDDING_DIM


def test_encode_query(test_server):
    """POST /encode with prefix="query" → 200 embeddings"""
    status, data = _post(test_server, "/encode", {
        "texts": ["テストクエリ"],
        "prefix": "query",
    })
    assert status == 200
    assert "embeddings" in data
    assert len(data["embeddings"]) == 1
    assert len(data["embeddings"][0]) == EMBEDDING_DIM


def test_encode_batch(test_server):
    """POST /encode with multiple texts → 正しい数の結果"""
    texts = ["テスト1", "テスト2", "テスト3"]
    status, data = _post(test_server, "/encode", {
        "texts": texts,
        "prefix": "document",
    })
    assert status == 200
    assert len(data["embeddings"]) == len(texts)
    for emb in data["embeddings"]:
        assert len(emb) == EMBEDDING_DIM


# ========================================
# /encode エンドポイント: エラー系
# ========================================


def test_encode_invalid_json(test_server):
    """不正JSON → 400"""
    status, data = _post(test_server, "/encode", b"not json{{{")
    assert status == 400
    assert "error" in data


def test_encode_empty_texts(test_server):
    """texts=[] → 400"""
    status, data = _post(test_server, "/encode", {
        "texts": [],
        "prefix": "document",
    })
    assert status == 400
    assert "error" in data


def test_encode_invalid_prefix(test_server):
    """prefix="invalid" → 400"""
    status, data = _post(test_server, "/encode", {
        "texts": ["テスト"],
        "prefix": "invalid",
    })
    assert status == 400
    assert "error" in data


# ========================================
# 未知パス
# ========================================


def test_404_unknown_path(test_server):
    """未知パス → 404"""
    status, data = _get(test_server, "/unknown")
    assert status == 404
    assert "error" in data
