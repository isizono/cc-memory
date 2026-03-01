"""embeddingサービスのテスト"""
import os
import tempfile
import pytest
import numpy as np

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


@pytest.fixture
def mock_embedding_model(monkeypatch):
    """sentence-transformersのモデルをモック化"""

    class MockModel:
        def encode(self, text):
            # 384次元のダミーベクトルを返す（テキストに基づいて決定論的に生成）
            # sentence-transformersと同様にリスト入力（バッチ）にも対応
            if isinstance(text, list):
                return np.array([
                    self._encode_single(t) for t in text
                ])
            return self._encode_single(text)

        def _encode_single(self, text):
            np.random.seed(hash(text) % (2**32))
            return np.random.rand(EMBEDDING_DIM).astype(np.float32)

    # embedding_serviceのグローバル状態をリセット
    monkeypatch.setattr(emb, '_model', MockModel())
    monkeypatch.setattr(emb, '_model_load_failed', False)
    monkeypatch.setattr(emb, '_backfill_done', True)  # テスト中はバックフィルをスキップ
    yield


@pytest.fixture
def test_subject(temp_db, mock_embedding_model):
    """テスト用サブジェクトを作成する"""
    result = add_subject(name="test-emb-subject", description="Embedding test subject")
    return result["subject_id"]


# ========================================
# encode_document / encode_query のテスト
# ========================================


def test_encode_document_returns_embedding(temp_db, mock_embedding_model):
    """encode_document: 正常にembeddingが返る"""
    result = emb.encode_document("テスト文書")

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM
    assert all(isinstance(v, float) for v in result)


def test_encode_query_returns_embedding(temp_db, mock_embedding_model):
    """encode_query: 正常にembeddingが返る"""
    result = emb.encode_query("テストクエリ")

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM
    assert all(isinstance(v, float) for v in result)


def test_encode_document_has_doc_prefix(temp_db, monkeypatch):
    """encode_document: prefix「検索文書: 」が付与されている"""
    captured_texts = []

    class PrefixCapturingModel:
        def encode(self, text):
            captured_texts.append(text)
            return np.random.rand(EMBEDDING_DIM).astype(np.float32)

    monkeypatch.setattr(emb, '_model', PrefixCapturingModel())
    monkeypatch.setattr(emb, '_model_load_failed', False)
    monkeypatch.setattr(emb, '_backfill_done', True)

    emb.encode_document("テスト文書")

    assert len(captured_texts) == 1
    assert captured_texts[0] == "検索文書: テスト文書"


def test_encode_query_has_query_prefix(temp_db, monkeypatch):
    """encode_query: prefix「検索クエリ: 」が付与されている"""
    captured_texts = []

    class PrefixCapturingModel:
        def encode(self, text):
            captured_texts.append(text)
            return np.random.rand(EMBEDDING_DIM).astype(np.float32)

    monkeypatch.setattr(emb, '_model', PrefixCapturingModel())
    monkeypatch.setattr(emb, '_model_load_failed', False)
    monkeypatch.setattr(emb, '_backfill_done', True)

    emb.encode_query("テストクエリ")

    assert len(captured_texts) == 1
    assert captured_texts[0] == "検索クエリ: テストクエリ"


# ========================================
# graceful degradation のテスト
# ========================================


def test_graceful_degradation_model_load_failure(temp_db, monkeypatch):
    """graceful degradation: モデルロード失敗時にNoneを返す"""
    monkeypatch.setattr(emb, '_model', None)
    monkeypatch.setattr(emb, '_model_load_failed', True)
    monkeypatch.setattr(emb, '_backfill_done', False)

    result = emb.encode_document("テスト")

    assert result is None


# ========================================
# 遅延ロードのテスト
# ========================================


