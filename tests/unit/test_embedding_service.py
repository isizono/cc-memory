"""embeddingサービス（HTTPクライアント）のテスト"""
import os
import tempfile
import urllib.error

import pytest

from src.db import init_database, get_connection, execute_query
from src.services.subject_service import add_subject
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
import src.services.embedding_service as emb


EMBEDDING_DIM = 384


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        yield db_path
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


def _make_dummy_embeddings(texts: list[str]) -> list[list[float]]:
    """テスト用のダミーembeddingリストを返す。"""
    return [[0.1] * EMBEDDING_DIM for _ in texts]


@pytest.fixture
def mock_request_encode(monkeypatch):
    """_request_encodeをモック化して正常動作を返す"""
    def fake_request_encode(texts, prefix):
        return _make_dummy_embeddings(texts)

    monkeypatch.setattr(emb, '_request_encode', fake_request_encode)
    monkeypatch.setattr(emb, '_backfill_done', True)
    yield


@pytest.fixture
def mock_request_encode_none(monkeypatch):
    """_request_encodeをモック化してNoneを返す（サーバー不通シミュレーション）"""
    def fake_request_encode(texts, prefix):
        return None

    monkeypatch.setattr(emb, '_request_encode', fake_request_encode)
    monkeypatch.setattr(emb, '_backfill_done', True)
    yield


@pytest.fixture
def test_subject(temp_db, mock_request_encode):
    """テスト用サブジェクトを作成する"""
    result = add_subject(name="test-emb-subject", description="Embedding test subject")
    return result["subject_id"]


# ========================================
# _request_encode のテスト
# ========================================


def test_request_encode_success(monkeypatch):
    """_request_encode: 正常レスポンスのパース"""
    import json
    import io

    class FakeResponse:
        status = 200
        def __init__(self):
            embeddings = [[0.1] * EMBEDDING_DIM]
            self._data = json.dumps({"embeddings": embeddings}).encode("utf-8")
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(emb.urllib.request, 'urlopen', fake_urlopen)

    result = emb._request_encode(["テスト"], "document")
    assert result is not None
    assert len(result) == 1
    assert len(result[0]) == EMBEDDING_DIM


def test_request_encode_connection_refused(monkeypatch):
    """_request_encode: 接続拒否時にNone"""
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(emb.urllib.request, 'urlopen', fake_urlopen)
    # _ensure_serverもモックして即失敗させる
    monkeypatch.setattr(emb, '_ensure_server', lambda: False)

    result = emb._request_encode(["テスト"], "document")
    assert result is None


