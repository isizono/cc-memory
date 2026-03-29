"""check-inサービスの統合テスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.activity_service import add_activity, update_activity
from tests.helpers import add_decision, add_log
from src.services.material_service import add_material
from src.services.relation_service import add_relation
from src.services.topic_service import add_topic
from src.services.checkin_service import check_in, DECISIONS_FULL_LIMIT
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
        check_in=False,
    )
    return result["activity_id"]


@pytest.fixture
def activity_with_intent(temp_db):
    """intent:タグ付きアクティビティを作成するフィクスチャ"""
    result = add_activity(
        title="[設計] API設計",
        description="APIの設計を行う",
        tags=["domain:test", "intent:design"],
        check_in=False,
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
        assert result["activity"]["description"] == "タグnotesカラムを追加する作業"
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
        update_activity(activity_id, status="in_progress")

        result = check_in(activity_id)

        assert "error" not in result
        assert result["activity"]["status"] == "in_progress"

    def test_check_in_completed_activity(self, activity_id):
        """completedのアクティビティもin_progressに戻る"""
        update_activity(activity_id, status="completed")

        result = check_in(activity_id)

        assert "error" not in result
        assert result["activity"]["status"] == "in_progress"

    def test_check_in_not_found(self, temp_db):
        """存在しないactivity_idでNOT_FOUNDエラーになる"""
        result = check_in(9999)

        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"
        assert "9999" in result["error"]["message"]

    def test_check_in_no_related_topics_when_no_relations(self, activity_id):
        """リレーションがない場合、related_topicsが結果に含まれない"""
        result = check_in(activity_id)

        assert "error" not in result
        # リレーションが未設定のため、related_topicsは省略される
        assert "related_topics" not in result

    def test_check_in_materials_empty(self, activity_id):
        """materials 0件の場合、空リストが返る"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["materials"] == []

    def test_check_in_with_materials(self, activity_id):
        """materialsがある場合、activity_material_relations経由でカタログ形式で返る"""
        m1 = add_material("設計書", "# 設計\n詳細内容", ["domain:test"],
                          related=[{"type": "activity", "ids": [activity_id]}])
        m2 = add_material("調査結果", "# 調査\n結果内容", ["domain:test"],
                          related=[{"type": "activity", "ids": [activity_id]}])

        result = check_in(activity_id)

        assert "error" not in result
        assert len(result["materials"]) == 2
        # カタログ形式: id, title, snippet, created_at（contentなし）
        for m in result["materials"]:
            assert "id" in m
            assert "title" in m
            assert "snippet" in m
            assert "created_at" in m
            assert "content" not in m
        # snippetの値が正しい
        assert result["materials"][0]["snippet"] == "# 設計\n詳細内容"
        assert result["materials"][1]["snippet"] == "# 調査\n結果内容"

    def test_check_in_materials_snippet_truncated(self, activity_id):
        """materialsのsnippetが200文字に切り詰められる"""
        long_content = "あ" * 250
        add_material("長い資材", long_content, ["domain:test"],
                      related=[{"type": "activity", "ids": [activity_id]}])

        result = check_in(activity_id)

        assert "error" not in result
        assert len(result["materials"]) == 1
        assert len(result["materials"][0]["snippet"]) == 200
        assert result["materials"][0]["snippet"] == "あ" * 200

    def test_check_in_recent_decisions_empty_without_relations(self, activity_id):
        """リレーションがない場合、recent_decisionsは空リスト"""
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
        assert "intent:" in lines[1]

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
            check_in=False,
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

    def test_intent_tag_notes_injected_every_time(self, temp_db):
        """intent:タグのnotesは毎回注入される（常時注入）"""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE tags SET notes = ? WHERE namespace = 'intent' AND name = 'design'",
                ("設計の教訓",),
            )
            conn.commit()
        finally:
            conn.close()

        activity = add_activity(
            title="Design task",
            description="Desc",
            tags=["intent:design"],
            check_in=False,
        )
        aid = activity["activity_id"]

        # 1回目
        result1 = check_in(aid)
        assert "error" not in result1
        intent_notes1 = [n for n in result1["tag_notes"] if n["tag"] == "intent:design"]
        assert len(intent_notes1) == 1

        # 2回目: intent: は常時注入なので再度返る
        result2 = check_in(aid)
        assert "error" not in result2
        intent_notes2 = [n for n in result2["tag_notes"] if n["tag"] == "intent:design"]
        assert len(intent_notes2) == 1

    def test_non_intent_tag_notes_injected_once(self, temp_db):
        """intent:以外のタグのnotesはセッション初回のみ注入される"""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO tags (namespace, name, notes) VALUES (?, ?, ?)",
                ("domain", "once", "1回だけの教訓"),
            )
            conn.commit()
        finally:
            conn.close()

        activity = add_activity(
            title="Domain task",
            description="Desc",
            tags=["domain:once"],
            check_in=False,
        )
        aid = activity["activity_id"]

        # 1回目: 注入される
        result1 = check_in(aid)
        assert "error" not in result1
        domain_notes1 = [n for n in result1["tag_notes"] if n["tag"] == "domain:once"]
        assert len(domain_notes1) == 1

        # 2回目: domain: は通常タグなので注入されない
        result2 = check_in(aid)
        assert "error" not in result2
        domain_notes2 = [n for n in result2["tag_notes"] if n["tag"] == "domain:once"]
        assert len(domain_notes2) == 0



