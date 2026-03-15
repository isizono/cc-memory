"""リレーションサービスの統合テスト"""
import os
import tempfile

import pytest

from src.db import get_connection, init_database
from src.services.relation_service import add_relation, get_map, remove_relation
from src.services.tag_service import _injected_tags


DEFAULT_TAGS = [("domain", "test")]


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


def _create_topic(conn, title="Test Topic"):
    """テスト用トピックを作成する"""
    from src.services.tag_service import ensure_tag_ids, link_tags

    cursor = conn.execute(
        "INSERT INTO discussion_topics (title, description) VALUES (?, ?)",
        (title, f"Description for {title}"),
    )
    topic_id = cursor.lastrowid
    tag_ids = ensure_tag_ids(conn, DEFAULT_TAGS)
    link_tags(conn, "topic_tags", "topic_id", topic_id, tag_ids)
    return topic_id


def _create_activity(conn, title="Test Activity"):
    """テスト用アクティビティを作成する"""
    from src.services.tag_service import ensure_tag_ids, link_tags

    cursor = conn.execute(
        "INSERT INTO activities (title, description, status) VALUES (?, ?, ?)",
        (title, f"Description for {title}", "pending"),
    )
    activity_id = cursor.lastrowid
    tag_ids = ensure_tag_ids(conn, DEFAULT_TAGS)
    link_tags(conn, "activity_tags", "activity_id", activity_id, tag_ids)
    return activity_id


@pytest.fixture
def sample_entities(temp_db):
    """テスト用のトピックとアクティビティを作成する"""
    conn = get_connection()
    try:
        t1 = _create_topic(conn, "Topic A")
        t2 = _create_topic(conn, "Topic B")
        t3 = _create_topic(conn, "Topic C")
        a1 = _create_activity(conn, "Activity X")
        a2 = _create_activity(conn, "Activity Y")
        a3 = _create_activity(conn, "Activity Z")
        conn.commit()
    finally:
        conn.close()
    return {"t1": t1, "t2": t2, "t3": t3, "a1": a1, "a2": a2, "a3": a3}


