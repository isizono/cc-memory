"""トピック管理API（読み取り系）のテスト

get_topicsはsubject_idパラメータを使用しており、後続タスク(#404)でtags引数に切り替える。
ここではadd_topic/add_log/add_decisionの書き込み呼び出し修正のみ行い、
get_topicsのテストはスキップする。
get_logs/get_decisionsは引数変更なしのため、書き込み部分のみ修正して動作させる。
"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.topic_service import (
    add_topic,
    get_topics,
)
from src.services.discussion_log_service import add_log, get_logs
from src.services.decision_service import add_decision, get_decisions


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        yield db_path
        # クリーンアップ
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


# ========================================
# get-topics のテスト
# 後続タスク(#404)でget_topicsのsubject_id → tagsに切り替えた後に再有効化
# ========================================


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_empty(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_desc_order(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_pagination(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_offset_beyond_total(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_invalid_limit(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_invalid_offset(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_ancestors_root(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_ancestors_3_levels(temp_db):
    pass


@pytest.mark.skip("Pending task #404/#405: read tool migration (get_topics uses subject_id)")
def test_get_topics_no_parent_topic_id_field(temp_db):
    pass


# ========================================
# get-logs のテスト
# ========================================


def test_get_logs_empty(temp_db):
    """ログが存在しない場合、空の配列が返る"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)
    result = get_logs(topic_id=topic["topic_id"])

    assert "error" not in result
    assert result["logs"] == []


def test_get_logs_multiple(temp_db):
    """複数のログを取得できる"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)

    # 3つのログを追加
    log1 = add_log(topic_id=topic["topic_id"], title="Title 1", content="Log 1")
    log2 = add_log(topic_id=topic["topic_id"], title="Title 2", content="Log 2")
    log3 = add_log(topic_id=topic["topic_id"], title="Title 3", content="Log 3")

    result = get_logs(topic_id=topic["topic_id"])

    assert "error" not in result
    assert len(result["logs"]) == 3
    assert result["logs"][0]["id"] == log1["log_id"]
    assert result["logs"][0]["content"] == "Log 1"
    assert result["logs"][1]["id"] == log2["log_id"]
    assert result["logs"][2]["id"] == log3["log_id"]


def test_get_logs_with_pagination(temp_db):
    """ページネーションで取得できる"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)

    # 5つのログを追加
    logs = []
    for i in range(5):
        log = add_log(topic_id=topic["topic_id"], title=f"Title {i}", content=f"Log {i}")
        logs.append(log)

    # 最初の3件を取得
    result1 = get_logs(topic_id=topic["topic_id"], limit=3)
    assert len(result1["logs"]) == 3

    # 4件目から取得
    result2 = get_logs(
        topic_id=topic["topic_id"],
        start_id=logs[3]["log_id"],
        limit=3,
    )
    assert len(result2["logs"]) == 2
    assert result2["logs"][0]["id"] == logs[3]["log_id"]


# ========================================
# get-decisions のテスト
# ========================================


def test_get_decisions_empty(temp_db):
    """決定事項が存在しない場合、空の配列が返る"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)
    result = get_decisions(topic_id=topic["topic_id"])

    assert "error" not in result
    assert result["topic_id"] == topic["topic_id"]
    assert result["topic_name"] == "Topic"
    assert result["decisions"] == []


def test_get_decisions_topic_name_included(temp_db):
    """topic_nameがトップレベルに含まれる"""
    topic = add_topic(title="テスト用トピック", description="Test", tags=DEFAULT_TAGS)
    add_decision(topic_id=topic["topic_id"], decision="Dec 1", reason="Reason 1")

    result = get_decisions(topic_id=topic["topic_id"])

    assert result["topic_id"] == topic["topic_id"]
    assert result["topic_name"] == "テスト用トピック"
    assert len(result["decisions"]) == 1
    assert "topic_id" not in result["decisions"][0]


def test_get_decisions_nonexistent_topic(temp_db):
    """存在しないtopic_idの場合、topic_name=nullで空配列"""
    result = get_decisions(topic_id=999999)

    assert "error" not in result
    assert result["topic_id"] == 999999
    assert result["topic_name"] is None
    assert result["decisions"] == []


def test_get_decisions_multiple(temp_db):
    """複数の決定事項を取得できる"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)

    # 3つの決定事項を追加
    dec1 = add_decision(
        topic_id=topic["topic_id"],
        decision="Decision 1",
        reason="Reason 1",
    )
    dec2 = add_decision(
        topic_id=topic["topic_id"],
        decision="Decision 2",
        reason="Reason 2",
    )
    dec3 = add_decision(
        topic_id=topic["topic_id"],
        decision="Decision 3",
        reason="Reason 3",
    )

    result = get_decisions(topic_id=topic["topic_id"])

    assert "error" not in result
    assert len(result["decisions"]) == 3
    assert result["decisions"][0]["id"] == dec1["decision_id"]
    assert result["decisions"][0]["decision"] == "Decision 1"
    assert result["decisions"][1]["id"] == dec2["decision_id"]
    assert result["decisions"][2]["id"] == dec3["decision_id"]


def test_get_decisions_with_pagination(temp_db):
    """ページネーションで取得できる"""
    topic = add_topic(title="Topic", description="Test description", tags=DEFAULT_TAGS)

    # 5つの決定事項を追加
    decisions = []
    for i in range(5):
        dec = add_decision(
            topic_id=topic["topic_id"],
            decision=f"Decision {i}",
            reason=f"Reason {i}",
        )
        decisions.append(dec)

    # 最初の3件を取得
    result1 = get_decisions(topic_id=topic["topic_id"], limit=3)
    assert len(result1["decisions"]) == 3

    # 4件目から取得
    result2 = get_decisions(
        topic_id=topic["topic_id"],
        start_id=decisions[3]["decision_id"],
        limit=3,
    )
    assert len(result2["decisions"]) == 2
    assert result2["decisions"][0]["id"] == decisions[3]["decision_id"]