class TestCheckInRelations:
    """リレーション関連のcheck-inテスト"""

    def test_related_activities_returned(self, temp_db):
        """関連アクティビティがrelated_activitiesに含まれる"""
        a1 = add_activity(title="親タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        a2 = add_activity(title="子タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a1["activity_id"], [{"type": "activity", "ids": [a2["activity_id"]]}])

        result = check_in(a1["activity_id"])

        assert "error" not in result
        assert "related_activities" in result
        assert len(result["related_activities"]) == 1
        assert result["related_activities"][0]["id"] == a2["activity_id"]
        assert result["related_activities"][0]["title"] == "子タスク"

    def test_no_related_activities_key_when_empty(self, activity_id):
        """関連アクティビティがない場合、related_activitiesキーは省略される"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "related_activities" not in result

    def test_single_related_topic_sets_topic_key(self, temp_db):
        """関連トピックが1件の場合、topicキーにdictがセットされる"""
        topic = add_topic(title="テストトピック", description="Desc", tags=DEFAULT_TAGS)
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert "topic" in result
        assert result["topic"]["id"] == topic["topic_id"]
        assert result["related_topics"] == [result["topic"]]

    def test_multiple_related_topics_no_topic_key(self, temp_db):
        """関連トピックが複数の場合、topicキーは省略される"""
        t1 = add_topic(title="トピック1", description="Desc", tags=DEFAULT_TAGS)
        t2 = add_topic(title="トピック2", description="Desc", tags=DEFAULT_TAGS)
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [t1["topic_id"], t2["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert "topic" not in result
        assert len(result["related_topics"]) == 2

    def test_decisions_limited_to_max(self, temp_db):
        """decisionsがDECISIONS_FULL_LIMIT件に制限される"""
        topic = add_topic(title="決定多数トピック", description="Desc", tags=DEFAULT_TAGS)
        for i in range(DECISIONS_FULL_LIMIT + 5):
            add_decision(decision=f"決定事項{i}", reason="理由", topic_id=topic["topic_id"])
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert len(result["recent_decisions"]) == DECISIONS_FULL_LIMIT


class TestCheckInCoverage:
    """coverageフィールドのテスト"""

    def test_coverage_field_exists(self, activity_id):
        """coverageフィールドがトップレベルに含まれる"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "coverage" in result
        assert "decisions" in result["coverage"]
        assert "materials" in result["coverage"]
        assert "logs" in result["coverage"]

    def test_coverage_is_first_key(self, activity_id):
        """coverageがレスポンスの最初のキーである"""
        result = check_in(activity_id)

        assert "error" not in result
        keys = list(result.keys())
        assert keys[0] == "coverage"

    def test_coverage_no_relations_format(self, activity_id):
        """リレーションなしの場合、coverage分母は0"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["coverage"]["decisions"] == "0/0"
        assert result["coverage"]["materials"] == "0/0"
        assert result["coverage"]["logs"] == "0/0"

    def test_coverage_with_decisions(self, temp_db):
        """decisionsがある場合、coverageの分母に件数が反映される"""
        topic = add_topic(title="トピック", description="Desc", tags=DEFAULT_TAGS)
        for i in range(3):
            add_decision(decision=f"決定{i}", reason="理由", topic_id=topic["topic_id"])
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        # 分子: min(3, DECISIONS_FULL_LIMIT) = 3, 分母: 3
        assert result["coverage"]["decisions"] == "3/3"

    def test_coverage_decisions_exceeds_limit(self, temp_db):
        """decisions総数がDECISIONS_FULL_LIMITを超えた場合、分子は制限値になる"""
        topic = add_topic(title="トピック", description="Desc", tags=DEFAULT_TAGS)
        total = DECISIONS_FULL_LIMIT + 5
        for i in range(total):
            add_decision(decision=f"決定{i}", reason="理由", topic_id=topic["topic_id"])
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert result["coverage"]["decisions"] == f"{DECISIONS_FULL_LIMIT}/{total}"

    def test_coverage_with_materials(self, activity_id):
        """materialsがある場合、coverageの分母に件数が反映される"""
        add_material("資材1", "内容1", DEFAULT_TAGS, related=[{"type": "activity", "ids": [activity_id]}])
        add_material("資材2", "内容2", DEFAULT_TAGS, related=[{"type": "activity", "ids": [activity_id]}])

        result = check_in(activity_id)

        assert "error" not in result
        assert result["coverage"]["materials"] == "2/2"

    def test_coverage_logs_always_zero_numerator(self, temp_db):
        """logsの分子は常に0（本文を含めないため）"""
        topic = add_topic(title="トピック", description="Desc", tags=DEFAULT_TAGS)
        for i in range(3):
            add_log(topic_id=topic["topic_id"], title=f"ログ{i}", content=f"内容{i}")
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert result["coverage"]["logs"] == "0/3"

    def test_coverage_zero_related_topics(self, activity_id):
        """関連topic 0件の場合、coverage "0/0"が返る（Edge case）"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["coverage"]["decisions"] == "0/0"
        assert result["coverage"]["materials"] == "0/0"
        assert result["coverage"]["logs"] == "0/0"


