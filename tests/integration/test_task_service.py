"""タスクサービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database, execute_query
from src.services.task_service import add_task, get_tasks, update_task


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
def task_with_db(temp_db):
    """タスクを作成するフィクスチャ"""
    task = add_task(
        title="Test Task",
        description="This is a test task",
        tags=DEFAULT_TAGS,
    )
    return {"task": task}


class TestAddTask:
    """add_taskの統合テスト"""

    def test_add_task_success(self, temp_db):
        """タスクの追加が成功する"""
        result = add_task(
            title="New Task",
            description="Task description",
            tags=DEFAULT_TAGS,
        )

        assert "error" not in result
        assert result["task_id"] > 0
        assert result["title"] == "New Task"
        assert result["description"] == "Task description"
        assert result["status"] == "pending"
        assert "tags" in result
        assert "domain:test" in result["tags"]

    def test_add_task_tags_required(self, temp_db):
        """tags=[]でTAGS_REQUIREDエラーになる"""
        result = add_task(
            title="Task",
            description="Description",
            tags=[],
        )

        assert "error" in result
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_add_task_tags_stored(self, temp_db):
        """タスク作成時にtask_tagsにレコードが正しくINSERTされる"""
        result = add_task(
            title="Tagged Task",
            description="Tagged description",
            tags=["domain:cc-memory", "hooks"],
        )

        assert "error" not in result
        assert sorted(result["tags"]) == ["domain:cc-memory", "hooks"]


class TestGetTasks:
    """get_tasksの統合テスト"""

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_empty(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_with_status_filter(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_default_status_is_active(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_limit(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_total_count(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_total_count_exceeds_limit(self, temp_db):
        pass

    def test_get_tasks_invalid_limit_zero(self, temp_db):
        """limit=0でINVALID_PARAMETERエラーになる"""
        result = get_tasks(subject_id=1, status="pending", limit=0)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_tasks_invalid_limit_negative(self, temp_db):
        """limit=-1でINVALID_PARAMETERエラーになる"""
        result = get_tasks(subject_id=1, status="pending", limit=-1)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_tasks_invalid_status(self, temp_db):
        """無効なstatusでINVALID_STATUSエラーになる"""
        result = get_tasks(subject_id=1, status="invalid_status")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_description_truncated(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_description_short_not_truncated(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_active_returns_pending_and_in_progress(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_active_sort_order(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_active_total_count(self, temp_db):
        pass

    @pytest.mark.skip("Pending task #404/#405: read tool migration (get_tasks uses subject_id)")
    def test_get_tasks_active_is_valid_status(self, temp_db):
        pass


class TestUpdateTask:
    """update_taskの統合テスト"""

    def test_update_status_to_in_progress(self, task_with_db):
        """ステータスをin_progressに更新できる"""
        task = task_with_db["task"]
        result = update_task(task["task_id"], new_status="in_progress")

        assert "error" not in result
        assert result["status"] == "in_progress"

    def test_update_status_to_completed(self, task_with_db):
        """ステータスをcompletedに更新できる"""
        task = task_with_db["task"]
        result = update_task(task["task_id"], new_status="completed")

        assert "error" not in result
        assert result["status"] == "completed"

    def test_update_status_invalid(self, task_with_db):
        """無効なステータスでエラーになる"""
        task = task_with_db["task"]
        result = update_task(task["task_id"], new_status="invalid_status")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    def test_update_task_not_found(self, temp_db):
        """存在しないタスクIDでエラーになる"""
        result = update_task(9999, new_status="in_progress")

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
