"""アクティビティサービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database, execute_query, get_connection
from src.services.activity_service import add_activity, get_activities, update_activity
from src.services.tag_service import _injected_tags
from src.services.activity_service import add_activity, get_activities, update_activity, ACTIVITY_DESC_MAX_LEN


DEFAULT_TAGS = ["domain:test"]


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()
        # tag_notes注入済みセットをリセット（テスト間の干渉防止）
        _injected_tags.clear()
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
        check_in=False,
    )
    return {"activity": activity}


class TestAddActivity:
    """add_activityの統合テスト"""

    def test_add_activity_success(self, temp_db):
        """アクティビティの追加が成功する（check_in=False）"""
        result = add_activity(
            title="New Activity",
            description="Activity description",
            tags=DEFAULT_TAGS,
            check_in=False,
        )

        assert "error" not in result
        assert result["activity_id"] > 0
        assert "check_in_result" not in result

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
            check_in=False,
        )

        assert "error" not in result
        assert result["activity_id"] > 0

        # DBで直接確認
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT t.namespace, t.name
                FROM tags t
                JOIN activity_tags at ON t.id = at.tag_id
                WHERE at.activity_id = ?
                ORDER BY t.namespace, t.name
                """,
                (result["activity_id"],),
            ).fetchall()
            tag_names = sorted(f"{r['namespace']}:{r['name']}" if r['namespace'] else r['name'] for r in rows)
            assert tag_names == ["domain:cc-memory", "hooks"]
        finally:
            conn.close()

    def test_add_activity_with_check_in_default(self, temp_db):
        """デフォルト（check_in=True）でcheck_in_resultが含まれる"""
        result = add_activity(
            title="Activity with check-in",
            description="Description",
            tags=DEFAULT_TAGS,
        )

        assert "error" not in result
        assert "check_in_result" in result
        check_in_result = result["check_in_result"]
        assert "error" not in check_in_result
        assert "activity" in check_in_result
        assert check_in_result["activity"]["status"] == "in_progress"
        assert "tag_notes" in check_in_result
        assert "summary" in check_in_result

    def test_add_activity_with_check_in_false(self, temp_db):
        """check_in=Falseで従来動作（ステータスpending、check_in_resultなし）"""
        result = add_activity(
            title="Activity no check-in",
            description="Description",
            tags=DEFAULT_TAGS,
            check_in=False,
        )

        assert "error" not in result
        assert "check_in_result" not in result

    def test_add_activity_with_related(self, temp_db):
        """related指定でアクティビティ作成時にリレーションが張られる"""
        # トピックを作成
        conn = get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO discussion_topics (title, description) VALUES (?, ?)",
                ("Test Topic", "Topic description"),
            )
            topic_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        result = add_activity(
            title="Activity with topic",
            description="Description",
            tags=DEFAULT_TAGS,
            related=[{"type": "topic", "ids": [topic_id]}],
            check_in=False,
        )

        assert "error" not in result
        assert result["activity_id"] > 0

        # topic_activity_relationsにリレーションが保存されていることを確認
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM topic_activity_relations WHERE topic_id = ? AND activity_id = ?",
                (topic_id, result["activity_id"]),
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_add_activity_without_related(self, temp_db):
        """related未指定でリレーションなしのアクティビティが作成される"""
        result = add_activity(
            title="Activity no topic",
            description="Description",
            tags=DEFAULT_TAGS,
            check_in=False,
        )

        assert "error" not in result

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM topic_activity_relations WHERE activity_id = ?",
                (result["activity_id"],),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_add_activity_with_related_and_check_in(self, temp_db):
        """related + check_in=Trueでrelated_topicsが取得される"""
        # トピックを作成
        conn = get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO discussion_topics (title, description) VALUES (?, ?)",
                ("Test Topic", "Topic description"),
            )
            topic_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        result = add_activity(
            title="Activity with topic and check-in",
            description="Description",
            tags=DEFAULT_TAGS,
            related=[{"type": "topic", "ids": [topic_id]}],
        )

        assert "error" not in result
        assert "check_in_result" in result
        check_in_result = result["check_in_result"]
        assert "error" not in check_in_result
        # related_topicsに関連トピックが含まれる
        assert "related_topics" in check_in_result
        assert any(t["id"] == topic_id for t in check_in_result["related_topics"])
        assert "recent_decisions" in check_in_result


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
        add_activity(title="Activity A", description="Desc A", tags=["domain:test"], check_in=False)
        add_activity(title="Activity B", description="Desc B", tags=["domain:other"], check_in=False)

        result = get_activities()

        assert "error" not in result
        assert result["total_count"] == 2
        titles = {t["title"] for t in result["activities"]}
        assert titles == {"Activity A", "Activity B"}

    def test_get_activities_no_tags_with_status_filter(self, temp_db):
        """tags未指定 + status指定で全ドメインからフィルタ"""
        activity_a = add_activity(title="Activity A", description="Desc", tags=["domain:test"], check_in=False)
        add_activity(title="Activity B", description="Desc", tags=["domain:other"], check_in=False)
        update_activity(activity_a["activity_id"], new_status="completed")

        result = get_activities(status="completed")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "Activity A"

    def test_get_activities_completed_sorted_by_updated_at_desc(self, temp_db):
        """completedのソート順がupdated_at DESCになっている"""
        a1 = add_activity(title="Old completed", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        a2 = add_activity(title="New completed", description="Desc", tags=DEFAULT_TAGS, check_in=False)
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
        a1 = add_activity(title="Old pending", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        a2 = add_activity(title="New pending", description="Desc", tags=DEFAULT_TAGS, check_in=False)

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

    def test_get_activities_since_filter(self, temp_db):
        """since指定でupdated_at以降のアクティビティのみ返す"""
        add_activity(title="Old Activity", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_activity(title="New Activity", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2026-01-01 00:00:00' WHERE title = 'Old Activity'"
        )
        conn.execute(
            "UPDATE activities SET updated_at = '2026-03-15 00:00:00' WHERE title = 'New Activity'"
        )
        conn.commit()
        conn.close()

        result = get_activities(tags=DEFAULT_TAGS, since="2026-03-01")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "New Activity"

    def test_get_activities_until_filter(self, temp_db):
        """until指定でupdated_at以前のアクティビティのみ返す"""
        add_activity(title="Old Activity", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_activity(title="New Activity", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2026-01-01 00:00:00' WHERE title = 'Old Activity'"
        )
        conn.execute(
            "UPDATE activities SET updated_at = '2026-03-15 00:00:00' WHERE title = 'New Activity'"
        )
        conn.commit()
        conn.close()

        result = get_activities(tags=DEFAULT_TAGS, until="2026-02-01")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "Old Activity"

    def test_get_activities_since_and_until_combined(self, temp_db):
        """since+until指定で範囲内のアクティビティのみ返す"""
        add_activity(title="Jan", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_activity(title="Feb", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_activity(title="Mar", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        conn.execute("UPDATE activities SET updated_at = '2026-01-15 00:00:00' WHERE title = 'Jan'")
        conn.execute("UPDATE activities SET updated_at = '2026-02-15 00:00:00' WHERE title = 'Feb'")
        conn.execute("UPDATE activities SET updated_at = '2026-03-15 00:00:00' WHERE title = 'Mar'")
        conn.commit()
        conn.close()

        result = get_activities(tags=DEFAULT_TAGS, since="2026-02-01", until="2026-02-28")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "Feb"

    def test_get_activities_since_with_status_filter(self, temp_db):
        """since + status組み合わせ"""
        a1 = add_activity(title="Old Completed", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        a2 = add_activity(title="New Pending", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        update_activity(a1["activity_id"], new_status="completed")

        # update_activityが内部でupdated_at=CURRENT_TIMESTAMPを設定するため、
        # テスト用に手動で上書きしてsince条件の検証を可能にする
        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2026-01-01 00:00:00' WHERE id = ?",
            (a1["activity_id"],),
        )
        conn.execute(
            "UPDATE activities SET updated_at = '2026-03-15 00:00:00' WHERE id = ?",
            (a2["activity_id"],),
        )
        conn.commit()
        conn.close()

        result = get_activities(status="completed", since="2026-03-01")

        assert "error" not in result
        assert result["total_count"] == 0
        assert result["activities"] == []

    def test_get_activities_until_includes_same_day(self, temp_db):
        """until指定日と同日のレコードが含まれる（境界テスト）"""
        add_activity(title="Same Day", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        conn.execute(
            "UPDATE activities SET updated_at = '2026-03-15 18:00:00' WHERE title = 'Same Day'"
        )
        conn.commit()
        conn.close()

        result = get_activities(tags=DEFAULT_TAGS, until="2026-03-15")

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["activities"][0]["title"] == "Same Day"

    def test_get_activities_invalid_since_format(self, temp_db):
        """不正なsince形式でINVALID_PARAMETERエラー"""
        result = get_activities(tags=DEFAULT_TAGS, since="not-a-date")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_activities_invalid_until_format(self, temp_db):
        """不正なuntil形式でINVALID_PARAMETERエラー"""
        result = get_activities(tags=DEFAULT_TAGS, until="2026/03/15")

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

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
