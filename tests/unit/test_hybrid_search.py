"""ハイブリッド検索（FTS5 + ベクトル + RRF統合）のテスト"""
import hashlib
import os
import tempfile
import pytest
import numpy as np

from src.db import init_database, get_connection
from src.services.subject_service import add_subject
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
from src.services.search_service import search, _rrf_merge, RRF_K, RRF_W_FTS, RRF_W_VEC
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


def _make_deterministic_embedding(text: str) -> list[float]:
    """テキストから決定論的にembeddingを生成する。"""
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
    np.random.seed(seed)
    return np.random.rand(EMBEDDING_DIM).astype(np.float32).tolist()


@pytest.fixture
def mock_embedding_model(monkeypatch):
    """_request_encodeをモック化して決定論的なembeddingを返す"""

    def fake_request_encode(texts, prefix):
        return [_make_deterministic_embedding(t) for t in texts]

    monkeypatch.setattr(emb, '_request_encode', fake_request_encode)
    monkeypatch.setattr(emb, '_backfill_done', True)
    yield


@pytest.fixture
def disable_embedding(monkeypatch):
    """embeddingサービスを無効化"""
    monkeypatch.setattr(emb, '_request_encode', lambda texts, prefix: None)
    monkeypatch.setattr(emb, '_backfill_done', True)


@pytest.fixture
def test_subject(temp_db, mock_embedding_model):
    """テスト用サブジェクト（embedding有効）"""
    result = add_subject(name="hybrid-test", description="Hybrid search test")
    return result["subject_id"]


@pytest.fixture
def test_subject_no_vec(temp_db, disable_embedding):
    """テスト用サブジェクト（embedding無効）"""
    result = add_subject(name="fts-only-test", description="FTS only test")
    return result["subject_id"]


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
# ハイブリッド検索 統合テスト
# ========================================


def test_hybrid_search_3char_returns_results(test_subject):
    """3文字以上: ハイブリッド検索で結果が返る"""
    add_topic(
        subject_id=test_subject,
        title="ハイブリッド検索テスト用トピック",
        description="FTS5とベクトルの両方で検索される",
    )

    result = search(subject_id=test_subject, keyword="ハイブリッド検索テスト")

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["results"][0]["type"] == "topic"
    assert isinstance(result["results"][0]["score"], float)


def test_hybrid_search_2char_vec_only(test_subject):
    """2文字キーワード + ベクトル有効: ベクトル検索のみで結果が返る"""
    add_topic(
        subject_id=test_subject,
        title="設計ドキュメント",
        description="アーキテクチャ設計の詳細",
    )

    result = search(subject_id=test_subject, keyword="設計")

    assert "error" not in result
    assert len(result["results"]) >= 1
    # 登録したトピックがベクトル検索でヒットする
    titles = [r["title"] for r in result["results"]]
    assert "設計ドキュメント" in titles
    assert result["total_count"] == len(result["results"])


def test_hybrid_search_2char_vec_disabled(test_subject_no_vec):
    """2文字キーワード + ベクトル無効: KEYWORD_TOO_SHORTエラー"""
    add_topic(
        subject_id=test_subject_no_vec,
        title="設計ドキュメント",
        description="アーキテクチャ設計の詳細",
    )

    result = search(subject_id=test_subject_no_vec, keyword="設計")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_hybrid_search_1char_always_error(test_subject):
    """1文字キーワード: ベクトル有効でもエラー"""
    result = search(subject_id=test_subject, keyword="設")

    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_hybrid_search_3char_vec_disabled_fts_fallback(test_subject_no_vec):
    """3文字以上 + ベクトル無効: FTSのみで正常動作（graceful degradation）"""
    add_topic(
        subject_id=test_subject_no_vec,
        title="認証フローの設計議論",
        description="OAuth2の認証フローについて検討する",
    )

    result = search(subject_id=test_subject_no_vec, keyword="認証フロー")

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["results"][0]["title"] == "認証フローの設計議論"


def test_hybrid_search_score_is_rrf(test_subject):
    """スコアがRRFスコアで返る（BM25の生値ではない）"""
    add_topic(
        subject_id=test_subject,
        title="RRFスコアテスト用トピック",
        description="スコアの形式を検証する",
    )

    result = search(subject_id=test_subject, keyword="RRFスコアテスト")

    assert "error" not in result
    assert len(result["results"]) >= 1
    score = result["results"][0]["score"]
    # RRFスコアは 1/(k+rank) の範囲内（0 < score <= 1/(k+1)）
    # w_fts=1, w_vec=1, k=60 なので最大 2/(60+1) ≈ 0.0328
    assert 0 < score <= 2 / (RRF_K + 1) + 0.001


def test_hybrid_search_cross_type_with_vec(test_subject):
    """ハイブリッド検索: topic/decision/task全てが対象"""
    topic = add_topic(
        subject_id=test_subject,
        title="横断ハイブリッドテスト用トピック",
        description="横断検索の動作確認",
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="横断ハイブリッドテスト決定",
        reason="テスト用",
    )
    add_task(
        subject_id=test_subject,
        title="横断ハイブリッドテストタスク",
        description="テスト用タスク",
    )

    result = search(subject_id=test_subject, keyword="横断ハイブリッドテスト")

    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "task" in types_found


def test_hybrid_search_type_filter(test_subject):
    """ハイブリッド検索: type_filterが効く"""
    topic = add_topic(
        subject_id=test_subject,
        title="フィルターハイブリッドテスト用",
        description="テスト用",
    )
    add_decision(
        topic_id=topic["topic_id"],
        decision="フィルターハイブリッドテスト決定",
        reason="テスト用",
    )

    result = search(
        subject_id=test_subject,
        keyword="フィルターハイブリッドテスト",
        type_filter="topic",
    )

    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "topic"


def test_hybrid_search_subject_isolation(test_subject):
    """ハイブリッド検索: subject_id分離が保たれる"""
    subject2_result = add_subject(name="hybrid-isolated", description="Isolated")
    subject2 = subject2_result["subject_id"]

    add_topic(
        subject_id=test_subject,
        title="分離テスト対象トピック",
        description="このサブジェクトのみヒットすべき",
    )
    add_topic(
        subject_id=subject2,
        title="分離テスト他サブジェクト",
        description="こちらはヒットしない",
    )

    result = search(subject_id=test_subject, keyword="分離テスト")

    assert "error" not in result
    for item in result["results"]:
        assert item["title"] != "分離テスト他サブジェクト"
