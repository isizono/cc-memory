"""ハイブリッド検索（FTS5 + ベクトル + RRF統合）のテスト

_rrf_merge単体テスト + _apply_recency_boost単体テスト + タグ対応の統合テスト。
"""
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
import pytest
import numpy as np

from src.db import init_database, get_connection
from src.services.search_service import (
    _rrf_merge, _apply_recency_boost,
    RRF_K, RRF_W_FTS, RRF_W_VEC, RECENCY_DECAY_RATE,
)
from src.services import search_service
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.activity_service import add_activity
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
        {"type": "activity", "id": 20, "title": "Y"},
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
        tags=["domain:test", "intent:design"],
    )
    add_topic(
        title="タグ付きハイブリッド検索対象外テスト",
        description="これはヒットしない",
        tags=["domain:other"],
    )

    result = search_service.search(
        keyword="タグ付きハイブリッド検索",
        tags=["intent:design"],
    )

    assert "error" not in result
    titles = [r["title"] for r in result["results"]]
    assert any("対象テスト" in t for t in titles)
    assert all("対象外テスト" not in t for t in titles)


def test_hybrid_search_cross_type_with_tags(temp_db, mock_embedding_model):
    """ハイブリッド検索: topic/decision/activity全てが対象（タグフィルタ付き）"""
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
    add_activity(
        title="横断ハイブリッドタグテストアクティビティ",
        description="テスト用アクティビティ",
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
    assert "activity" in types_found


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


# ========================================
# keyword配列（AND検索）のテスト
# ========================================


def test_hybrid_keyword_array_and(temp_db, mock_embedding_model):
    """配列keyword: ハイブリッド検索でAND動作"""
    add_topic(
        title="ハイブリッド配列検索テスト対象",
        description="メモリ管理と検索機能の両方を扱うトピック",
        tags=DEFAULT_TAGS,
    )
    add_topic(
        title="ハイブリッド配列検索テスト対象外",
        description="メモリ管理のみ",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword=["ハイブリッド配列検索", "テスト対象"])

    assert "error" not in result
    assert len(result["results"]) >= 1


def test_hybrid_keyword_array_vec_search(temp_db, mock_embedding_model):
    """配列keyword: ベクトル検索が結果を返す"""
    add_topic(
        title="ベクトル配列検索テスト用トピック",
        description="複数キーワードでのベクトル検索確認",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword=["ベクトル配列検索", "テスト用"])

    assert "error" not in result
    assert len(result["results"]) >= 1
    assert isinstance(result["results"][0]["score"], float)


def test_hybrid_keyword_array_2char_vec_only(temp_db, mock_embedding_model):
    """配列内に2文字キーワード: ベクトル検索のみで動作"""
    add_topic(
        title="設計レビュー用ドキュメント",
        description="設計の詳細レビュー",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword=["設計", "レビュー"])

    assert "error" not in result
    # ベクトル検索のみなので結果は返る（エラーにはならない）
    assert "results" in result


# ========================================
# keyword配列（OR検索）のテスト
# ========================================


def test_hybrid_keyword_or_basic(temp_db, mock_embedding_model):
    """OR検索: ハイブリッド検索でOR動作"""
    add_topic(title="ハイブリッドOR検索テスト対象A", description="メモリ管理の説明", tags=DEFAULT_TAGS)
    add_topic(title="ハイブリッドOR検索テスト対象B", description="検索機能の説明", tags=DEFAULT_TAGS)
    result = search_service.search(keyword=["ハイブリッドOR検索テスト対象A", "ハイブリッドOR検索テスト対象B"], keyword_mode="or")
    assert "error" not in result
    assert len(result["results"]) >= 2


def test_hybrid_keyword_or_2char_vec(temp_db, mock_embedding_model):
    """OR検索: 2文字キーワード混在でもベクトル検索が補完する"""
    add_topic(title="設計レビュー用ドキュメント", description="設計の詳細レビュー", tags=DEFAULT_TAGS)
    result = search_service.search(keyword=["設計", "レビュー"], keyword_mode="or")
    assert "error" not in result
    assert "results" in result


# ========================================
# _apply_recency_boost 単体テスト
# ========================================


def test_recency_boost_newer_scores_higher(temp_db):
    """recency boost: 同じRRFスコアでも新しいアイテムのほうがスコアが高くなる"""
    # 2つのトピックを作成
    t1 = add_topic(
        title="古いトピック",
        description="recency boostテスト用",
        tags=DEFAULT_TAGS,
    )
    t2 = add_topic(
        title="新しいトピック",
        description="recency boostテスト用",
        tags=DEFAULT_TAGS,
    )

    # t1のcreated_atを365日前に書き換え
    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("UPDATE discussion_topics SET created_at = ? WHERE id = ?", (old_date, t1["topic_id"]))
    conn.commit()
    conn.close()

    base_score = 0.01
    results = [
        {"type": "topic", "id": t1["topic_id"], "title": "古いトピック", "score": base_score},
        {"type": "topic", "id": t2["topic_id"], "title": "新しいトピック", "score": base_score},
    ]

    _apply_recency_boost(results)

    score_old = next(r["score"] for r in results if r["id"] == t1["topic_id"])
    score_new = next(r["score"] for r in results if r["id"] == t2["topic_id"])

    # 新しいアイテムのほうがスコアが高い
    assert score_new > score_old
    # ソート順も新しいほうが先
    assert results[0]["id"] == t2["topic_id"]


def test_recency_boost_decay_formula(temp_db):
    """recency boost: 減衰率が formula 通りに計算される"""
    t = add_topic(
        title="減衰計算テスト",
        description="テスト用",
        tags=DEFAULT_TAGS,
    )

    # created_atを固定日時に設定し、nowも固定して厳密に検証
    created_at = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2025, 7, 2, 0, 0, 0, tzinfo=timezone.utc)  # 182日後
    conn = get_connection()
    conn.execute(
        "UPDATE discussion_topics SET created_at = ? WHERE id = ?",
        (created_at.strftime("%Y-%m-%d %H:%M:%S"), t["topic_id"]),
    )
    conn.commit()
    conn.close()

    base_score = 1.0
    results = [
        {"type": "topic", "id": t["topic_id"], "title": "減衰計算テスト", "score": base_score},
    ]

    _apply_recency_boost(results, now=now)

    # 182日 × 0.0014 = 0.2548, factor = 1/(1+0.2548) ≈ 0.797
    expected_factor = 1.0 / (1.0 + 182 * RECENCY_DECAY_RATE)
    assert results[0]["score"] == pytest.approx(base_score * expected_factor)


