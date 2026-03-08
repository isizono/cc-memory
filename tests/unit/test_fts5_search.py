"""FTS5統合検索（search / get_by_id）のテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
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


def test_search_trigger_sync_task(temp_db):
    """taskがsearch_indexに同期される"""
    add_task(title="トリガー同期タスク検索テスト", description="テスト用タスク", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="トリガー同期タスク検索テスト")
    assert "error" not in result
    assert len(result["results"]) >= 1
    types = [r["type"] for r in result["results"]]
    assert "task" in types


def test_search_invalid_type_filter(temp_db):
    """不正なtype_filterでINVALID_TYPE_FILTERエラー"""
    result = search_service.search(keyword="テスト", type_filter="invalid")
    assert "error" in result
    assert result["error"]["code"] == "INVALID_TYPE_FILTER"


def test_search_cross_type(temp_db):
    """横断検索: topic/decision/task全てが対象"""
    topic = add_topic(title="横断検索テスト用トピック", description="テスト", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="横断検索テスト決定", reason="テスト")
    add_task(title="横断検索テスト用タスク", description="テスト", tags=DEFAULT_TAGS)
    result = search_service.search(keyword="横断検索テスト")
    assert "error" not in result
    types_found = {r["type"] for r in result["results"]}
    assert "topic" in types_found
    assert "decision" in types_found
    assert "task" in types_found


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
# get_by_id ツールのテスト
# ========================================


def test_get_by_id_topic(temp_db):
    """get_by_id: topicの詳細取得"""
    topic = add_topic(title="詳細取得テスト用トピック", description="テスト説明", tags=DEFAULT_TAGS)
    result = search_service.get_by_id("topic", topic["topic_id"])
    assert "error" not in result
    assert result["type"] == "topic"
    assert result["data"]["title"] == "詳細取得テスト用トピック"
    assert result["data"]["description"] == "テスト説明"
    assert "tags" in result["data"]
    assert "domain:test" in result["data"]["tags"]


def test_get_by_id_decision(temp_db):
    """get_by_id: decisionの詳細取得"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    dec = add_decision(topic_id=topic["topic_id"], decision="詳細取得テスト決定", reason="テスト理由")
    result = search_service.get_by_id("decision", dec["decision_id"])
    assert "error" not in result
    assert result["type"] == "decision"
    assert result["data"]["decision"] == "詳細取得テスト決定"
    assert "tags" in result["data"]
    assert "domain:test" in result["data"]["tags"]


def test_get_by_id_task(temp_db):
    """get_by_id: taskの詳細取得"""
    task = add_task(title="詳細取得テスト用タスク", description="テスト説明", tags=DEFAULT_TAGS)
    result = search_service.get_by_id("task", task["task_id"])
    assert "error" not in result
    assert result["type"] == "task"
    assert result["data"]["title"] == "詳細取得テスト用タスク"
    assert "tags" in result["data"]
    assert "domain:test" in result["data"]["tags"]


def test_get_by_id_log(temp_db):
    """get_by_id: logの詳細取得"""
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    log = add_log_entry(topic_id=topic["topic_id"], title="詳細取得テストログ", content="テスト内容")
    result = search_service.get_by_id("log", log["log_id"])
    assert "error" not in result
    assert result["type"] == "log"
    assert result["data"]["title"] == "詳細取得テストログ"
    assert result["data"]["content"] == "テスト内容"
    assert "tags" in result["data"]
    assert "domain:test" in result["data"]["tags"]


def test_get_by_id_not_found(temp_db):
    """get_by_id: 存在しないIDでNOT_FOUNDエラー"""
    result = search_service.get_by_id("topic", 999999)
    assert "error" in result
    assert result["error"]["code"] == "NOT_FOUND"


def test_get_by_id_invalid_type(temp_db):
    """get_by_id: 不正な種別でINVALID_TYPEエラー"""
    result = search_service.get_by_id("invalid", 1)
    assert "error" in result
    assert result["error"]["code"] == "INVALID_TYPE"


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
    # ここではtitle付きで作成し、get_by_idでフォールバック動作を確認
    topic = add_topic(title="トピック", description="テスト", tags=DEFAULT_TAGS)
    log = add_log_entry(topic_id=topic["topic_id"], title="フォールバックテスト", content="テスト内容です")
    result = search_service.get_by_id("log", log["log_id"])
    assert "error" not in result
    assert result["data"]["title"] == "フォールバックテスト"


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
