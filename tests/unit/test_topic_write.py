"""トピック管理API（書き込み系）のテスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.topic_service import add_topic
from src.services.discussion_log_service import add_log
from src.services.decision_service import add_decision


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


DEFAULT_TAGS = ["domain:test"]


def test_add_topic_success(temp_db):
    """トピックの追加が成功する"""
    result = add_topic(
        title="開発フローの詳細",
        description="プランモードの使い方、タスク分解の粒度を決定する",
        tags=DEFAULT_TAGS,
    )

    assert "error" not in result
    assert result["topic_id"] > 0
    assert result["title"] == "開発フローの詳細"
    assert result["description"] == "プランモードの使い方、タスク分解の粒度を決定する"
    assert "tags" in result
    assert "domain:test" in result["tags"]
    assert "created_at" in result


def test_add_topic_tags_stored(temp_db):
    """トピック作成時にtopic_tagsにレコードが正しくINSERTされる"""
    result = add_topic(
        title="タグテスト",
        description="タグの永続化テスト",
        tags=["domain:cc-memory", "hooks", "scope:search"],
    )

    assert "error" not in result
    assert sorted(result["tags"]) == ["domain:cc-memory", "hooks", "scope:search"]

    # DBで直接確認
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.namespace, t.name
            FROM tags t
            JOIN topic_tags tt ON t.id = tt.tag_id
            WHERE tt.topic_id = ?
            ORDER BY t.namespace, t.name
            """,
            (result["topic_id"],),
        ).fetchall()
        assert len(rows) == 3
    finally:
        conn.close()


def test_add_topic_tags_required(temp_db):
    """tags=[]でTAGS_REQUIREDエラーが返る"""
    result = add_topic(
        title="空タグ",
        description="タグなし",
        tags=[],
    )

    assert "error" in result
    assert result["error"]["code"] == "TAGS_REQUIRED"


def test_add_topic_invalid_namespace(temp_db):
    """不正なnamespaceでINVALID_TAG_NAMESPACEエラーが返る"""
    result = add_topic(
        title="不正NS",
        description="不正なnamespace",
        tags=["invalid:tag"],
    )

    assert "error" in result
    assert result["error"]["code"] == "INVALID_TAG_NAMESPACE"


def test_add_topic_duplicate_tags(temp_db):
    """重複タグは静かに排除される"""
    result = add_topic(
        title="重複タグ",
        description="重複テスト",
        tags=["domain:test", "domain:test", "hooks", "hooks"],
    )

    assert "error" not in result
    assert sorted(result["tags"]) == ["domain:test", "hooks"]


def test_add_topic_empty_string_tags(temp_db):
    """空文字タグはスキップされる"""
    result = add_topic(
        title="空文字タグ",
        description="空文字テスト",
        tags=["domain:test", "", "  ", "hooks"],
    )

    assert "error" not in result
    assert sorted(result["tags"]) == ["domain:test", "hooks"]


def test_add_topic_empty_string_tags_only(temp_db):
    """空文字タグのみの場合、TAGS_REQUIREDエラーになる"""
    result = add_topic(
        title="空文字のみ",
        description="全部空文字",
        tags=["", "  "],
    )

    assert "error" in result
    assert result["error"]["code"] == "TAGS_REQUIRED"


def test_add_log_success(temp_db):
    """議論ログの追加が成功する"""
    # トピックを作成
    topic = add_topic(title="テストトピック", description="Test description", tags=DEFAULT_TAGS)

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
    assert "tags" in result
    # tagsはtopicから継承
    assert "domain:test" in result["tags"]
    assert "created_at" in result


def test_add_log_with_tags(temp_db):
    """議論ログに追加タグを指定できる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=["domain:test"])

    result = add_log(
        topic_id=topic["topic_id"],
        title="追加タグテスト",
        content="タグ追加のテスト",
        tags=["extra-tag"],
    )

    assert "error" not in result
    # topic_tags UNION log_tags
    assert "domain:test" in result["tags"]
    assert "extra-tag" in result["tags"]


def test_add_log_without_tags(temp_db):
    """tags=NoneでtopicのタグのみがUNIONされる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=["domain:test", "hooks"])

    result = add_log(
        topic_id=topic["topic_id"],
        title="タグなし",
        content="タグを指定しない",
    )

    assert "error" not in result
    assert sorted(result["tags"]) == sorted(topic["tags"])


def test_add_log_multiple(temp_db):
    """同じトピックに複数のログを追加できる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=DEFAULT_TAGS)

    # 3つのログを追加
    log1 = add_log(topic_id=topic["topic_id"], title="タイトル1", content="ログ1")
    log2 = add_log(topic_id=topic["topic_id"], title="タイトル2", content="ログ2")
    log3 = add_log(topic_id=topic["topic_id"], title="タイトル3", content="ログ3")

    assert "error" not in log1
    assert "error" not in log2
    assert "error" not in log3
    assert log1["log_id"] != log2["log_id"] != log3["log_id"]


def test_add_log_invalid_topic(temp_db):
    """存在しないトピックIDでエラーになる"""
    result = add_log(topic_id=99999, title="test title", content="test")

    assert "error" in result
    assert result["error"]["code"] == "CONSTRAINT_VIOLATION"


def test_add_decision_success(temp_db):
    """決定事項の追加が成功する"""
    # トピックを作成
    topic = add_topic(title="テストトピック", description="Test description", tags=DEFAULT_TAGS)

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
    assert "tags" in result
    # tagsはtopicから継承
    assert "domain:test" in result["tags"]
    assert "created_at" in result


def test_add_decision_with_tags(temp_db):
    """決定事項に追加タグを指定できる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=["domain:test"])

    result = add_decision(
        topic_id=topic["topic_id"],
        decision="タグ付き決定",
        reason="追加タグのテスト",
        tags=["extra"],
    )

    assert "error" not in result
    # topic_tags UNION decision_tags
    assert "domain:test" in result["tags"]
    assert "extra" in result["tags"]


def test_add_decision_without_tags(temp_db):
    """tags=NoneでtopicのタグのみがUNIONされる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=["domain:test", "scope:api"])

    result = add_decision(
        topic_id=topic["topic_id"],
        decision="タグなし決定",
        reason="タグを指定しない",
    )

    assert "error" not in result
    assert sorted(result["tags"]) == sorted(topic["tags"])


def test_add_decision_without_topic(temp_db):
    """topic_id=Noneで決定事項を追加するとCONSTRAINT_VIOLATIONが返る"""
    result = add_decision(
        decision="グローバルな決定事項",
        reason="サブジェクト全体に関わる",
        topic_id=None,
    )

    assert "error" in result
    assert result["error"]["code"] == "CONSTRAINT_VIOLATION"


def test_add_decision_multiple(temp_db):
    """複数の決定事項を追加できる"""
    topic = add_topic(title="テストトピック", description="Test description", tags=DEFAULT_TAGS)

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


def test_on_delete_cascade_decisions(temp_db):
    """トピック削除時にdecisionsがカスケード削除される"""
    topic = add_topic(
        title="カスケードテストトピック",
        description="ON DELETE CASCADEの動作確認",
        tags=DEFAULT_TAGS,
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


def test_on_delete_cascade_discussion_logs(temp_db):
    """トピック削除時にdiscussion_logsがカスケード削除される"""
    topic = add_topic(
        title="ログカスケードテストトピック",
        description="discussion_logsのON DELETE CASCADE確認",
        tags=DEFAULT_TAGS,
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