def test_recency_boost_empty_list():
    """recency boost: 空リストでエラーにならない"""
    results = []
    _apply_recency_boost(results)
    assert results == []


def test_recency_boost_reorders_by_score(temp_db):
    """recency boost: スコア降順で再ソートされる"""
    t1 = add_topic(title="トピックA", description="テスト", tags=DEFAULT_TAGS)
    t2 = add_topic(title="トピックB", description="テスト", tags=DEFAULT_TAGS)

    # t1を古く、t2を新しくする
    old_date = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("UPDATE discussion_topics SET created_at = ? WHERE id = ?", (old_date, t1["topic_id"]))
    conn.commit()
    conn.close()

    # t1のスコアをわずかに高くしても、古さで逆転するケース
    results = [
        {"type": "topic", "id": t1["topic_id"], "title": "トピックA", "score": 0.012},
        {"type": "topic", "id": t2["topic_id"], "title": "トピックB", "score": 0.010},
    ]

    _apply_recency_boost(results)

    # 730日前: factor = 1/(1+730*0.0014) = 1/2.022 ≈ 0.495
    # t1: 0.012 * 0.495 ≈ 0.00594
    # t2 (今日): 0.010 * ~1.0 = 0.010
    # t2が上位に来るはず
    assert results[0]["id"] == t2["topic_id"]


