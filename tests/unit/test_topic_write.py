"""トピック管理API（書き込み系）のテスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.subject_service import add_subject
from src.services.topic_service import add_topic
from src.services.discussion_log_service import add_log
from src.services.decision_service import add_decision
from src.services.search_service import search


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


def test_add_topic_success(test_subject):
    """トピックの追加が成功する"""
    result = add_topic(
        subject_id=test_subject,
        title="開発フローの詳細",
        description="プランモードの使い方、タスク分解の粒度を決定する",
    )

    assert "error" not in result
    assert result["topic_id"] > 0
    assert result["subject_id"] == test_subject
    assert result["title"] == "開発フローの詳細"
    assert result["description"] == "プランモードの使い方、タスク分解の粒度を決定する"
    assert result["parent_topic_id"] is None
    assert "created_at" in result


def test_add_topic_with_parent(test_subject):
    """親トピックを指定してトピックを追加できる"""
    # 親トピックを作成
    parent = add_topic(subject_id=test_subject, title="親トピック", description="Test description")

    # 子トピックを作成
    result = add_topic(
        subject_id=test_subject,
        title="子トピック",
        description="Test description",
        parent_topic_id=parent["topic_id"],
    )

    assert "error" not in result
    assert result["parent_topic_id"] == parent["topic_id"]


def test_add_log_success(test_subject):
    """議論ログの追加が成功する"""
    # トピックを作成
    topic = add_topic(subject_id=test_subject, title="テストトピック", description="Test description")

    # ログを追加
    result = add_log(
        topic_id=topic["topic_id"],
        title="プランモードの議論",
        content="AI: プランモードは設計議論フェーズでは不要だと考えます\nユーザー：同意します。",
    )

    assert "error" not in result
    assert result["log_id"] > 0
    assert result["topic_id"] == topic["topic_id"]
    assert result["title"] == "プランモードの議論"
    assert "AI: プランモードは設計議論フェーズでは不要だと考えます" in result["content"]
    assert "created_at" in result


def test_add_log_multiple(test_subject):
    """同じトピックに複数のログを追加できる"""
    topic = add_topic(subject_id=test_subject, title="テストトピック", description="Test description")

    # 3つのログを追加
    log1 = add_log(topic_id=topic["topic_id"], title="タイトル1", content="ログ1")
    log2 = add_log(topic_id=topic["topic_id"], title="タイトル2", content="ログ2")
    log3 = add_log(topic_id=topic["topic_id"], title="タイトル3", content="ログ3")

    assert "error" not in log1
    assert "error" not in log2
    assert "error" not in log3
    assert log1["log_id"] != log2["log_id"] != log3["log_id"]


def test_add_log_invalid_topic(test_subject):
    """存在しないトピックIDでエラーになる"""
    result = add_log(topic_id=99999, title="test title", content="test")

    assert "error" in result
    assert result["error"]["code"] == "CONSTRAINT_VIOLATION"


def test_add_decision_success(test_subject):
    """決定事項の追加が成功する"""
    # トピックを作成
    topic = add_topic(subject_id=test_subject, title="テストトピック", description="Test description")

    # 決定事項を追加
    result = add_decision(
        topic_id=topic["topic_id"],
        decision="設計議論フェーズではプランモード不要。",
        reason="設計議論では自由に発散→収束させたい。",
    )

    assert "error" not in result
    assert result["decision_id"] > 0
    assert result["topic_id"] == topic["topic_id"]
    assert result["decision"] == "設計議論フェーズではプランモード不要。"
    assert result["reason"] == "設計議論では自由に発散→収束させたい。"
    assert "created_at" in result


def test_add_decision_without_topic(temp_db):
    """topic_id=Noneで決定事項を追加するとCONSTRAINT_VIOLATIONが返る"""
    result = add_decision(
        decision="グローバルな決定事項",
        reason="サブジェクト全体に関わる",
        topic_id=None,
    )

    assert "error" in result
    assert result["error"]["code"] == "CONSTRAINT_VIOLATION"


def test_add_decision_multiple(test_subject):
    """複数の決定事項を追加できる"""
    topic = add_topic(subject_id=test_subject, title="テストトピック", description="Test description")

    dec1 = add_decision(
        topic_id=topic["topic_id"],
        decision="決定1",
        reason="理由1",
    )
    dec2 = add_decision(
        topic_id=topic["topic_id"],
        decision="決定2",
        reason="理由2",
    )

    assert "error" not in dec1
    assert "error" not in dec2
    assert dec1["decision_id"] != dec2["decision_id"]


# ========================================
# ON DELETE CASCADE のテスト
# ========================================


def _delete_topic(topic_id: int) -> None:
    """テスト用: トピックを直接SQLで削除するヘルパー"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM discussion_topics WHERE id = ?", (topic_id,))
        conn.commit()
    finally:
        conn.close()