def test_request_encode_timeout(monkeypatch):
    """_request_encode: タイムアウト時にNone"""
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(emb.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(emb, '_ensure_server', lambda: False)

    result = emb._request_encode(["テスト"], "document")
    assert result is None


# ========================================
# _ensure_server のテスト
# ========================================


def test_ensure_server_already_running(monkeypatch):
    """_ensure_server: health OK → Popen呼ばない"""
    class FakeResponse:
        status = 200
        def read(self):
            return b'{"status": "ok"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(emb.urllib.request, 'urlopen', fake_urlopen)

    popen_called = []
    original_popen = emb.subprocess.Popen

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_called.append(True)

    monkeypatch.setattr(emb.subprocess, 'Popen', FakePopen)

    result = emb._ensure_server()
    assert result is True
    assert len(popen_called) == 0


def test_ensure_server_starts_server(monkeypatch):
    """_ensure_server: health失敗 → Popen呼ぶ"""
    call_count = [0]

    class FakeResponse:
        status = 200
        def read(self):
            return b'{"status": "ok"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # 最初のhealthチェックは失敗
            raise urllib.error.URLError("Connection refused")
        # 起動後のhealthチェックは成功
        return FakeResponse()

    monkeypatch.setattr(emb.urllib.request, 'urlopen', fake_urlopen)

    popen_called = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            popen_called.append(True)

    monkeypatch.setattr(emb.subprocess, 'Popen', FakePopen)
    # sleepを高速化
    monkeypatch.setattr(emb.time, 'sleep', lambda x: None)

    result = emb._ensure_server()
    assert result is True
    assert len(popen_called) == 1


# ========================================
# encode_document / encode_query のテスト
# ========================================


def test_encode_document_returns_embedding(temp_db, mock_request_encode):
    """encode_document: _request_encodeモックで正常動作"""
    result = emb.encode_document("テスト文書")

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM


def test_encode_query_returns_embedding(temp_db, mock_request_encode):
    """encode_query: _request_encodeモックで正常動作"""
    result = emb.encode_query("テストクエリ")

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM


def test_encode_document_server_unavailable(temp_db, mock_request_encode_none):
    """encode_document: _request_encodeがNone → None返却"""
    result = emb.encode_document("テスト文書")
    assert result is None


def test_encode_query_server_unavailable(temp_db, mock_request_encode_none):
    """encode_query: _request_encodeがNone → None返却"""
    result = emb.encode_query("テストクエリ")
    assert result is None


# ========================================
# encode_document / encode_query がprefix引数を正しく渡すテスト
# ========================================


def test_encode_document_passes_document_prefix(temp_db, monkeypatch):
    """encode_document: prefix="document"で_request_encodeを呼ぶ"""
    captured = []

    def capture_request_encode(texts, prefix):
        captured.append({"texts": texts, "prefix": prefix})
        return _make_dummy_embeddings(texts)

    monkeypatch.setattr(emb, '_request_encode', capture_request_encode)
    monkeypatch.setattr(emb, '_backfill_done', True)

    emb.encode_document("テスト文書")

    assert len(captured) == 1
    assert captured[0]["prefix"] == "document"
    assert captured[0]["texts"] == ["テスト文書"]


def test_encode_query_passes_query_prefix(temp_db, monkeypatch):
    """encode_query: prefix="query"で_request_encodeを呼ぶ"""
    captured = []

    def capture_request_encode(texts, prefix):
        captured.append({"texts": texts, "prefix": prefix})
        return _make_dummy_embeddings(texts)

    monkeypatch.setattr(emb, '_request_encode', capture_request_encode)
    monkeypatch.setattr(emb, '_backfill_done', True)

    emb.encode_query("テストクエリ")

    assert len(captured) == 1
    assert captured[0]["prefix"] == "query"
    assert captured[0]["texts"] == ["テストクエリ"]


# ========================================
# insert_embedding のテスト
# ========================================


def test_insert_embedding_adds_to_vec_index(temp_db, mock_request_encode):
    """insert_embedding: vec_indexにレコードが追加される"""
    subject = add_subject(name="insert-emb-test", description="Test")
    subject_id = subject["subject_id"]
    topic = add_topic(
        subject_id=subject_id,
        title="テストトピック",
        description="テスト説明",
    )

    # search_indexのIDを取得
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("topic", topic["topic_id"]),
    )
    assert len(rows) > 0
    search_index_id = rows[0]["id"]

    # vec_indexにembeddingが存在するか確認
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# ========================================
# add系関数の統合テスト
# ========================================


def test_add_topic_creates_embedding(test_subject):
    """add_topic後にvec_indexにembeddingが存在する"""
    topic = add_topic(
        subject_id=test_subject,
        title="Embedding統合テストトピック",
        description="vec_indexへの格納を検証する",
    )

    assert "error" not in topic

    # search_indexのIDを取得
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("topic", topic["topic_id"]),
    )
    assert len(rows) > 0
    search_index_id = rows[0]["id"]

    # vec_indexにembeddingが存在する
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_add_decision_creates_embedding(test_subject):
    """add_decision後にvec_indexにembeddingが存在する"""
    topic = add_topic(
        subject_id=test_subject,
        title="テスト用トピック",
        description="テスト",
    )
    dec = add_decision(
        topic_id=topic["topic_id"],
        decision="Embedding統合テスト決定",
        reason="vec_indexへの格納検証",
    )

    assert "error" not in dec

    # search_indexのIDを取得
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("decision", dec["decision_id"]),
    )
    assert len(rows) > 0
    search_index_id = rows[0]["id"]

    # vec_indexにembeddingが存在する
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_add_task_creates_embedding(test_subject):
    """add_task後にvec_indexにembeddingが存在する"""
    task = add_task(
        subject_id=test_subject,
        title="Embedding統合テストタスク",
        description="vec_indexへの格納を検証する",
    )

    assert "error" not in task

    # search_indexのIDを取得
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("task", task["task_id"]),
    )
    assert len(rows) > 0
    search_index_id = rows[0]["id"]

    # vec_indexにembeddingが存在する
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# ========================================
# backfill のテスト
# ========================================


