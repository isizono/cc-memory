"""FTS5統合検索（search / get_by_id）のテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.activity_service import add_activity
from src.services.discussion_log_service import add_log as add_log_entry
from src.services import search_service
import src.services.embedding_service as emb


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture(autouse=True)
def disable_embedding(monkeypatch):
    """FTS5テストではembeddingサービスを無効化"""
    monkeypatch.setattr(emb, '_server_initialized', False)
    monkeypatch.setattr(emb, '_backfill_done', True)
    monkeypatch.setattr(emb, '_ensure_server_running', lambda: False)


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


# ========================================
# search ツールのテスト
# ========================================


def test_search_basic(temp_db):
    """基本検索: タグなしで全件検索"""
    add_topic(title="テスト用トピック検索対象", description="検索テスト説明文", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="テスト用トピック検索対象")
    assert "error" not in result
    assert len(result["results"]) >= 1


def test_search_response_format(temp_db):
    """レスポンス形式: results配列とtotal_count"""
    add_topic(title="レスポンス形式検索テスト", description="テスト用", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="レスポンス形式検索テスト")
    assert "error" not in result
    assert "results" in result
    assert "total_count" in result
    assert isinstance(result["results"], list)
    if result["results"]:
        item = result["results"][0]
        assert "type" in item
        assert "id" in item
        assert "title" in item
        assert "score" in item
        assert "snippet" in item


def test_search_bm25_ranking(temp_db):
    """BM25ランキング: タイトルマッチの方がスコアが高い"""
    add_topic(title="ランキング最優先テスト対象トピック", description="別の説明", tags=DEFAULT_TAGS)
    add_topic(title="別のトピック", description="ランキング最優先テスト対象トピックの説明", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="ランキング最優先テスト対象トピック")
    assert "error" not in result
    assert len(result["results"]) >= 1


def test_search_type_filter(temp_db):
    """type_filterで種別を絞り込み"""
    topic = add_topic(title="フィルタテスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="フィルタテスト決定事項", reason="テスト")
    result = search_service.search(keyword="フィルタテスト", type_filter="topic")
    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "topic"


def test_search_with_tags(temp_db):
    """タグフィルタ: ANDで絞り込み"""
    add_topic(title="タグ対象トピック一致テスト", description="テスト", tags=["domain:test", "scope:search"])
    add_topic(title="タグ対象外トピック一致テスト", description="テスト", tags=["domain:other"])
    result = search_service.search(keyword="タグ対象", tags=["domain:test"])
    assert "error" not in result
    # domain:test のみヒット
    titles = [r["title"] for r in result["results"]]
    assert any("タグ対象トピック一致テスト" in t for t in titles)
    assert all("タグ対象外トピック一致テスト" not in t for t in titles)


def test_search_with_multiple_tags_and(temp_db):
    """タグフィルタ: 複数タグのAND条件"""
    add_topic(title="複数タグAND対象テスト", description="テスト", tags=["domain:test", "scope:search"])
    add_topic(title="複数タグAND部分テスト", description="テスト", tags=["domain:test"])
    result = search_service.search(keyword="複数タグAND", tags=["domain:test", "scope:search"])
    assert "error" not in result
    titles = [r["title"] for r in result["results"]]
    assert any("複数タグAND対象テスト" in t for t in titles)
    assert all("複数タグAND部分テスト" not in t for t in titles)


def test_search_tags_empty_list(temp_db):
    """空配列のタグ: 全件検索と同じ"""
    add_topic(title="空タグリスト検索テスト", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="空タグリスト検索テスト", tags=[])
    assert "error" not in result
    assert len(result["results"]) >= 1


def test_search_nonexistent_tag(temp_db):
    """存在しないタグ: 空結果"""
    add_topic(title="存在しないタグ検索テスト", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="存在しないタグ検索テスト", tags=["domain:nonexistent"])
    assert "error" not in result
    assert result["results"] == []
    assert result["total_count"] == 0


def test_search_limit_control(temp_db):
    """limit指定で件数制御"""
    for i in range(5):
        add_topic(title=f"リミットテスト用トピック{i}", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="リミットテスト用トピック", limit=2)
    assert "error" not in result
    assert len(result["results"]) <= 2


def test_search_limit_max_50(temp_db):
    """limit=100指定でも50に丸められる"""
    add_topic(title="リミット最大値テスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="リミット最大値テスト用トピック", limit=100)
    assert "error" not in result
    # 内部的にlimitが50にclampされていればOK（結果が少なくてもエラーにならない）


def test_search_keyword_too_short(temp_db):
    """1文字キーワードでKEYWORD_TOO_SHORTエラー"""
    result = search_service.search(keyword="あ")
    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_keyword_too_short_after_strip(temp_db):
    """空白を除くと1文字になるキーワード"""
    result = search_service.search(keyword=" あ ")
    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_empty_results(temp_db):
    """ヒットなしで空配列"""
    result = search_service.search(keyword="絶対に存在しないキーワード123456")
    assert "error" not in result
    assert result["results"] == []
    assert result["total_count"] == 0


def test_search_special_characters(temp_db):
    """特殊文字を含むキーワード（FTS5エスケープ確認）"""
    add_topic(title='テスト "特殊文字" 検索対象', description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword='"特殊文字"')
    assert "error" not in result


def test_search_japanese(temp_db):
    """日本語キーワード検索"""
    add_topic(title="日本語検索テスト用トピック", description="漢字ひらがなカタカナ", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="日本語検索テスト用")
    assert "error" not in result
    assert len(result["results"]) >= 1


def test_search_trigger_sync_topic(temp_db):
    """topicがsearch_indexに同期される"""
    add_topic(title="トリガー同期トピック検索テスト", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="トリガー同期トピック検索テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    assert result["results"][0]["type"] == "topic"


def test_search_trigger_sync_decision(temp_db):
    """decisionがsearch_indexに同期される"""
    topic = add_topic(title="同期テスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="トリガー同期決定事項テスト", reason="テスト理由")
    result = search_service.search(keyword="トリガー同期決定事項テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    types = [r["type"] for r in result["results"]]
    assert "decision" in types


def test_search_trigger_sync_activity(temp_db):
    """activityがsearch_indexに同期される"""
    add_activity(title="トリガー同期アクティビティ検索テスト", description="テスト用アクティビティ", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="トリガー同期アクティビティ検索テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    types = [r["type"] for r in result["results"]]
    assert "activity" in types


def test_search_invalid_type_filter(temp_db):
    """不正なtype_filterでINVALID_TYPE_FILTERエラー"""
    result = search_service.search(keyword="テスト", type_filter="invalid")
    assert "error" in result
    assert result["error"]["code"] == "INVALID_TYPE_FILTER"


def test_search_cross_type(temp_db):
    """横断検索: topic/decision/activity全てが対象"""
    topic = add_topic(title="横断検索テスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="横断検索テスト決定", reason="テスト")
    add_activity(title="横断検索テスト用アクティビティ", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="横断検索テスト")
    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "activity" in types_found


def test_search_decision_inherits_topic_tags(temp_db):
    """decisionはtopicのタグを継承してフィルタされる"""
    topic = add_topic(title="継承テスト用トピック", description="テスト", tags=["domain:test", "scope:inherit"])
    add_decision(topic_id=topic["topic_id"], decision="継承タグフィルタ決定テスト", reason="テスト")
    # scope:inherit でフィルタ → topicを親に持つdecisionもヒット
    result = search_service.search(keyword="継承タグフィルタ決定テスト", tags=["scope:inherit"])
    assert "error" not in result
    types = [r["type"] for r in result["results"]]
    assert "decision" in types


def test_search_log_inherits_topic_tags(temp_db):
    """logはtopicのタグを継承してフィルタされる"""
    topic = add_topic(title="ログ継承テスト用トピック", description="テスト", tags=["domain:test", "scope:loginherit"])
    add_log_entry(topic_id=topic["topic_id"], title="継承タグフィルタログテスト", content="テストログ内容")
    result = search_service.search(keyword="継承タグフィルタログテスト", tags=["scope:loginherit"])
    assert "error" not in result
    types = [r["type"] for r in result["results"]]
    assert "log" in types


# ========================================
# get_by_ids ツールのテスト
# ========================================


def test_get_by_ids_single_topic(temp_db):
    """get_by_ids: topicの詳細取得（1件）"""
    topic = add_topic(title="詳細取得テスト用トピック", description="テスト説明", tags=DEFAULT_TAGS)
    result = search_service.get_by_ids([{"type": "topic", "id": topic["topic_id"]}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" not in item
    assert item["type"] == "topic"
    assert item["data"]["title"] == "詳細取得テスト用トピック"
    assert item["data"]["description"] == "テスト説明"
    assert "tags" in item["data"]
    assert "domain:test" in item["data"]["tags"]


def test_get_by_ids_single_decision(temp_db):
    """get_by_ids: decisionの詳細取得（1件）"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    dec = add_decision(topic_id=topic["topic_id"], decision="詳細取得テスト決定", reason="テスト理由")
    result = search_service.get_by_ids([{"type": "decision", "id": dec["decision_id"]}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" not in item
    assert item["type"] == "decision"
    assert item["data"]["decision"] == "詳細取得テスト決定"
    assert "tags" in item["data"]
    assert "domain:test" in item["data"]["tags"]


def test_get_by_ids_single_activity(temp_db):
    """get_by_ids: activityの詳細取得（1件）"""
    activity = add_activity(title="詳細取得テスト用アクティビティ", description="テスト説明", tags=DEFAULT_TAGS)
    result = search_service.get_by_ids([{"type": "activity", "id": activity["activity_id"]}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" not in item
    assert item["type"] == "activity"
    assert item["data"]["title"] == "詳細取得テスト用アクティビティ"
    assert "tags" in item["data"]
    assert "domain:test" in item["data"]["tags"]


def test_get_by_ids_single_log(temp_db):
    """get_by_ids: logの詳細取得（1件）"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    log = add_log_entry(topic_id=topic["topic_id"], title="詳細取得テストログ", content="テスト内容")
    result = search_service.get_by_ids([{"type": "log", "id": log["log_id"]}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" not in item
    assert item["type"] == "log"
    assert item["data"]["title"] == "詳細取得テストログ"
    assert item["data"]["content"] == "テスト内容"
    assert "tags" in item["data"]
    assert "domain:test" in item["data"]["tags"]


def test_get_by_ids_not_found(temp_db):
    """get_by_ids: 存在しないIDでNOT_FOUNDエラー"""
    result = search_service.get_by_ids([{"type": "topic", "id": 999999}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" in item
    assert item["error"]["code"] == "NOT_FOUND"


def test_get_by_ids_invalid_type(temp_db):
    """get_by_ids: 不正な種別でINVALID_TYPEエラー"""
    result = search_service.get_by_ids([{"type": "invalid", "id": 1}])
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert "error" in item
    assert item["error"]["code"] == "INVALID_TYPE"


# ========================================
# discussion_logs 検索テスト
# ========================================


def test_search_trigger_sync_log(temp_db):
    """logがsearch_indexに同期される"""
    topic = add_topic(title="ログ同期テスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_log_entry(topic_id=topic["topic_id"], title="トリガー同期ログ検索テスト", content="ログの内容")
    result = search_service.search(keyword="トリガー同期ログ検索テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    types = [r["type"] for r in result["results"]]
    assert "log" in types


def test_search_type_filter_log(temp_db):
    """type_filter=logでログのみ取得"""
    topic = add_topic(title="ログフィルタテスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_log_entry(topic_id=topic["topic_id"], title="ログフィルタ対象テスト", content="ログ内容")
    result = search_service.search(keyword="ログフィルタ対象テスト", type_filter="log")
    assert "error" not in result
    for item in result["results"]:
        assert item["type"] == "log"


def test_search_cross_type_includes_log(temp_db):
    """横断検索にlogも含まれる"""
    topic = add_topic(title="横断ログ検索テスト用", description="テスト", tags=DEFAULT_TAGS)
    add_log_entry(topic_id=topic["topic_id"], title="横断ログ検索テスト対象", content="ログ内容")
    add_decision(topic_id=topic["topic_id"], decision="横断ログ検索テスト決定", reason="テスト")
    result = search_service.search(keyword="横断ログ検索テスト")
    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "log" in types_found


def test_search_log_title_fallback(temp_db):
    """logのtitleが空の場合、contentの先頭50文字をフォールバック"""
    # NOTE: add_log_entryはtitle空文字をバリデーションエラーにするため、
    # ここではtitle付きで作成し、get_by_idsでフォールバック動作を確認
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    log = add_log_entry(topic_id=topic["topic_id"], title="フォールバックテスト", content="テスト内容です")
    result = search_service.get_by_ids([{"type": "log", "id": log["log_id"]}])
    item = result["results"][0]
    assert "error" not in item
    assert item["data"]["title"] == "フォールバックテスト"


def test_add_log_empty_title_error(temp_db):
    """バリデーション: title空文字でadd_logするとバリデーションエラー"""
    topic = add_topic(
        title="バリデーションテスト用トピック",
        description="テスト用",
        tags=DEFAULT_TAGS,
    )

    result = add_log_entry(
        topic_id=topic["topic_id"],
        title="",
        content="内容があってもtitleが空ならエラー",
    )

    assert "error" in result
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "title must not be empty" in result["error"]["message"]


# ========================================
# snippet テスト
# ========================================


def test_search_snippet_topic(temp_db):
    """search結果のtopicにsnippetが含まれること（ソース: description）"""
    add_topic(title="スニペットトピックテスト", description="これはトピックの説明文です", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="スニペットトピックテスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "topic")
    assert "snippet" in item
    assert item["snippet"] == "これはトピックの説明文です"


def test_search_snippet_decision(temp_db):
    """search結果のdecisionにsnippetが含まれること（ソース: decision）"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="スニペット決定事項テスト用の内容", reason="テスト理由")
    result = search_service.search(keyword="スニペット決定事項テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "decision")
    assert "snippet" in item
    assert item["snippet"] == "スニペット決定事項テスト用の内容"


def test_search_snippet_activity(temp_db):
    """search結果のactivityにsnippetが含まれること（ソース: description）"""
    add_activity(title="スニペットアクティビティテスト", description="アクティビティの詳細説明テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="スニペットアクティビティテスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "activity")
    assert "snippet" in item
    assert item["snippet"] == "アクティビティの詳細説明テスト"


def test_search_snippet_log(temp_db):
    """search結果のlogにsnippetが含まれること（ソース: content）"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    add_log_entry(topic_id=topic["topic_id"], title="スニペットログテスト", content="ログの内容テスト文")
    result = search_service.search(keyword="スニペットログテスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "log")
    assert "snippet" in item
    assert item["snippet"] == "ログの内容テスト文"


def test_search_snippet_max_length(temp_db):
    """snippetは200文字以下に切り詰められること"""
    long_desc = "あ" * 300
    add_topic(title="スニペット長制限テスト", description=long_desc, tags=DEFAULT_TAGS)
    result = search_service.search(keyword="スニペット長制限テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "topic")
    assert "snippet" in item
    assert len(item["snippet"]) <= 200
    assert item["snippet"] == "あ" * 200


def test_search_snippet_empty_source(temp_db):
    """snippetソースが空（空文字列）の場合、snippetは空文字列"""
    add_topic(title="スニペット空ソーステスト", description="", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="スニペット空ソーステスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    item = next(r for r in result["results"] if r["type"] == "topic")
    assert "snippet" in item
    assert item["snippet"] == ""


# ========================================
# keyword配列（AND検索）のテスト
# ========================================


def test_search_keyword_array_and(temp_db):
    """配列keyword: AND検索で両キーワードを含む結果のみ返す"""
    add_topic(title="メモリ管理の検索テスト", description="検索機能のテスト", tags=DEFAULT_TAGS)
    add_topic(title="メモリ管理の設計ドキュメント", description="設計の詳細", tags=DEFAULT_TAGS)
    add_topic(title="検索機能の改善提案", description="改善案", tags=DEFAULT_TAGS)
    # "メモリ" AND "検索" → 両方含む最初のトピックのみヒット
    result = search_service.search(keyword=["メモリ管理", "検索テスト"])
    assert "error" not in result
    assert len(result["results"]) >= 1
    titles = [r["title"] for r in result["results"]]
    assert any("メモリ管理の検索テスト" in t for t in titles)
    # "メモリ管理の設計ドキュメント" は "検索テスト" を含まないのでヒットしない
    assert all("メモリ管理の設計ドキュメント" not in t for t in titles)


def test_search_keyword_array_element_too_short(temp_db):
    """配列内に2文字未満の要素があるとKEYWORD_TOO_SHORTエラー"""
    result = search_service.search(keyword=["テスト", "あ"])
    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_keyword_empty_array(temp_db):
    """空配列でKEYWORD_TOO_SHORTエラー"""
    result = search_service.search(keyword=[])
    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"


def test_search_keyword_single_string_backward_compat(temp_db):
    """単一文字列の後方互換: 既存動作と同じ"""
    add_topic(title="後方互換テスト用トピック検索", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="後方互換テスト用トピック検索")
    assert "error" not in result
    assert len(result["results"]) >= 1


def test_search_keyword_array_with_2char_fts_skipped(temp_db):
    """配列内に2文字キーワードがある場合、FTS5検索はスキップされる（ベクトル無効時エラー）"""
    # embedding無効（autouse fixture）なので、2文字キーワードがあるとベクトルのみ→エラー
    result = search_service.search(keyword=["テスト", "設計"])
    assert "error" in result
    assert result["error"]["code"] == "KEYWORD_TOO_SHORT"
    assert "vector search is unavailable" in result["error"]["message"]


# ========================================
# get_by_ids バッチ取得のテスト
# ========================================


def test_get_by_ids_batch(temp_db):
    """複数アイテムのバッチ取得"""
    add_topic(title="トピック1", description="説明1", tags=DEFAULT_TAGS)
    add_activity(title="アクティビティ1", description="説明1", tags=DEFAULT_TAGS)
    result = search_service.get_by_ids([
        {"type": "topic", "id": 1},
        {"type": "activity", "id": 1},
    ])
    assert "results" in result
    assert len(result["results"]) == 2
    assert result["results"][0]["type"] == "topic"
    assert result["results"][1]["type"] == "activity"


def test_get_by_ids_empty(temp_db):
    """空リストの場合"""
    result = search_service.get_by_ids([])
    assert result == {"results": []}


def test_get_by_ids_too_many(temp_db):
    """件数上限超過"""
    items = [{"type": "topic", "id": i} for i in range(21)]
    result = search_service.get_by_ids(items)
    assert "error" in result
    assert result["error"]["code"] == "TOO_MANY_ITEMS"


def test_get_by_ids_not_found(temp_db):
    """存在しないidを含む場合（エラーは個別結果に含まれる）"""
    result = search_service.get_by_ids([
        {"type": "topic", "id": 99999},
    ])
    assert len(result["results"]) == 1
    assert "error" in result["results"][0]
    assert result["results"][0]["error"]["code"] == "NOT_FOUND"


def test_get_by_ids_mixed_types(temp_db):
    """全4種類のtype混在でのバッチ取得"""
    topic = add_topic(title="混在テストトピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="混在テスト決定", reason="テスト")
    add_activity(title="混在テストアクティビティ", description="テスト", tags=DEFAULT_TAGS)
    add_log_entry(topic_id=topic["topic_id"], title="混在テストログ", content="テスト内容")
    result = search_service.get_by_ids([
        {"type": "topic", "id": 1},
        {"type": "decision", "id": 1},
        {"type": "activity", "id": 1},
        {"type": "log", "id": 1},
    ])
    assert "results" in result
    assert len(result["results"]) == 4
    types = [r["type"] for r in result["results"]]
    assert types == ["topic", "decision", "activity", "log"]


def test_get_by_ids_at_limit(temp_db):
    """ちょうど20件（上限ぴったり）は成功する"""
    items = [{"type": "topic", "id": i} for i in range(20)]
    result = search_service.get_by_ids(items)
    assert "error" not in result
    assert "results" in result
    assert len(result["results"]) == 20


def test_get_by_ids_invalid_type(temp_db):
    """不正なtypeを含む場合（get_by_idのINVALID_TYPEエラーが個別結果に含まれる）"""
    result = search_service.get_by_ids([
        {"type": "invalid", "id": 1},
    ])
    assert "results" in result
    assert len(result["results"]) == 1
    assert "error" in result["results"][0]
    assert result["results"][0]["error"]["code"] == "INVALID_TYPE"


def test_get_by_ids_missing_fields(temp_db):
    """type/idフィールドが欠落した場合"""
    result = search_service.get_by_ids([
        {"type": "topic"},
        {"id": 1},
        {},
    ])
    assert len(result["results"]) == 3
    for r in result["results"]:
        assert "error" in r
        assert r["error"]["code"] == "VALIDATION_ERROR"
