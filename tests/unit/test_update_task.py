"""update_taskのユニットテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.task_service import add_task, update_task


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
def test_task(temp_db):
    """テスト用タスクを作成する"""
    result = add_task(
        title="Original Title",
        description="Original Description",
        tags=DEFAULT_TAGS,
    )
    return result


# ========================================
# 正常系テスト
# ========================================


class TestUpdateTaskSuccess:
    """update_taskの正常系テスト"""

    def test_update_status(self, test_task):
        """ステータスのみ変更できる"""
        result = update_task(test_task["task_id"], new_status="in_progress")

        assert "error" not in result
        assert result["status"] == "in_progress"
        assert result["title"] == "Original Title"
        assert result["description"] == "Original Description"

    def test_update_title(self, test_task):
        """タイトルのみ変更できる"""
        result = update_task(test_task["task_id"], title="New Title")

        assert "error" not in result
        assert result["title"] == "New Title"
        assert result["status"] == "pending"
        assert result["description"] == "Original Description"

    def test_update_description(self, test_task):
        """説明のみ変更できる"""
        result = update_task(test_task["task_id"], description="New Description")

        assert "error" not in result
        assert result["description"] == "New Description"
        assert result["title"] == "Original Title"
        assert result["status"] == "pending"

    def test_update_multiple_fields(self, test_task):
        """複数フィールドを同時に変更できる"""
        result = update_task(
            test_task["task_id"],
            new_status="in_progress",
            title="Updated Title",
            description="Updated Description",
        )

        assert "error" not in result
        assert result["status"] == "in_progress"
        assert result["title"] == "Updated Title"
        assert result["description"] == "Updated Description"

    def test_update_preserves_tags(self, test_task):
        """update_taskでタグが保持される"""
        result = update_task(test_task["task_id"], new_status="in_progress")

        assert "error" not in result
        assert "tags" in result
        assert "domain:test" in result["tags"]


# ========================================
# 異常系テスト
# ========================================


class TestUpdateTaskError:
    """update_taskの異常系テスト"""

    def test_all_none_returns_validation_error(self, test_task):
        """全パラメータがNoneだとVALIDATION_ERRORになる"""
        result = update_task(test_task["task_id"])

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_not_found(self, temp_db):
        """存在しないタスクIDでNOT_FOUNDになる"""
        result = update_task(9999, new_status="in_progress")

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    def test_invalid_status(self, test_task):
        """無効なステータスでINVALID_STATUSになる"""
        result = update_task(test_task["task_id"], new_status="invalid")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    def test_active_status_rejected(self, test_task):
        """activeはget_tasks用エイリアスであり、update_taskでは無効"""
        result = update_task(test_task["task_id"], new_status="active")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    def test_empty_title(self, test_task):
        """空文字のtitleでVALIDATION_ERRORになる"""
        result = update_task(test_task["task_id"], title="")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "title" in result["error"]["message"]

    def test_whitespace_title(self, test_task):
        """空白のみのtitleでVALIDATION_ERRORになる"""
        result = update_task(test_task["task_id"], title="   ")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "title" in result["error"]["message"]

    def test_empty_description(self, test_task):
        """空文字のdescriptionでVALIDATION_ERRORになる"""
        result = update_task(test_task["task_id"], description="")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "description" in result["error"]["message"]

    def test_whitespace_description(self, test_task):
        """空白のみのdescriptionでVALIDATION_ERRORになる"""
        result = update_task(test_task["task_id"], description="   ")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "description" in result["error"]["message"]

    def test_blocked_status_rejected(self, test_task):
        """blockedステータスがINVALID_STATUSになる"""
        result = update_task(test_task["task_id"], new_status="blocked")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"


# ========================================
# タグ更新テスト
# ========================================


class TestUpdateTaskTags:
    """update_taskのタグ更新テスト"""

    def test_update_tags(self, test_task):
        """タグ全置換"""
        result = update_task(test_task["task_id"], tags=["scope:search", "domain:cc-memory"])

        assert "error" not in result
        assert "tags" in result
        assert "scope:search" in result["tags"]
        assert "domain:cc-memory" in result["tags"]
        # 旧タグは除去されている
        assert "domain:test" not in result["tags"]

    def test_update_tags_empty_list(self, test_task):
        """tags=[]でTAGS_REQUIREDエラー"""
        result = update_task(test_task["task_id"], tags=[])

        assert "error" in result
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_update_tags_none(self, test_task):
        """tags=None（未指定）ではタグ変更なし"""
        # まずステータスだけ変更
        result = update_task(test_task["task_id"], new_status="in_progress")

        assert "error" not in result
        assert result["tags"] == ["domain:test"]  # 元のタグが保持される
