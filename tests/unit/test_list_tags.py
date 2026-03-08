"""list_tags機能のユニットテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import add_topic
from src.services.decision_service import add_decision
from src.services.task_service import add_task
from src.services.discussion_log_service import add_log
from src.services.tag_service import list_tags
import src.services.embedding_service as emb


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture(autouse=True)
def disable_embedding(monkeypatch):
    """embeddingサービスを無効化"""
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


def test_list_tags_basic(temp_db):
    """基本動作: タグ一覧を取得できる"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test"])
    result = list_tags()
    assert "error" not in result
    assert "tags" in result
    assert isinstance(result["tags"], list)
    # init_databaseで作成されるdomain:defaultも含まれる
    tag_strs = [t["tag"] for t in result["tags"]]
    assert "domain:test" in tag_strs
    assert "domain:default" in tag_strs


def test_list_tags_usage_count(temp_db):
    """usage_countが正しくカウントされる"""
    # domain:test を3つのエンティティで使用
    topic = add_topic(title="Topic 1", description="Desc", tags=["domain:test"])
    add_task(title="Task 1", description="Desc", tags=["domain:test"])
    add_decision(topic_id=topic["topic_id"], decision="Dec 1", reason="Reason", tags=["scope:search"])

    result = list_tags()
    assert "error" not in result

    # domain:test は topic_tags(1) + task_tags(1) = 2
    test_tag = next(t for t in result["tags"] if t["tag"] == "domain:test")
    assert test_tag["usage_count"] == 2

    # scope:search は decision_tags(1) = 1
    search_tag = next(t for t in result["tags"] if t["tag"] == "scope:search")
    assert search_tag["usage_count"] == 1


def test_list_tags_usage_count_includes_logs(temp_db):
    """logのタグもusage_countに含まれる"""
    topic = add_topic(title="Topic", description="Desc", tags=["domain:test"])
    add_log(topic_id=topic["topic_id"], title="Log 1", content="Content", tags=["scope:logcount"])

    result = list_tags()
    assert "error" not in result

    logcount_tag = next(t for t in result["tags"] if t["tag"] == "scope:logcount")
    assert logcount_tag["usage_count"] == 1


def test_list_tags_sorted_by_usage_count_desc(temp_db):
    """usage_count降順でソートされる"""
    # domain:test を2つのトピックで使用
    add_topic(title="Topic 1", description="Desc", tags=["domain:test"])
    add_topic(title="Topic 2", description="Desc", tags=["domain:test"])
    # scope:rare を1つだけ
    add_topic(title="Topic 3", description="Desc", tags=["scope:rare"])

    result = list_tags()
    assert "error" not in result

    # domain:default(1), domain:test(2), scope:rare(1) のはず
    # usage_count降順で、domain:testが最初に来る
    tags = result["tags"]
    usage_counts = [t["usage_count"] for t in tags]
    # 降順であること
    assert usage_counts == sorted(usage_counts, reverse=True)


def test_list_tags_namespace_filter(temp_db):
    """namespaceフィルタ: 指定namespaceのみ返る"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "scope:search"])

    result = list_tags(namespace="domain")
    assert "error" not in result
    for t in result["tags"]:
        assert t["namespace"] == "domain"


def test_list_tags_namespace_filter_scope(temp_db):
    """namespaceフィルタ: scopeで絞り込み"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "scope:search"])

    result = list_tags(namespace="scope")
    assert "error" not in result
    assert len(result["tags"]) >= 1
    for t in result["tags"]:
        assert t["namespace"] == "scope"


def test_list_tags_namespace_filter_nonexistent(temp_db):
    """存在しないnamespaceで空配列"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test"])

    result = list_tags(namespace="nonexistent")
    assert "error" not in result
    assert result["tags"] == []


def test_list_tags_no_namespace_filter(temp_db):
    """namespace未指定: 全タグを返す"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "scope:search"])

    result = list_tags()
    assert "error" not in result
    namespaces = {t["namespace"] for t in result["tags"]}
    # domain と scope と (domain:defaultの) domain が含まれる
    assert "domain" in namespaces


def test_list_tags_response_format(temp_db):
    """レスポンスの各タグのフォーマット確認"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test"])

    result = list_tags()
    assert "error" not in result
    assert len(result["tags"]) >= 1
    tag = result["tags"][0]
    assert "tag" in tag
    assert "id" in tag
    assert "namespace" in tag
    assert "name" in tag
    assert "usage_count" in tag
    assert isinstance(tag["id"], int)
    assert isinstance(tag["usage_count"], int)


def test_list_tags_bare_tag(temp_db):
    """namespaceなしの素タグも返る"""
    add_topic(title="Topic 1", description="Desc", tags=["baretag"])

    result = list_tags()
    assert "error" not in result
    bare = next((t for t in result["tags"] if t["tag"] == "baretag"), None)
    assert bare is not None
    assert bare["namespace"] == ""
    assert bare["name"] == "baretag"


def test_list_tags_bare_tag_namespace_filter(temp_db):
    """namespace=""でフィルタすると素タグのみ返る"""
    add_topic(title="Topic 1", description="Desc", tags=["domain:test", "baretag"])

    result = list_tags(namespace="")
    assert "error" not in result
    for t in result["tags"]:
        assert t["namespace"] == ""


def test_list_tags_empty_db(temp_db):
    """初期状態: init_databaseのdomain:defaultのみ"""
    result = list_tags()
    assert "error" not in result
    assert len(result["tags"]) >= 1
    tag_strs = [t["tag"] for t in result["tags"]]
    assert "domain:default" in tag_strs
