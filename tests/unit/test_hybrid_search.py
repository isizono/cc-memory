"""ハイブリッド検索（FTS5 + ベクトル + RRF統合）のテスト

_rrf_merge単体テスト + タグ対応の統合テスト。
"""
import hashlib
import os
import tempfile
import pytest
import numpy as np

from src.db import init_database, get_connection
from src.services.search_service import _rrf_merge, RRF_K, RRF_W_FTS, RRF_W_VEC
from src.services import search_service
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
import src.services.embedding_service as emb


EMBEDDING_DIM = 384
DEFAULT_TAGS = ["domain:test"]


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
    """embedding_serverへのHTTPリクエストをモック化"""

    def mock_encode_batch(texts, prefix):
        embeddings = []
        for text in texts:
            prefix_str = "検索文書: " if prefix == "document" else "検索クエリ: "
            seed = int(hashlib.sha256((prefix_str + text).encode()).hexdigest(), 16) % (2**32)
            np.random.seed(seed)
            embeddings.append(np.random.rand(EMBEDDING_DIM).astype(np.float32).tolist())
        return embeddings

    monkeypatch.setattr(emb, '_encode_batch', mock_encode_batch)
    monkeypatch.setattr(emb, '_server_initialized', True)
    monkeypatch.setattr(emb, '_backfill_done', True)
    yield


@pytest.fixture
def disable_embedding(monkeypatch):
    """embeddingサービスを無効化"""
    monkeypatch.setattr(emb, '_server_initialized', False)
    monkeypatch.setattr(emb, '_backfill_done', True)
    monkeypatch.setattr(emb, '_ensure_server_running', lambda: False)


# ========================================
# _rrf_merge 単体テスト
# ========================================


def test_rrf_merge_both_sources():
    """RRF統合: 両方にヒットするアイテムのスコアが加算される"""
    fts = [
        {"type": "topic", "id": 1, "title": "A"},
        {"type": "topic", "id": 2, "title": "B"},
    ]
    vec = [
        {"type": "topic", "id": 2, "title": "B"},
        {"type": "topic", "id": 1, "title": "A"},
    ]

    results = _rrf_merge(fts, vec, limit=10)

    assert len(results) == 2
    # id=1: FTSランク1 + vecランク2, id=2: FTSランク2 + vecランク1
    # 同じスコアになるはず
    score_1 = next(r["score"] for r in results if r["id"] == 1)
    score_2 = next(r["score"] for r in results if r["id"] == 2)
    expected = RRF_W_FTS / (RRF_K + 1) + RRF_W_VEC / (RRF_K + 2)
    assert score_1 == pytest.approx(expected)
    assert score_2 == pytest.approx(expected)


def test_rrf_merge_fts_only():
    """RRF統合: FTSのみの結果"""
    fts = [
        {"type": "topic", "id": 1, "title": "A"},
        {"type": "topic", "id": 2, "title": "B"},
    ]

    results = _rrf_merge(fts, [], limit=10)

    assert len(results) == 2
    # ランク1のスコアが高い
    assert results[0]["id"] == 1
    assert results[0]["score"] == pytest.approx(RRF_W_FTS / (RRF_K + 1))
    assert results[1]["id"] == 2
    assert results[1]["score"] == pytest.approx(RRF_W_FTS / (RRF_K + 2))


def test_rrf_merge_vec_only():
    """RRF統合: ベクトルのみの結果"""
    vec = [
        {"type": "decision", "id": 10, "title": "X"},
        {"type": "task", "id": 20, "title": "Y"},
    ]

    results = _rrf_merge([], vec, limit=10)

    assert len(results) == 2
    assert results[0]["id"] == 10
    assert results[0]["score"] == pytest.approx(RRF_W_VEC / (RRF_K + 1))


def test_rrf_merge_overlap_boosts_score():
    """RRF統合: 両方にヒットするアイテムは片方のみのアイテムより高スコア"""
    fts = [
        {"type": "topic", "id": 1, "title": "Both"},
        {"type": "topic", "id": 2, "title": "FTS only"},
    ]
    vec = [
        {"type": "topic", "id": 1, "title": "Both"},
        {"type": "topic", "id": 3, "title": "Vec only"},
    ]

    results = _rrf_merge(fts, vec, limit=10)

    score_both = next(r["score"] for r in results if r["id"] == 1)
    score_fts = next(r["score"] for r in results if r["id"] == 2)
    score_vec = next(r["score"] for r in results if r["id"] == 3)

    # 両方にヒット > 片方のみ
    assert score_both > score_fts
    assert score_both > score_vec


def test_rrf_merge_empty():
    """RRF統合: 両方空 → 空配列"""
    results = _rrf_merge([], [], limit=10)
    assert results == []


def test_rrf_merge_limit():
    """RRF統合: limit件数に切り詰められる"""
    fts = [{"type": "topic", "id": i, "title": f"T{i}"} for i in range(10)]

    results = _rrf_merge(fts, [], limit=3)

    assert len(results) == 3


