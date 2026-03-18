"""reminder_serviceのユニットテスト"""
import os
import tempfile
import pytest
from src.db import init_database
from src.services.reminder_service import add_reminder, list_reminders, update_reminder


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


class TestAddReminder:
    """add_reminderのテスト"""

    def test_add_reminder_success(self, temp_db):
        """リマインダーが正常に追加される"""
        result = add_reminder("テストリマインダー")

        assert "error" not in result
        assert result["reminder_id"] is not None

    def test_add_reminder_empty_content(self, temp_db):
        """空文字のcontentでバリデーションエラーになる"""
        result = add_reminder("")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "content" in result["error"]["message"]

    def test_add_reminder_whitespace_only(self, temp_db):
        """空白のみのcontentでバリデーションエラーになる"""
        result = add_reminder("   ")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_multiple_reminders(self, temp_db):
        """複数のリマインダーを追加できる"""
        result1 = add_reminder("リマインダー1")
        result2 = add_reminder("リマインダー2")

        assert "error" not in result1
        assert "error" not in result2
        assert result1["reminder_id"] != result2["reminder_id"]


class TestListReminders:
    """list_remindersのテスト"""

    def test_list_reminders_with_initial_data(self, temp_db):
        """マイグレーションで投入された初期データが含まれる"""
        result = list_reminders()

        assert "error" not in result
        assert result["total_count"] >= 1
        # 初期データの内容を確認
        contents = [r["content"] for r in result["reminders"]]
        assert any("IDを指示語代わりにしない" in c for c in contents)

    def test_list_reminders_after_add(self, temp_db):
        """追加したリマインダーが一覧に含まれる"""
        add_reminder("新しいリマインダー")

        result = list_reminders()

        assert "error" not in result
        contents = [r["content"] for r in result["reminders"]]
        assert "新しいリマインダー" in contents

    def test_list_reminders_order_by_id(self, temp_db):
        """リマインダーがID順にソートされている"""
        add_reminder("リマインダーA")
        add_reminder("リマインダーB")

        result = list_reminders()

        assert "error" not in result
        ids = [r["reminder_id"] for r in result["reminders"]]
        assert ids == sorted(ids)


class TestUpdateReminder:
    """update_reminderのテスト"""

    def test_update_content(self, temp_db):
        """contentを更新できる"""
        created = add_reminder("元のリマインダー")
        reminder_id = created["reminder_id"]

        result = update_reminder(reminder_id, content="更新後のリマインダー")

        assert "error" not in result
        assert result["reminder_id"] == reminder_id
        assert result["content"] == "更新後のリマインダー"
        assert result["active"] == 1

    def test_update_active_to_false(self, temp_db):
        """active=Falseで無効化できる"""
        created = add_reminder("無効化するリマインダー")
        reminder_id = created["reminder_id"]

        result = update_reminder(reminder_id, active=False)

        assert "error" not in result
        assert result["reminder_id"] == reminder_id
        assert result["active"] == 0

    def test_update_active_to_true(self, temp_db):
        """active=Trueで再有効化できる"""
        created = add_reminder("再有効化するリマインダー")
        reminder_id = created["reminder_id"]
        update_reminder(reminder_id, active=False)

        result = update_reminder(reminder_id, active=True)

        assert "error" not in result
        assert result["active"] == 1

    def test_update_both_content_and_active(self, temp_db):
        """contentとactiveを同時に更新できる"""
        created = add_reminder("元のリマインダー")
        reminder_id = created["reminder_id"]

        result = update_reminder(reminder_id, content="新しいリマインダー", active=False)

        assert "error" not in result
        assert result["content"] == "新しいリマインダー"
        assert result["active"] == 0

    def test_update_no_params(self, temp_db):
        """content/active両方未指定でバリデーションエラーになる"""
        result = update_reminder(1)

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "At least one" in result["error"]["message"]

    def test_update_not_found(self, temp_db):
        """存在しないIDでNOT_FOUNDエラーになる"""
        result = update_reminder(9999, content="存在しないリマインダー")

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "9999" in result["error"]["message"]

    def test_update_empty_content(self, temp_db):
        """空文字のcontentでバリデーションエラーになる"""
        created = add_reminder("元のリマインダー")
        reminder_id = created["reminder_id"]

        result = update_reminder(reminder_id, content="")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_update_invalid_active(self, temp_db):
        """非bool値でバリデーションエラーになる"""
        created = add_reminder("元のリマインダー")
        reminder_id = created["reminder_id"]

        result = update_reminder(reminder_id, active="invalid")

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert "active must be True or False" in result["error"]["message"]