class TestCheckInLogsCatalog:
    """logsカタログのテスト"""

    def test_logs_field_exists(self, activity_id):
        """logsフィールドが常に存在する"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "logs" in result

    def test_logs_empty_without_relations(self, activity_id):
        """リレーションなしの場合、logsは空リスト"""
        result = check_in(activity_id)

        assert "error" not in result
        assert result["logs"] == []

    def test_logs_catalog_id_and_title_only(self, temp_db):
        """logsカタログはid + titleのみ（contentなし）"""
        topic = add_topic(title="トピック", description="Desc", tags=DEFAULT_TAGS)
        add_log(topic_id=topic["topic_id"], title="初回議論", content="詳細な内容")
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [topic["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert len(result["logs"]) == 1
        assert "id" in result["logs"][0]
        assert "title" in result["logs"][0]
        assert "content" not in result["logs"][0]
        assert result["logs"][0]["title"] == "初回議論"

    def test_logs_catalog_multiple_topics(self, temp_db):
        """複数topicのlogsがカタログに集約される"""
        t1 = add_topic(title="トピック1", description="Desc", tags=DEFAULT_TAGS)
        t2 = add_topic(title="トピック2", description="Desc", tags=DEFAULT_TAGS)
        add_log(topic_id=t1["topic_id"], title="ログA", content="内容A")
        add_log(topic_id=t2["topic_id"], title="ログB", content="内容B")
        a = add_activity(title="タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        add_relation("activity", a["activity_id"], [{"type": "topic", "ids": [t1["topic_id"], t2["topic_id"]]}])

        result = check_in(a["activity_id"])

        assert "error" not in result
        assert len(result["logs"]) == 2
        titles = {l["title"] for l in result["logs"]}
        assert "ログA" in titles
        assert "ログB" in titles


class TestCheckInDependencies:
    """check-in結果のdependenciesフィールドのテスト"""

    def test_dependencies_present_when_depends_on_exists(self, temp_db):
        """depends_on関係がある場合、dependenciesフィールドが結果に含まれる"""
        dep = add_activity(title="依存先タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        main = add_activity(title="メインタスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
                (main["activity_id"], dep["activity_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        result = check_in(main["activity_id"])

        assert "error" not in result
        assert "dependencies" in result
        assert len(result["dependencies"]) == 1
        assert result["dependencies"][0]["id"] == dep["activity_id"]
        assert result["dependencies"][0]["title"] == "依存先タスク"
        assert result["dependencies"][0]["status"] == "pending"

    def test_dependencies_absent_when_no_depends_on(self, activity_id):
        """depends_on関係がない場合、dependenciesフィールドは省略される"""
        result = check_in(activity_id)

        assert "error" not in result
        assert "dependencies" not in result

    def test_dependencies_multiple(self, temp_db):
        """複数の依存先がある場合、全件がdependenciesに含まれる"""
        dep1 = add_activity(title="依存先1", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        dep2 = add_activity(title="依存先2", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        main = add_activity(title="メインタスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
                (main["activity_id"], dep1["activity_id"]),
            )
            conn.execute(
                "INSERT INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
                (main["activity_id"], dep2["activity_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        result = check_in(main["activity_id"])

        assert "error" not in result
        assert len(result["dependencies"]) == 2
        dep_ids = {d["id"] for d in result["dependencies"]}
        assert dep1["activity_id"] in dep_ids
        assert dep2["activity_id"] in dep_ids

    def test_dependencies_includes_completed(self, temp_db):
        """completedの依存先もdependenciesに含まれる（状態情報として有用）"""
        dep = add_activity(title="完了済み依存先", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        update_activity(dep["activity_id"], status="completed")
        main = add_activity(title="メインタスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
                (main["activity_id"], dep["activity_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        result = check_in(main["activity_id"])

        assert "error" not in result
        assert "dependencies" in result
        assert result["dependencies"][0]["status"] == "completed"

    def test_dependencies_status_reflects_current(self, temp_db):
        """dependenciesの各要素のstatusがDB上の最新値を反映する"""
        dep = add_activity(title="進行中タスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)
        update_activity(dep["activity_id"], status="in_progress")
        main = add_activity(title="メインタスク", description="Desc", tags=DEFAULT_TAGS, check_in=False)

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO activity_dependencies (dependent_id, dependency_id) VALUES (?, ?)",
                (main["activity_id"], dep["activity_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        result = check_in(main["activity_id"])

        assert "error" not in result
        assert result["dependencies"][0]["status"] == "in_progress"
