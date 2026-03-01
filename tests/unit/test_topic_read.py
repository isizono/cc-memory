"""トピック管理API（読み取り系）のテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.subject_service import add_subject
from src.services.topic_service import (
    add_topic,
    get_topics,
)
from src.services.discussion_log_service import add_log, get_logs
from src.services.decision_service import add_decision, get_decisions


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


@pytest.fixture
def test_subject(temp_db):
    """テスト用サブジェクトを作成する"""
    result = add_subject(name="test-subject", description="Test subject")
    return result["subject_id"]


# ========================================
# get-topics のテスト
# ========================================


def test_get_topics_empty(test_subject):
    """トピックが存在しない場合、空の配列が返る"""
    result = get_topics(subject_id=test_subject)

    assert "error" not in result
    assert result["topics"] == []
    assert result["total_count"] == 0


def test_get_topics_desc_order(test_subject):
    """新しいトピックが先頭に来る（DESC順）"""
    topic1 = add_topic(subject_id=test_subject, title="Topic 1", description="Desc 1")
    topic2 = add_topic(subject_id=test_subject, title="Topic 2", description="Desc 2")
    topic3 = add_topic(subject_id=test_subject, title="Topic 3", description="Desc 3")

    result = get_topics(subject_id=test_subject)

    assert "error" not in result
    assert len(result["topics"]) == 3
    # DESC順: 新しいものが先頭
    assert result["topics"][0]["id"] == topic3["topic_id"]
    assert result["topics"][1]["id"] == topic2["topic_id"]
    assert result["topics"][2]["id"] == topic1["topic_id"]
    assert result["total_count"] == 3


def test_get_topics_pagination(test_subject):
    """ページネーションで取得できる"""
    topics = []
    for i in range(5):
        t = add_topic(subject_id=test_subject, title=f"Topic {i}", description=f"Desc {i}")
        topics.append(t)

    # limit=3, offset=0 で最新3件
    result1 = get_topics(subject_id=test_subject, limit=3, offset=0)
    assert len(result1["topics"]) == 3
    assert result1["topics"][0]["id"] == topics[4]["topic_id"]
    assert result1["topics"][1]["id"] == topics[3]["topic_id"]
    assert result1["topics"][2]["id"] == topics[2]["topic_id"]
    assert result1["total_count"] == 5

    # offset=3 で次の2件
    result2 = get_topics(subject_id=test_subject, limit=3, offset=3)
    assert len(result2["topics"]) == 2
    assert result2["topics"][0]["id"] == topics[1]["topic_id"]
    assert result2["topics"][1]["id"] == topics[0]["topic_id"]
    assert result2["total_count"] == 5


def test_get_topics_offset_beyond_total(test_subject):
    """offset >= total_count の場合、空配列でtotal_countは正確な値"""
    add_topic(subject_id=test_subject, title="Topic 1", description="Desc")

    result = get_topics(subject_id=test_subject, offset=100)
    assert result["topics"] == []
    assert result["total_count"] == 1


def test_get_topics_invalid_limit(test_subject):
    """limit < 1 の場合、エラーを返す"""
    result = get_topics(subject_id=test_subject, limit=0)
    assert "error" in result
    assert result["error"]["code"] == "INVALID_PARAMETER"


def test_get_topics_invalid_offset(test_subject):
    """offset < 0 の場合、エラーを返す"""
    result = get_topics(subject_id=test_subject, offset=-1)
    assert "error" in result
    assert result["error"]["code"] == "INVALID_PARAMETER"


def test_get_topics_ancestors_root(test_subject):
    """ルートトピックのancestorsは空配列"""
    add_topic(subject_id=test_subject, title="Root", description="Root topic")

    result = get_topics(subject_id=test_subject)
    assert result["topics"][0]["ancestors"] == []


def test_get_topics_ancestors_3_levels(test_subject):
    """親子3段のトピックでancestorsが[{親}, {祖父}]になる"""
    grandparent = add_topic(subject_id=test_subject, title="Grandparent", description="GP")
    parent = add_topic(
        subject_id=test_subject, title="Parent", description="P",
        parent_topic_id=grandparent["topic_id"],
    )
    child = add_topic(
        subject_id=test_subject, title="Child", description="C",
        parent_topic_id=parent["topic_id"],
    )

    result = get_topics(subject_id=test_subject)

    # 各トピックを取得
    child_topic = next(t for t in result["topics"] if t["id"] == child["topic_id"])
    parent_topic = next(t for t in result["topics"] if t["id"] == parent["topic_id"])
    gp_topic = next(t for t in result["topics"] if t["id"] == grandparent["topic_id"])

    # childのancestors: [親, 祖父]
    assert len(child_topic["ancestors"]) == 2
    assert child_topic["ancestors"][0]["id"] == parent["topic_id"]
    assert child_topic["ancestors"][0]["title"] == "Parent"
    assert child_topic["ancestors"][1]["id"] == grandparent["topic_id"]
    assert child_topic["ancestors"][1]["title"] == "Grandparent"

    # parentのancestors: [祖父]
    assert len(parent_topic["ancestors"]) == 1
    assert parent_topic["ancestors"][0]["id"] == grandparent["topic_id"]

    # grandparentのancestors: []
    assert gp_topic["ancestors"] == []


def test_get_topics_no_parent_topic_id_field(test_subject):
    """レスポンスにparent_topic_idフィールドが含まれない"""
    add_topic(subject_id=test_subject, title="Topic", description="Desc")

    result = get_topics(subject_id=test_subject)
    assert "parent_topic_id" not in result["topics"][0]


# ========================================
# get-logs のテスト
# ========================================


def test_get_logs_empty(test_subject):
    """ログが存在しない場合、空の配列が返る"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")
    result = get_logs(topic_id=topic["topic_id"])

    assert "error" not in result
    assert result["logs"] == []


def test_get_logs_multiple(test_subject):
    """複数のログを取得できる"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")

    # 3つのログを追加
    log1 = add_log(topic_id=topic["topic_id"], content="Log 1")
    log2 = add_log(topic_id=topic["topic_id"], content="Log 2")
    log3 = add_log(topic_id=topic["topic_id"], content="Log 3")

    result = get_logs(topic_id=topic["topic_id"])

    assert "error" not in result
    assert len(result["logs"]) == 3
    assert result["logs"][0]["id"] == log1["log_id"]
    assert result["logs"][0]["content"] == "Log 1"
    assert result["logs"][1]["id"] == log2["log_id"]
    assert result["logs"][2]["id"] == log3["log_id"]


def test_get_logs_with_pagination(test_subject):
    """ページネーションで取得できる"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")

    # 5つのログを追加
    logs = []
    for i in range(5):
        log = add_log(topic_id=topic["topic_id"], content=f"Log {i}")
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


def test_get_decisions_empty(test_subject):
    """決定事項が存在しない場合、空の配列が返る"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")
    result = get_decisions(topic_id=topic["topic_id"])

    assert "error" not in result
    assert result["decisions"] == []


def test_get_decisions_multiple(test_subject):
    """複数の決定事項を取得できる"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")

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


def test_get_decisions_with_pagination(test_subject):
    """ページネーションで取得できる"""
    topic = add_topic(subject_id=test_subject, title="Topic", description="Test description")

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
