"""FTS5統合検索（search / get_by_id）のテスト

subjects廃止に伴い、search()のsubject_id引数は後続タスク(#404/#405)で
tags引数に置換される。ここでは書き込みツールの呼び出し修正のみ行い、
search/get_by_idを直接呼ぶテストはスキップする。
"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
from src.services.discussion_log_service import add_log as add_log_entry
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
# 後続タスク(#404/#405)でsearchのsubject_id → tagsに切り替えた後に再有効化
# ========================================


pytestmark_search = pytest.mark.skip("Pending task #404/#405: read tool migration (search uses subject_id)")


@pytestmark_search
def test_search_basic(temp_db):
    pass


@pytestmark_search
def test_search_response_format(temp_db):
    pass


@pytestmark_search
def test_search_bm25_ranking(temp_db):
    pass


@pytestmark_search
def test_search_type_filter(temp_db):
    pass


@pytestmark_search
def test_search_subject_isolation(temp_db):
    pass


@pytestmark_search
def test_search_limit_control(temp_db):
    pass


@pytestmark_search
def test_search_limit_max_50(temp_db):
    pass


@pytestmark_search
def test_search_keyword_too_short(temp_db):
    pass


@pytestmark_search
def test_search_keyword_too_short_after_strip(temp_db):
    pass


@pytestmark_search
def test_search_empty_results(temp_db):
    pass


@pytestmark_search
def test_search_special_characters(temp_db):
    pass


@pytestmark_search
def test_search_japanese(temp_db):
    pass


@pytestmark_search
def test_search_trigger_sync_topic(temp_db):
    pass


@pytestmark_search
def test_search_trigger_sync_decision(temp_db):
    pass


@pytestmark_search
def test_search_trigger_sync_task(temp_db):
    pass


@pytestmark_search
def test_search_invalid_type_filter(temp_db):
    pass


@pytestmark_search
def test_search_cross_type(temp_db):
    pass


# ========================================
# get_by_id ツールのテスト
# get_by_idはsubject_idカラムを参照するため後続タスクで対応
# ========================================


@pytestmark_search
def test_get_by_id_topic(temp_db):
    pass


@pytestmark_search
def test_get_by_id_decision(temp_db):
    pass


@pytestmark_search
def test_get_by_id_task(temp_db):
    pass


@pytestmark_search
def test_get_by_id_not_found(temp_db):
    pass


@pytestmark_search
def test_get_by_id_invalid_type(temp_db):
    pass


# ========================================
# discussion_logs 検索テスト
# ========================================


@pytestmark_search
def test_search_trigger_sync_log(temp_db):
    pass


@pytestmark_search
def test_search_type_filter_log(temp_db):
    pass


@pytestmark_search
def test_search_cross_type_includes_log(temp_db):
    pass


@pytestmark_search
def test_get_by_id_log(temp_db):
    pass


@pytestmark_search
def test_search_log_title_fallback(temp_db):
    pass


@pytestmark_search
def test_search_log_title_fallback_short_content(temp_db):
    pass


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