def test_backfill_fills_missing_embeddings(temp_db, monkeypatch):
    """backfill: search_indexにあってvec_indexにないレコードが埋められる"""
    # _request_encodeがNoneを返す（サーバー不通）でtopicを作成（embeddingは生成されない）
    monkeypatch.setattr(emb, '_request_encode', lambda texts, prefix: None)
    monkeypatch.setattr(emb, '_backfill_done', True)

    subject = add_subject(name="backfill-test", description="Test")
    subject_id = subject["subject_id"]
    topic = add_topic(
        subject_id=subject_id,
        title="バックフィルテストトピック",
        description="バックフィルの動作を検証する",
    )

    # この時点ではvec_indexにembeddingがないことを確認
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("topic", topic["topic_id"]),
    )
    assert len(rows) > 0
    search_index_id = rows[0]["id"]

    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 0
    finally:
        conn.close()

    # _request_encodeを正常動作に切り替え、_ensure_serverもモック
    monkeypatch.setattr(emb, '_request_encode', lambda texts, prefix: _make_dummy_embeddings(texts))
    monkeypatch.setattr(emb, '_ensure_server', lambda: True)

    filled = emb.backfill_embeddings()

    # init_databaseで作成されたfirst_topicも含まれうるので、1以上であればOK
    assert filled >= 1

    # vec_indexにembeddingが追加されている
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_backfill_noop_when_all_filled(temp_db, mock_request_encode):
    """backfill: 全レコードが既にある場合は何もしない"""
    # _ensure_serverもモック
    import src.services.embedding_service as emb_mod
    original_ensure = emb_mod._ensure_server
    emb_mod._ensure_server = lambda: True

    try:
        # init_database由来の未バックフィルレコードを先に処理しておく
        emb.backfill_embeddings()

        subject = add_subject(name="backfill-noop-test", description="Test")
        subject_id = subject["subject_id"]
        add_topic(
            subject_id=subject_id,
            title="全レコード存在テスト",
            description="バックフィル不要のケース",
        )

        # 全レコードにembeddingがある状態でバックフィル実行
        filled = emb.backfill_embeddings()
        assert filled == 0
    finally:
        emb_mod._ensure_server = original_ensure


# ========================================
# embedding失敗時のgraceful degradation テスト
# ========================================


def test_add_topic_succeeds_when_embedding_fails(temp_db, mock_request_encode_none):
    """embedding生成失敗時もadd_topic自体は成功する"""
    subject = add_subject(name="graceful-test", description="Test")
    subject_id = subject["subject_id"]

    topic = add_topic(
        subject_id=subject_id,
        title="Embedding失敗テスト",
        description="サーバー不通時もtopic作成は成功する",
    )

    assert "error" not in topic
    assert topic["topic_id"] is not None
    assert topic["title"] == "Embedding失敗テスト"

    # vec_indexにはembeddingがない
    rows = execute_query(
        "SELECT id FROM search_index WHERE source_type = ? AND source_id = ?",
        ("topic", topic["topic_id"]),
    )
    if rows:
        search_index_id = rows[0]["id"]
        conn = get_connection()
        try:
            cursor = conn.execute("SELECT count(*) FROM vec_index WHERE rowid = ?", (search_index_id,))
            count = cursor.fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_add_decision_succeeds_when_embedding_fails(temp_db, mock_request_encode_none):
    """embedding生成失敗時もadd_decision自体は成功する"""
    subject = add_subject(name="graceful-dec-test", description="Test")
    subject_id = subject["subject_id"]
    topic = add_topic(
        subject_id=subject_id,
        title="テスト用トピック",
        description="テスト",
    )

    dec = add_decision(
        topic_id=topic["topic_id"],
        decision="Embedding失敗テスト決定",
        reason="サーバー不通時もdecision作成は成功する",
    )

    assert "error" not in dec
    assert dec["decision_id"] is not None


def test_add_task_succeeds_when_embedding_fails(temp_db, mock_request_encode_none):
    """embedding生成失敗時もadd_task自体は成功する"""
    subject = add_subject(name="graceful-task-test", description="Test")
    subject_id = subject["subject_id"]

    task = add_task(
        subject_id=subject_id,
        title="Embedding失敗テストタスク",
        description="サーバー不通時もtask作成は成功する",
    )

    assert "error" not in task
    assert task["task_id"] is not None