# ========================================
# ハイブリッド検索 統合テスト（タグ対応）
# ========================================


def test_hybrid_search_3char_returns_results(temp_db, mock_embedding_model):
    """3文字以上: ハイブリッド検索で結果が返る"""
    add_topic(
        title="ハイブリッド検索テスト用トピック",
        description="FTS5とベクトルの両方で検索される",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="ハイブリッド検索テスト")

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["results"][0]["type"] == "topic"
    assert isinstance(result["results"][0]["score"], float)


def test_hybrid_search_2char_vec_only(temp_db, mock_embedding_model):
    """2文字キーワード + ベクトル有効: ベクトル検索のみで結果が返る"""
    add_topic(
        title="設計ドキュメント",
        description="アーキテクチャ設計の詳細",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="設計")

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["total_count"] == len(result["results"])


def test_hybrid_search_2char_vec_disabled(temp_db, disable_embedding):
    """2文字キーワード + ベクトル無効: KEYWORD_TOO_SHORTエラー"""
    add_topic(
        title="設計ドキュメント",
        description="アーキテクチャ設計の詳細",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="設計")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_hybrid_search_1char_always_error(temp_db, mock_embedding_model):
    """1文字キーワード: ベクトル有効でもエラー"""
    result = search_service.search(keyword="設")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_hybrid_search_3char_vec_disabled_fts_fallback(temp_db, disable_embedding):
    """3文字以上 + ベクトル無効: FTSのみで正常動作（graceful degradation）"""
    add_topic(
        title="認証フローの設計議論",
        description="OAuth2の認証フローについて検討する",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="認証フロー")

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["results"][0]["title"] == "認証フローの設計議論"


def test_hybrid_search_score_is_rrf(temp_db, mock_embedding_model):
    """スコアがRRFスコアで返る（BM25の生値ではない）"""
    add_topic(
        title="RRFスコアテスト用トピック",
        description="スコアの形式を検証する",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="RRFスコアテスト")

    assert "error" not in result
    assert len(result["results"]) >= 1
    score = result["results"][0]["score"]
    # RRFスコアは 1/(k+rank) の範囲内（0 < score <= 1/(k+1)）
    # w_fts=1, w_vec=1, k=60 なので最大 2/(60+1) ≈ 0.0328
    assert 0 < score <= 2 / (RRF_K + 1) + 0.001


def test_hybrid_search_with_tags(temp_db, mock_embedding_model):
    """ハイブリッド検索: タグフィルタ付き"""
    add_topic(
        title="タグ付きハイブリッド検索対象テスト",
        description="これはヒットすべき",
        tags=["domain:test", "scope:hybrid"],
    )
    add_topic(
        title="タグ付きハイブリッド検索対象外テスト",
        description="これはヒットしない",
        tags=["domain:other"],
    )

    result = search_service.search(
        keyword="タグ付きハイブリッド検索",
        tags=["scope:hybrid"],
    )

    assert "error" not in result
    titles = [r["title"] for r in result["results"]]
    assert any("対象テスト" in t for t in titles)
    assert all("対象外テスト" not in t for t in titles)


def test_hybrid_search_cross_type_with_tags(temp_db, mock_embedding_model):
    """ハイブリッド検索: topic/decision/task全てが対象（タグフィルタ付き）"""
    topic = add_topic(
        title="横断ハイブリッドタグテスト用トピック",
        description="横断検索の動作確認",
        tags=DEFAULT_TAGS,
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="横断ハイブリッドタグテスト決定",
        reason="テスト用",
    )
    add_task(
        title="横断ハイブリッドタグテストタスク",
        description="テスト用タスク",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(
        keyword="横断ハイブリッドタグテスト",
        tags=DEFAULT_TAGS,
    )

    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "task" in types_found


def test_hybrid_search_type_filter(temp_db, mock_embedding_model):
    """ハイブリッド検索: type_filterが効く"""
    topic = add_topic(
        title="フィルターハイブリッドテスト用",
        description="テスト用",
        tags=DEFAULT_TAGS,
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="フィルターハイブリッドテスト決定",
        reason="テスト用",
    )

    result = search_service.search(
        keyword="フィルターハイブリッドテスト",
        type_filter="topic",
    )

    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "topic"


def test_hybrid_search_tag_isolation(temp_db, mock_embedding_model):
    """ハイブリッド検索: タグによる分離が保たれる"""
    add_topic(
        title="分離テスト対象トピック",
        description="このタグのみヒットすべき",
        tags=["domain:test"],
    )
    add_topic(
        title="分離テスト他タグトピック",
        description="こちらはヒットしない",
        tags=["domain:other"],
    )

    result = search_service.search(keyword="分離テスト", tags=["domain:test"])

    assert "error" not in result
    for item in result["results"]:
        assert item["title"] != "分離テスト他タグトピック"
