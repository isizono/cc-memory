"""pin_service のテスト

エンティティ（decision, log, material）のpin/unpin操作と
バリデーションエラーをカバーする。
"""
import os
import tempfile
import pytest

from src.db import init_database, get_connection
from src.services.topic_service import add_topic
from src.services.discussion_log_service import add_logs
from src.services.decision_service import add_decisions
from src.services.material_service import add_material
from src.services.pin_service import update_pin
from src.services.tag_service import _injected_tags


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        _injected_tags.clear()
        yield db_path
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


@pytest.fixture
def topic(temp_db):
    """テスト用トピックを作成する"""
    return add_topic(title="テストトピック", description="テスト用", tags=DEFAULT_TAGS)


class TestPinDecision:
    """decisionのpin/unpin"""

    def test_pin_decision(self, topic):
        """decisionをpinできる"""
        tid = topic["topic_id"]
        result = add_decisions([
            {"topic_id": tid, "decision": "テスト決定", "reason": "テスト理由"},
        ])
        decision_id = result["created"][0]["decision_id"]

        pin_result = update_pin("decision", decision_id, True)
        assert "error" not in pin_result
        assert pin_result["entity_type"] == "decision"
        assert pin_result["entity_id"] == decision_id
        assert pin_result["pinned"] is True

        # DB上でもpinned=1であることを確認
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            assert row["pinned"] == 1
        finally:
            conn.close()

    def test_unpin_decision(self, topic):
        """decisionをunpinできる"""
        tid = topic["topic_id"]
        result = add_decisions([
            {"topic_id": tid, "decision": "テスト決定", "reason": "テスト理由"},
        ])
        decision_id = result["created"][0]["decision_id"]

        # pin → unpin
        update_pin("decision", decision_id, True)
        unpin_result = update_pin("decision", decision_id, False)

        assert "error" not in unpin_result
        assert unpin_result["pinned"] is False

        # DB上でもpinned=0であることを確認
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            assert row["pinned"] == 0
        finally:
            conn.close()


class TestPinLog:
    """logのpin/unpin"""

    def test_pin_log(self, topic):
        """logをpinできる"""
        tid = topic["topic_id"]
        result = add_logs([
            {"topic_id": tid, "content": "テストログ内容", "title": "テストログ"},
        ])
        log_id = result["created"][0]["log_id"]

        pin_result = update_pin("log", log_id, True)
        assert "error" not in pin_result
        assert pin_result["entity_type"] == "log"
        assert pin_result["entity_id"] == log_id
        assert pin_result["pinned"] is True

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM discussion_logs WHERE id = ?", (log_id,)
            ).fetchone()
            assert row["pinned"] == 1
        finally:
            conn.close()

    def test_unpin_log(self, topic):
        """logをunpinできる"""
        tid = topic["topic_id"]
        result = add_logs([
            {"topic_id": tid, "content": "テストログ内容", "title": "テストログ"},
        ])
        log_id = result["created"][0]["log_id"]

        update_pin("log", log_id, True)
        unpin_result = update_pin("log", log_id, False)

        assert "error" not in unpin_result
        assert unpin_result["pinned"] is False

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM discussion_logs WHERE id = ?", (log_id,)
            ).fetchone()
            assert row["pinned"] == 0
        finally:
            conn.close()


class TestPinMaterial:
    """materialのpin/unpin"""

    def test_pin_material(self, temp_db):
        """materialをpinできる"""
        result = add_material(
            title="テスト資材",
            content="テスト資材の内容",
            tags=DEFAULT_TAGS,
            source="テスト用データ",
        )
        material_id = result["material_id"]

        pin_result = update_pin("material", material_id, True)
        assert "error" not in pin_result
        assert pin_result["entity_type"] == "material"
        assert pin_result["entity_id"] == material_id
        assert pin_result["pinned"] is True

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            assert row["pinned"] == 1
        finally:
            conn.close()

    def test_unpin_material(self, temp_db):
        """materialをunpinできる"""
        result = add_material(
            title="テスト資材",
            content="テスト資材の内容",
            tags=DEFAULT_TAGS,
            source="テスト用データ",
        )
        material_id = result["material_id"]

        update_pin("material", material_id, True)
        unpin_result = update_pin("material", material_id, False)

        assert "error" not in unpin_result
        assert unpin_result["pinned"] is False

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pinned FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            assert row["pinned"] == 0
        finally:
            conn.close()


class TestPinIdempotent:
    """pin操作の冪等性"""

    def test_pin_twice_no_error(self, topic):
        """既にpinされた状態でpinしてもエラーにならない"""
        tid = topic["topic_id"]
        result = add_decisions([
            {"topic_id": tid, "decision": "テスト決定", "reason": "テスト理由"},
        ])
        decision_id = result["created"][0]["decision_id"]

        result1 = update_pin("decision", decision_id, True)
        result2 = update_pin("decision", decision_id, True)

        assert "error" not in result1
        assert "error" not in result2
        assert result2["pinned"] is True

    def test_unpin_twice_no_error(self, topic):
        """既にunpinされた状態でunpinしてもエラーにならない"""
        tid = topic["topic_id"]
        result = add_decisions([
            {"topic_id": tid, "decision": "テスト決定", "reason": "テスト理由"},
        ])
        decision_id = result["created"][0]["decision_id"]

        # デフォルトでpinned=0なので、unpinを2回呼ぶ
        result1 = update_pin("decision", decision_id, False)
        result2 = update_pin("decision", decision_id, False)

        assert "error" not in result1
        assert "error" not in result2
        assert result2["pinned"] is False


class TestPinValidationErrors:
    """バリデーションエラー"""

    def test_invalid_entity_type(self, temp_db):
        """不正なentity_typeでエラーが返る"""
        result = update_pin("topic", 1, True)

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "Invalid entity_type" in result["error"]["message"]

    def test_nonexistent_decision_id(self, topic):
        """存在しないdecision IDでエラーが返る"""
        result = update_pin("decision", 99999, True)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "decision" in result["error"]["message"]
        assert "99999" in result["error"]["message"]

    def test_nonexistent_log_id(self, topic):
        """存在しないlog IDでエラーが返る"""
        result = update_pin("log", 99999, True)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "log" in result["error"]["message"]

    def test_nonexistent_material_id(self, temp_db):
        """存在しないmaterial IDでエラーが返る"""
        result = update_pin("material", 99999, True)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "material" in result["error"]["message"]
