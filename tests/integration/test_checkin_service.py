"""check-inサービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.activity_service import add_activity, update_activity
from src.services.material_service import add_material
from src.services.checkin_service import check_in
from src.services.tag_service import _injected_tags


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
def activity_id(temp_db):
    """テスト用アクティビティを作成してIDを返すフィクスチャ"""
    result = add_activity(
        title="[作業] タグnotesカラム追加",
        description="タグnotesカラムを追加する作業",
        tags=DEFAULT_TAGS,
    )
    return result["activity_id"]


@pytest.fixture
def activity_with_intent(temp_db):
    """intent:タグ付きアクティビティを作成するフィクスチャ"""
    result = add_activity(
        title="[設計] API設計",
        description="APIの設計を行う",
        tags=["domain:test", "intent:design"],
    )
    return result["activity_id"]


class TestCheckIn:
    """check_inの統合テスト"""

    def test_check_in_success(self, activity_id):
        """check-inが成功し、必須フィールドがすべて返る"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "activity" in result
        assert result["activity"]["id"] == activity_id
        assert result["activity"]["title"] == "[作業] タグnotesカラム追加"
        assert result["activity"]["status"] == "in_progress"
        assert "tags" in result["activity"]
        assert "tag_notes" in result
        assert "materials" in result
        assert "recent_decisions" in result
        assert "summary" in result

    def test_check_in_status_updated_to_in_progress(self, activity_id):
        """pendingのアクティビティがin_progressに自動更新される"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["activity"]["status"] == "in_progress"

    def test_check_in_already_in_progress(self, activity_id):
        """すでにin_progressの場合、status変更なしでcheck-in成功"""
        # 先にin_progressに変更
        update_activity(activity_id, new_status="in_progress")

        result = check_in(activity_id)

        assert "error" not in result
        assert result["activity"]["status"] == "in_progress"

    def test_check_in_completed_activity(self, activity_id):
        """completedのアクティビティもin_progressに戻る"""
        update_activity(activity_id, new_status="completed")

        result = check_in(activity_id)

        assert "error" not in result
        assert result["activity"]["status"] == "in_progress"

    def test_check_in_not_found(self, temp_db):
        """存在しないactivity_idでNOT_FOUNDエラーになる"""
        result = check_in(9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "9999" in result["error"]["message"]

    def test_check_in_topic_omitted_when_no_topic_id(self, activity_id):
        """topic_idがない場合、topicフィールドが結果に含まれない"""
        result = check_in(activity_id)

        assert "error" not in result
        # activitiesテーブルにtopic_idカラムがないため、topicは省略される
        assert "topic" not in result

    def test_check_in_materials_empty(self, activity_id):
        """materials 0件の場合、空リストが返る"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["materials"] == []

    def test_check_in_with_materials(self, activity_id):
        """materialsがある場合、カタログ形式で返る"""
        add_material(activity_id, "設計書", "# 設計\n詳細内容")
        add_material(activity_id, "調査結果", "# 調査\n結果内容")

        result = check_in(activity_id)

        assert "error" not in result
        assert len(result["materials"]) == 2
        # カタログ形式: id, title, created_at のみ（contentなし）
        for m in result["materials"]:
            assert "id" in m
            assert "title" in m
            assert "created_at" in m
            assert "content" not in m

    def test_check_in_recent_decisions_empty_without_topic(self, activity_id):
        """topic_idがない場合、recent_decisionsは空リスト"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["recent_decisions"] == []


class TestCheckInSummary:
    """summary文字列のフォーマット確認"""

    def test_summary_format_basic(self, activity_id):
        """summaryが仕様のフォーマットに従っている"""
        result = check_in(activity_id)

        assert "error" not in result
        summary = result["summary"]
        lines = summary.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("check-in: ")
        assert "[作業] タグnotesカラム追加" in lines[0]
        assert "notes:" in lines[1]
        assert "intent:" in lines[1]
        assert "資材:" in lines[1]

    def test_summary_intent_from_tag(self, activity_with_intent):
        """intent:タグがある場合、summaryにintent値が表示される"""
        result = check_in(activity_with_intent)

        assert "error" not in result
        assert "intent: design" in result["summary"]

    def test_summary_intent_unset(self, activity_id):
        """intent:タグがない場合、(未設定)と表示される"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "intent: (未設定)" in result["summary"]

    def test_summary_materials_count(self, activity_id):
        """summaryに資材の件数が反映される"""
        add_material(activity_id, "資材1", "内容1")
        add_material(activity_id, "資材2", "内容2")
        add_material(activity_id, "資材3", "内容3")

        result = check_in(activity_id)

        assert "error" not in result
        assert "資材: 3件" in result["summary"]

    def test_summary_notes_count(self, temp_db):
        """tag_notesがある場合、summaryにnotes件数と行数が反映される"""
        # notesを持つタグを作成
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO tags (namespace, name, notes) VALUES (?, ?, ?)",
                ("domain", "noted", "教訓1行目\n教訓2行目\n教訓3行目"),
            )
            conn.commit()
        finally:
            conn.close()

        activity = add_activity(
            title="Test with notes",
            description="Desc",
            tags=["domain:noted"],
        )

        result = check_in(activity["activity_id"])

        assert "error" not in result
        assert "notes: 1件 (3行)" in result["summary"]


class TestCheckInTagNotes:
    """tag_notes注入の確認"""

    def test_tag_notes_injected(self, temp_db):
        """notesを持つタグがtag_notesに含まれる"""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO tags (namespace, name, notes) VALUES (?, ?, ?)",
                ("domain", "withnotes", "重要な教訓"),
            )
            conn.commit()
        finally:
            conn.close()

        activity = add_activity(
            title="Tag notes test",
            description="Desc",
            tags=["domain:withnotes"],
        )

        result = check_in(activity["activity_id"])

        assert "error" not in result
        assert len(result["tag_notes"]) == 1
        assert result["tag_notes"][0]["tag"] == "domain:withnotes"
        assert result["tag_notes"][0]["notes"] == "重要な教訓"

    def test_tag_notes_empty_when_no_notes(self, activity_id):
        """notesがないタグの場合、tag_notesは空リスト"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["tag_notes"] == []