def _count_decisions(topic_id: int) -> int:
    """テスト用: 指定トピックのdecisions件数を返すヘルパー"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE topic_id = ?", (topic_id,)
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


def _count_logs(topic_id: int) -> int:
    """テスト用: 指定トピックのdiscussion_logs件数を返すヘルパー"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM discussion_logs WHERE topic_id = ?", (topic_id,)
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


def test_on_delete_cascade_decisions(test_subject):
    """トピック削除時にdecisionsがカスケード削除される"""
    topic = add_topic(
        subject_id=test_subject,
        title="カスケードテストトピック",
        description="ON DELETE CASCADEの動作確認",
    )
    topic_id = topic["topic_id"]

    # decisionsを2件追加
    add_decision(
        topic_id=topic_id,
        decision="カスケードテスト決定1",
        reason="テスト理由1",
    )
    add_decision(
        topic_id=topic_id,
        decision="カスケードテスト決定2",
        reason="テスト理由2",
    )

    # 削除前に2件あることを確認
    assert _count_decisions(topic_id) == 2

    # トピックを削除
    _delete_topic(topic_id)

    # decisionsがカスケード削除されて0件になることを確認
    assert _count_decisions(topic_id) == 0


def test_on_delete_cascade_discussion_logs(test_subject):
    """トピック削除時にdiscussion_logsがカスケード削除される"""
    topic = add_topic(
        subject_id=test_subject,
        title="ログカスケードテストトピック",
        description="discussion_logsのON DELETE CASCADE確認",
    )
    topic_id = topic["topic_id"]

    # discussion_logsを3件追加
    add_log(topic_id=topic_id, title="カスケード1", content="ログ1: カスケードテスト")
    add_log(topic_id=topic_id, title="カスケード2", content="ログ2: カスケードテスト")
    add_log(topic_id=topic_id, title="カスケード3", content="ログ3: カスケードテスト")

    # 削除前に3件あることを確認
    assert _count_logs(topic_id) == 3

    # トピックを削除
    _delete_topic(topic_id)

    # discussion_logsがカスケード削除されて0件になることを確認
    assert _count_logs(topic_id) == 0


def test_on_delete_cascade_decisions_fts5_sync(test_subject):
    """トピック削除時にdecisionsのFTS5インデックスもカスケード削除される"""
    topic = add_topic(
        subject_id=test_subject,
        title="FTS5カスケードテストトピック",
        description="FTS5インデックスのカスケード削除確認",
    )
    topic_id = topic["topic_id"]

    # decisionを追加（FTS5トリガーでsearch_indexに登録される）
    add_decision(
        topic_id=topic_id,
        decision="FTS5カスケード削除テスト決定事項",
        reason="FTS5インデックスのカスケード削除を確認する",
    )

    # 追加直後に検索で見つかることを確認
    result_before = search(subject_id=test_subject, keyword="FTS5カスケード削除テスト決定事項")
    assert "error" not in result_before
    decision_results_before = [
        r for r in result_before["results"] if r["type"] == "decision"
    ]
    assert len(decision_results_before) == 1

    # トピックを削除（decisionsもカスケード削除 → FTS5トリガーが発火してsearch_indexも削除）
    _delete_topic(topic_id)

    # FTS5インデックスからも削除されていることを確認
    result_after = search(subject_id=test_subject, keyword="FTS5カスケード削除テスト決定事項")
    assert "error" not in result_after
    decision_results_after = [
        r for r in result_after["results"] if r["type"] == "decision"
    ]
    assert len(decision_results_after) == 0