class TestAddRelation:
    """add_relationの統合テスト"""

    def test_add_topic_topic_relation(self, sample_entities):
        """topic↔topicリレーションが追加できる"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        assert "error" not in result
        assert result["added"] == 1

    def test_add_topic_activity_relation(self, sample_entities):
        """topic↔activityリレーションが追加できる"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        assert "error" not in result
        assert result["added"] == 1

    def test_add_activity_topic_relation(self, sample_entities):
        """activity→topicリレーション（逆方向指定）が追加できる"""
        e = sample_entities
        result = add_relation("activity", e["a1"], [{"type": "topic", "ids": [e["t1"]]}])

        assert "error" not in result
        assert result["added"] == 1

        # topic_activity_relationsに正しく格納されていることを確認
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM topic_activity_relations WHERE topic_id = ? AND activity_id = ?",
                (e["t1"], e["a1"]),
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_add_activity_activity_relation(self, sample_entities):
        """activity↔activityリレーションが追加できる"""
        e = sample_entities
        result = add_relation("activity", e["a1"], [{"type": "activity", "ids": [e["a2"]]}])

        assert "error" not in result
        assert result["added"] == 1

    def test_add_multiple_targets(self, sample_entities):
        """複数ターゲットを一度に追加できる"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [
            {"type": "topic", "ids": [e["t2"], e["t3"]]},
            {"type": "activity", "ids": [e["a1"]]},
        ])

        assert "error" not in result
        assert result["added"] == 3

    def test_add_relation_idempotent(self, sample_entities):
        """重複追加が冪等（エラーにならず、added=0）"""
        e = sample_entities
        result1 = add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])
        assert result1["added"] == 1

        result2 = add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])
        assert "error" not in result2
        assert result2["added"] == 0

    def test_add_relation_self_reference_skipped(self, sample_entities):
        """自己参照はスキップされる（エラーにならない）"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t1"]]}])

        assert "error" not in result
        assert result["added"] == 0

    def test_add_relation_invalid_source_type(self, sample_entities):
        """不正なsource_typeでエラー"""
        e = sample_entities
        result = add_relation("invalid", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        assert "error" in result
        assert result["error"]["code"] == "INVALID_ENTITY_TYPE"

    def test_add_relation_invalid_target_type(self, sample_entities):
        """不正なtarget_typeでエラー"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [{"type": "invalid", "ids": [e["t2"]]}])

        assert "error" in result
        assert result["error"]["code"] == "INVALID_ENTITY_TYPE"

    def test_add_relation_empty_targets(self, sample_entities):
        """空のtargetsでエラー"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [])

        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    def test_add_relation_nonexistent_id_returns_error(self, sample_entities):
        """存在しないIDへのリレーション追加はFK違反でエラー"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [{"type": "topic", "ids": [99999]}])

        assert "error" in result
        assert result["error"]["code"] == "CONSTRAINT_VIOLATION"

    def test_add_relation_partial_fk_violation_rolls_back(self, sample_entities):
        """複数targets指定時にFK違反が起きると全体がロールバックされる"""
        e = sample_entities
        result = add_relation("topic", e["t1"], [
            {"type": "topic", "ids": [e["t2"], 99999]},
        ])

        assert "error" in result

        # t2へのリレーションもロールバックされている
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM topic_relations"
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_add_relation_normalizes_order(self, sample_entities):
        """topic_id_1 < topic_id_2に正規化される"""
        e = sample_entities
        # t2 > t1 のはずなので、t2からt1への追加でもt1,t2の順に格納される
        result = add_relation("topic", e["t2"], [{"type": "topic", "ids": [e["t1"]]}])
        assert result["added"] == 1

        # 正規化済みの順で格納されていることを確認
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM topic_relations WHERE topic_id_1 = ? AND topic_id_2 = ?",
                (min(e["t1"], e["t2"]), max(e["t1"], e["t2"])),
            ).fetchone()
            assert row is not None
        finally:
            conn.close()


class TestRemoveRelation:
    """remove_relationの統合テスト"""

    def test_remove_relation_success(self, sample_entities):
        """リレーション削除が正常に動作する"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        result = remove_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        assert "error" not in result
        assert result["removed"] == 1

    def test_remove_nonexistent_relation(self, sample_entities):
        """存在しないリレーションの削除がエラーにならない"""
        e = sample_entities
        result = remove_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        assert "error" not in result
        assert result["removed"] == 0

    def test_remove_topic_activity_relation(self, sample_entities):
        """topic↔activityリレーション削除が動作する"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        result = remove_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        assert "error" not in result
        assert result["removed"] == 1

    def test_remove_activity_activity_relation(self, sample_entities):
        """activity↔activityリレーション削除が動作する"""
        e = sample_entities
        add_relation("activity", e["a1"], [{"type": "activity", "ids": [e["a2"]]}])

        result = remove_relation("activity", e["a1"], [{"type": "activity", "ids": [e["a2"]]}])

        assert "error" not in result
        assert result["removed"] == 1

    def test_remove_activity_topic_relation(self, sample_entities):
        """activity→topic方向のリレーション削除が動作する"""
        e = sample_entities
        add_relation("activity", e["a1"], [{"type": "topic", "ids": [e["t1"]]}])

        result = remove_relation("activity", e["a1"], [{"type": "topic", "ids": [e["t1"]]}])

        assert "error" not in result
        assert result["removed"] == 1

    def test_remove_multiple_targets(self, sample_entities):
        """複数ターゲットの一括削除"""
        e = sample_entities
        add_relation("topic", e["t1"], [
            {"type": "topic", "ids": [e["t2"], e["t3"]]},
        ])

        result = remove_relation("topic", e["t1"], [
            {"type": "topic", "ids": [e["t2"], e["t3"]]},
        ])

        assert "error" not in result
        assert result["removed"] == 2


class TestGetMap:
    """get_mapの統合テスト"""

    def test_get_map_depth_0_returns_origin(self, sample_entities):
        """depth=0で起点のみ返る"""
        e = sample_entities
        # リレーションを追加（depth=0テストでは結果に含まれない想定）
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=0)

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["entities"][0]["type"] == "topic"
        assert result["entities"][0]["id"] == e["t1"]
        assert result["entities"][0]["depth"] == 0

    def test_get_map_depth_1_returns_direct_relations(self, sample_entities):
        """depth=1で直接関連が返る"""
        e = sample_entities
        add_relation("topic", e["t1"], [
            {"type": "topic", "ids": [e["t2"]]},
            {"type": "activity", "ids": [e["a1"]]},
        ])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=1)

        assert "error" not in result
        # 起点(t1) + t2 + a1 = 3エンティティ
        assert result["total_count"] == 3
        types_ids = {(ent["type"], ent["id"]) for ent in result["entities"]}
        assert ("topic", e["t1"]) in types_ids
        assert ("topic", e["t2"]) in types_ids
        assert ("activity", e["a1"]) in types_ids

    def test_get_map_min_depth_filters(self, sample_entities):
        """min_depthで起点を除外できる"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        result = get_map("topic", e["t1"], min_depth=1, max_depth=1)

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["entities"][0]["id"] == e["t2"]

    def test_get_map_transitive_depth_2(self, sample_entities):
        """depth=2で間接関連が返る"""
        e = sample_entities
        # t1 - t2 - t3 のチェーン
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])
        add_relation("topic", e["t2"], [{"type": "topic", "ids": [e["t3"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=2)

        assert "error" not in result
        # t1(0) + t2(1) + t3(2) = 3エンティティ
        assert result["total_count"] == 3
        types_ids = {(ent["type"], ent["id"]) for ent in result["entities"]}
        assert ("topic", e["t3"]) in types_ids

    def test_get_map_no_infinite_loop_on_cycle(self, sample_entities):
        """循環参照で無限ループしない"""
        e = sample_entities
        # t1 - t2 - t3 - t1 の循環
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])
        add_relation("topic", e["t2"], [{"type": "topic", "ids": [e["t3"]]}])
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t3"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=5)

        assert "error" not in result
        # 循環してもt1, t2, t3の3つしかない
        assert result["total_count"] == 3

    def test_get_map_returns_catalog_with_title_and_tags(self, sample_entities):
        """カタログにtitleとtagsが含まれる"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=1)

        assert "error" not in result
        for entity in result["entities"]:
            assert "title" in entity
            assert "tags" in entity
            assert isinstance(entity["tags"], list)
            assert entity["title"] != ""

    def test_get_map_invalid_entity_type(self, temp_db):
        """不正なentity_typeでエラー"""
        result = get_map("invalid", 1, min_depth=0, max_depth=1)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_ENTITY_TYPE"

    def test_get_map_invalid_min_depth(self, temp_db):
        """min_depth < 0でエラー"""
        result = get_map("topic", 1, min_depth=-1, max_depth=1)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_map_max_depth_less_than_min_depth(self, temp_db):
        """max_depth < min_depthでエラー"""
        result = get_map("topic", 1, min_depth=2, max_depth=1)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"

    def test_get_map_max_depth_exceeds_limit(self, temp_db):
        """max_depth > 10でエラー"""
        result = get_map("topic", 1, min_depth=0, max_depth=11)

        assert "error" in result
        assert result["error"]["code"] == "INVALID_PARAMETER"
        assert "10" in result["error"]["message"]

    def test_get_map_max_depth_at_limit(self, sample_entities):
        """max_depth=10は許可される"""
        e = sample_entities
        result = get_map("topic", e["t1"], min_depth=0, max_depth=10)

        assert "error" not in result

    def test_get_map_no_relations(self, sample_entities):
        """リレーションなしの場合、起点のみ返る"""
        e = sample_entities
        result = get_map("topic", e["t1"], min_depth=0, max_depth=2)

        assert "error" not in result
        assert result["total_count"] == 1
        assert result["entities"][0]["id"] == e["t1"]

    def test_get_map_nonexistent_id_returns_empty(self, temp_db):
        """存在しないIDのget_mapは空結果を返す"""
        result = get_map("topic", 99999, min_depth=0, max_depth=1)

        assert "error" not in result
        assert result["total_count"] == 0
        assert result["entities"] == []

    def test_get_map_multiple_paths_returns_min_depth(self, sample_entities):
        """同じエンティティに異なるdepthで到達する場合、MIN(depth)が返る"""
        e = sample_entities
        # t1 - t2 - t3 (depth 2 for t3)
        # t1 - t3        (depth 1 for t3)
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"], e["t3"]]}])
        add_relation("topic", e["t2"], [{"type": "topic", "ids": [e["t3"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=2)

        assert "error" not in result
        t3_entry = next(ent for ent in result["entities"] if ent["id"] == e["t3"])
        assert t3_entry["depth"] == 1  # 直接到達の方が浅い

    def test_get_map_sorted_by_depth(self, sample_entities):
        """結果がdepth順でソートされている"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])
        add_relation("topic", e["t2"], [{"type": "topic", "ids": [e["t3"]]}])

        result = get_map("topic", e["t1"], min_depth=0, max_depth=2)

        depths = [ent["depth"] for ent in result["entities"]]
        assert depths == sorted(depths)


class TestCascadeDelete:
    """ON DELETE CASCADEの動作テスト"""

    def test_cascade_delete_topic_cleans_topic_relations(self, sample_entities):
        """トピック削除時にtopic_relationsが消える"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "topic", "ids": [e["t2"]]}])

        conn = get_connection()
        try:
            conn.execute("DELETE FROM discussion_topics WHERE id = ?", (e["t1"],))
            conn.commit()

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM topic_relations WHERE topic_id_1 = ? OR topic_id_2 = ?",
                (e["t1"], e["t1"]),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_cascade_delete_topic_cleans_topic_activity_relations(self, sample_entities):
        """トピック削除時にtopic_activity_relationsが消える"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        conn = get_connection()
        try:
            conn.execute("DELETE FROM discussion_topics WHERE id = ?", (e["t1"],))
            conn.commit()

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM topic_activity_relations WHERE topic_id = ?",
                (e["t1"],),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_cascade_delete_activity_cleans_topic_activity_relations(self, sample_entities):
        """アクティビティ削除時にtopic_activity_relationsが消える"""
        e = sample_entities
        add_relation("topic", e["t1"], [{"type": "activity", "ids": [e["a1"]]}])

        conn = get_connection()
        try:
            conn.execute("DELETE FROM activities WHERE id = ?", (e["a1"],))
            conn.commit()

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM topic_activity_relations WHERE activity_id = ?",
                (e["a1"],),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_cascade_delete_activity_cleans_activity_relations(self, sample_entities):
        """アクティビティ削除時にactivity_relationsが消える"""
        e = sample_entities
        add_relation("activity", e["a1"], [{"type": "activity", "ids": [e["a2"]]}])

        conn = get_connection()
        try:
            conn.execute("DELETE FROM activities WHERE id = ?", (e["a1"],))
            conn.commit()

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM activity_relations WHERE activity_id_1 = ? OR activity_id_2 = ?",
                (e["a1"], e["a1"]),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()
