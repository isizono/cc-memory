"""アクティビティサービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database, execute_query, get_connection
from src.services.activity_service import add_activity, get_activities, update_activity, ACTIVITY_DESC_MAX_LEN


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
def activity_with_db(temp_db):
    """アクティビティを作成するフィクスチャ"""
    activity = add_activity(
        title="Test Activity",
        description="This is a test activity",
        tags=DEFAULT_TAGS,
    )
    return {"activity": activity}


class TestAddActivity:
    """add_activityの統合テスト"""

    def test_add_activity_success(self, temp_db):
        """アクティビティの追加が成功する"""
        result = add_activity(
            title="New Activity",
            description="Activity description",
            tags=DEFAULT_TAGS,
        )

        assert "error" not in result
        assert result["activity_id"] > 0
        assert result["title"] == "New Activity"
        assert result["description"] == "Activity description"
        assert result["status"] == "pending"
        assert "tags" in result
        assert "domain:test" in result["tags"]

    def test_add_activity_tags_required(self, temp_db):
        """tags=[]でTAGS_REQUIREDエラーになる"""
        result = add_activity(
            title="Activity",
            description="Description",
            tags=[],
        )

        assert "error" in result
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_add_activity_tags_stored(self, temp_db):
        """アクティビティ作成時にactivity_tagsにレコードが正しくINSERTされる"""
        result = add_activity(
            title="Tagged Activity",
            description="Tagged description",
            tags=["domain:cc-memory", "hooks"],
        )

        assert "error" not in result
        assert sorted(result["tags"]) == ["domain:cc-memory", "hooks"]


class TestGetActivities:
    """get_activitiesの統合テスト"""

    def test_get_activities_invalid_limit_zero(self, temp_db):
        """limit=0でINVALID_PARAMETERエラーになる"""
        result = get_activities(tags=DEFAULT_TAGS, status="pending", limit=0)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_activities_invalid_limit_negative(self, temp_db):
        """limit=-1でINVALID_PARAMETERエラーになる"""
        result = get_activities(tags=DEFAULT_TAGS, status="pending", limit=-1)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_activities_invalid_status(self, temp_db):
        """無効なstatusでINVALID_STATUSエラーになる"""
        result = get_activities(tags=DEFAULT_TAGS, status="invalid_status")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    def test_get_activities_no_tags_returns_all(self, temp_db):
        """tags未指定で全アクティビティを返す"""
        add_activity(title="Activity A", description="Desc A", tags=["domain:test"])
        add_activity(title="Activity B", description="Desc B", tags=["domain:other"])

        result = get_activities()

        assert "error" not in result
        assert result["total_count"] == 2
        titles = {t["title"] for t in result["activities"]}
        assert titles == {"Activity A", "Activity B"}

    def test_get_activities_no_tags_with_status_filter(self, temp_db):
        """tags未指定 + status指定で全ドメインからフィルタ"""
        activity_a = add_activity(title="Activity A", description="Desc", tags=["domain:test"])
        add_activity(title="Activity B", description="Desc", tags=["domain:other"])
        update_activity(activity_a["activity_id"], new_status="completed")

        result = get_activities(status="completed")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "Activity A"

    def test_get_activities_completed_sorted_by_updated_at_desc(self, temp_db):
        """completedのソート順がupdated_at DESCになっている"""
        a1 = add_activity(title="Old completed", description="Desc", tags=DEFAULT_TAGS)
        a2 = add_activity(title="New completed", description="Desc", tags=DEFAULT_TAGS)
        update_activity(a1["activity_id"], new_status="completed")
        update_activity(a2["activity_id"], new_status="completed")

        # a1のupdated_atを古い値に書き換えてソート順を明確にする
        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (a1["activity_id"],),
        )
        conn.commit()
        conn.close()

        result = get_activities(status="completed", limit=10)

        assert "error" not in result
        assert result["total_count"] == 2
        titles = [a["title"] for a in result["activities"]]
        assert titles == ["New completed", "Old completed"]

    def test_get_activities_pending_sorted_by_updated_at_desc(self, temp_db):
        """pendingのソート順がupdated_at DESCになっている"""
        a1 = add_activity(title="Old pending", description="Desc", tags=DEFAULT_TAGS)
        a2 = add_activity(title="New pending", description="Desc", tags=DEFAULT_TAGS)

        # a1のupdated_atを古い値に書き換えてソート順を明確にする
        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (a1["activity_id"],),
        )
        conn.commit()
        conn.close()

        result = get_activities(status="pending", limit=10)

        assert "error" not in result
        assert result["total_count"] == 2
        titles = [a["title"] for a in result["activities"]]
        assert titles == ["New pending", "Old pending"]

    def test_get_activities_truncates_description_at_max_len(self, temp_db):
        """descriptionがACTIVITY_DESC_MAX_LEN文字に切り詰められること"""
        long_desc = "a" * (ACTIVITY_DESC_MAX_LEN + 50)
        add_activity(title="Long Desc", description=long_desc, tags=["domain:test"])

        result = get_activities(tags=["domain:test"])

        assert "error" not in result
        activity = result["activities"][0]
        assert len(activity["description"]) == ACTIVITY_DESC_MAX_LEN


class TestUpdateActivity:
    """update_activityの統合テスト"""

    def test_update_status_to_in_progress(self, activity_with_db):
        """ステータスをin_progressに更新できる"""
        activity = activity_with_db["activity"]
        result = update_activity(activity["activity_id"], new_status="in_progress")

        assert "error" not in result
        assert result["status"] == "in_progress"

    def test_update_status_to_completed(self, activity_with_db):
        """ステータスをcompletedに更新できる"""
        activity = activity_with_db["activity"]
        result = update_activity(activity["activity_id"], new_status="completed")

        assert "error" not in result
        assert result["status"] == "completed"

    def test_update_status_invalid(self, activity_with_db):
        """無効なステータスでエラーになる"""
        activity = activity_with_db["activity"]
        result = update_activity(activity["activity_id"], new_status="invalid_status")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_STATUS"

    def test_update_activity_not_found(self, temp_db):
        """存在しないアクティビティIDでエラーになる"""
        result = update_activity(9999, new_status="in_progress")

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