def test_recency_boost_cross_type(temp_db):
    """recency boost: 異なるtypeを横断して処理できる"""
    t = add_topic(title="横断テストトピック", description="テスト", tags=DEFAULT_TAGS)
    d = add_decision(
        topic_id=t["topic_id"],
        decision="横断テスト決定",
        reason="テスト用",
    )

    base_score = 0.01
    results = [
        {"type": "topic", "id": t["topic_id"], "title": "横断テストトピック", "score": base_score},
        {"type": "decision", "id": d["decision_id"], "title": "横断テスト決定", "score": base_score},
    ]

    # エラーなく完了すること
    _apply_recency_boost(results)

    # 両方ともスコアが付いている（作成直後なのでほぼ変わらない）
    for r in results:
        assert r["score"] > 0
        assert r["score"] <= base_score


# ========================================
# recency boost 統合テスト
# ========================================


def test_search_recency_boost_applied(temp_db, mock_embedding_model):
    """search結果にrecency boostが適用されている: 新しいほうが上位"""
    # 2つのトピックを同じキーワードで作成
    t_old = add_topic(
        title="リーセンシー統合テスト用トピック古い",
        description="リーセンシー統合テストの検証用データ",
        tags=DEFAULT_TAGS,
    )
    t_new = add_topic(
        title="リーセンシー統合テスト用トピック新しい",
        description="リーセンシー統合テストの検証用データ",
        tags=DEFAULT_TAGS,
    )

    # 古いほうのcreated_atを1年前に設定
    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("UPDATE discussion_topics SET created_at = ? WHERE id = ?", (old_date, t_old["topic_id"]))
    conn.commit()
    conn.close()

    result = search_service.search(keyword="リーセンシー統合テスト")

    assert "error" not in result
    assert len(result["results"]) >= 2

    # 結果内でtopic同士の順序を確認
    topic_results = [r for r in result["results"] if r["type"] == "topic"]
    assert len(topic_results) >= 2

    # 新しいトピックが古いトピックより先に来る
    ids_in_order = [r["id"] for r in topic_results]
    idx_new = ids_in_order.index(t_new["topic_id"])
    idx_old = ids_in_order.index(t_old["topic_id"])
    assert idx_new < idx_old, "新しいトピックが古いトピックより上位に来るべき"


# ========================================
# search_methods_used テスト
# ========================================


def test_search_methods_used_hybrid(temp_db, mock_embedding_model):
    """3文字以上 + ベクトル有効: fts5とvectorの両方が使われる"""
    add_topic(
        title="ハイブリッドメソッド確認テスト用",
        description="FTS5とベクトルの両方が使われることを確認",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="ハイブリッドメソッド確認テスト")

    assert "error" not in result
    assert "search_methods_used" in result
    assert result["search_methods_used"] == ["fts5", "vector"]


def test_search_methods_used_vector_only(temp_db, mock_embedding_model):
    """2文字キーワード + ベクトル有効: vectorのみが使われる"""
    add_topic(
        title="設計ドキュメント",
        description="アーキテクチャ設計の詳細",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="設計")

    assert "error" not in result
    assert "search_methods_used" in result
    assert result["search_methods_used"] == ["vector"]


def test_search_methods_used_fts_only_vec_disabled(temp_db, disable_embedding):
    """3文字以上 + ベクトル無効: fts5のみが使われる"""
    add_topic(
        title="認証フローメソッド確認テスト",
        description="FTSのみで検索される",
        tags=DEFAULT_TAGS,
    )

    result = search_service.search(keyword="認証フローメソッド確認テスト")

    assert "error" not in result
    assert "search_methods_used" in result
    assert result["search_methods_used"] == ["fts5"]