def test_lazy_loading_no_reload(temp_db, monkeypatch):
    """遅延ロード: 2回目の呼び出しでモデルを再ロードしない"""

    class CountingModel:
        def encode(self, text):
            return np.random.rand(EMBEDDING_DIM).astype(np.float32)

    model = CountingModel()
    monkeypatch.setattr(emb, '_model', model)
    monkeypatch.setattr(emb, '_model_load_failed', False)
    monkeypatch.setattr(emb, '_backfill_done', True)

    # 2回呼び出す
    emb.encode_document("テスト1")
    emb.encode_document("テスト2")

    # _modelが既にセットされているので、_load_modelはモデルをそのまま返す（再ロードしない）
    assert emb._model is model


# ========================================
# insert_embedding のテスト
# ========================================


def test_insert_embedding_adds_to_vec_index(temp_db, mock_embedding_model):
    """insert_embedding: vec_indexにレコードが追加される"""
    # まずsearch_indexにレコードを作成するためにtopicを追加
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

    class MockModel:
        def encode(self, text):
            if isinstance(text, list):
                return np.array([
                    self._encode_single(t) for t in text
                ])
            return self._encode_single(text)

        def _encode_single(self, text):
            np.random.seed(42)
            return np.random.rand(EMBEDDING_DIM).astype(np.float32)

    # モデルなしでtopicを作成（embeddingは生成されない）
    monkeypatch.setattr(emb, '_model', None)
    monkeypatch.setattr(emb, '_model_load_failed', True)
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

    # モデルをセットしてバックフィル実行
    monkeypatch.setattr(emb, '_model', MockModel())
    monkeypatch.setattr(emb, '_model_load_failed', False)

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


def test_backfill_noop_when_all_filled(temp_db, mock_embedding_model):
    """backfill: 全レコードが既にある場合は何もしない"""
    # init_database由来の未バックフィルレコードを先に処理しておく
    emb.backfill_embeddings()

    subject = add_subject(name="backfill-noop-test", description="Test")
    subject_id = subject["subject_id"]
    # add_topicがembeddingも生成する（mock_embedding_modelがある）
    add_topic(
        subject_id=subject_id,
        title="全レコード存在テスト",
        description="バックフィル不要のケース",
    )

    # 全レコードにembeddingがある状態でバックフィル実行
    filled = emb.backfill_embeddings()
    assert filled == 0


# ========================================
# embedding失敗時のgraceful degradation テスト
# ========================================


def test_add_topic_succeeds_when_embedding_fails(temp_db, monkeypatch):
    """embedding生成失敗時もadd_topic自体は成功する"""
    monkeypatch.setattr(emb, '_model', None)
    monkeypatch.setattr(emb, '_model_load_failed', True)
    monkeypatch.setattr(emb, '_backfill_done', True)

    subject = add_subject(name="graceful-test", description="Test")
    subject_id = subject["subject_id"]

    topic = add_topic(
        subject_id=subject_id,
        title="Embedding失敗テスト",
        description="モデルロード失敗時もtopic作成は成功する",
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


def test_add_decision_succeeds_when_embedding_fails(temp_db, monkeypatch):
    """embedding生成失敗時もadd_decision自体は成功する"""
    monkeypatch.setattr(emb, '_model', None)
    monkeypatch.setattr(emb, '_model_load_failed', True)
    monkeypatch.setattr(emb, '_backfill_done', True)

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
        reason="モデルロード失敗時もdecision作成は成功する",
    )

    assert "error" not in dec
    assert dec["decision_id"] is not None


def test_add_task_succeeds_when_embedding_fails(temp_db, monkeypatch):
    """embedding生成失敗時もadd_task自体は成功する"""
    monkeypatch.setattr(emb, '_model', None)
    monkeypatch.setattr(emb, '_model_load_failed', True)
    monkeypatch.setattr(emb, '_backfill_done', True)

    subject = add_subject(name="graceful-task-test", description="Test")
    subject_id = subject["subject_id"]

    task = add_task(
        subject_id=subject_id,
        title="Embedding失敗テストタスク",
        description="モデルロード失敗時もtask作成は成功する",
    )

    assert "error" not in task
    assert task["task_id"] is not None
