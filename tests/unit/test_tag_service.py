"""タグユーティリティのユニットテスト"""
import os
import tempfile
import pytest
from src.db import init_database, get_connection
from src.services.tag_service import (
    parse_tag,
    validate_and_parse_tags,
    ensure_tag_ids,
    link_tags,
    format_tags,
    get_entity_tags,
    get_effective_tags,
)


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


class MockRow:
    """sqlite3.Rowのモック（key-based access対応）"""
    def __init__(self, ns, name):
        self._data = {"namespace": ns, "name": name}
    def __getitem__(self, key):
        return self._data[key]


# ========================================
# parse_tag テスト
# ========================================


class TestParseTag:
    """parse_tagのテスト"""

    def test_namespace_with_name(self):
        """namespace付きタグをパースできる"""
        assert parse_tag("domain:cc-memory") == ("domain", "cc-memory")

    def test_bare_tag(self):
        """素タグをパースできる"""
        assert parse_tag("hooks") == ("", "hooks")

    def test_mode_namespace(self):
        """mode namespaceをパースできる"""
        assert parse_tag("mode:design") == ("mode", "design")

    def test_scope_namespace(self):
        """scope namespaceをパースできる"""
        assert parse_tag("scope:parent-topic") == ("scope", "parent-topic")

    def test_colon_in_name(self):
        """nameにコロンが含まれる場合、最初のコロンで分割"""
        assert parse_tag("domain:cc:memory") == ("domain", "cc:memory")

    def test_empty_colon(self):
        """コロンだけの場合"""
        assert parse_tag(":value") == ("", "value")


# ========================================
# validate_and_parse_tags テスト
# ========================================


class TestValidateAndParseTags:
    """validate_and_parse_tagsのテスト"""

    def test_valid_tags(self):
        """正常なタグ配列をパースできる"""
        result = validate_and_parse_tags(["domain:cc-memory", "hooks", "mode:design"])
        assert isinstance(result, list)
        assert len(result) == 3
        assert ("domain", "cc-memory") in result
        assert ("", "hooks") in result
        assert ("mode", "design") in result

    def test_deduplicate(self):
        """重複タグを排除する"""
        result = validate_and_parse_tags(["domain:test", "domain:test", "hooks", "hooks"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_skip_empty_strings(self):
        """空文字タグをスキップする"""
        result = validate_and_parse_tags(["domain:test", "", "  ", "hooks"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_required_empty_list(self):
        """required=Trueで空配列はTAGS_REQUIREDエラー"""
        result = validate_and_parse_tags([], required=True)
        assert isinstance(result, dict)
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_required_all_empty_strings(self):
        """required=Trueで全部空文字もTAGS_REQUIREDエラー"""
        result = validate_and_parse_tags(["", "  "], required=True)
        assert isinstance(result, dict)
        assert result["error"]["code"] == "TAGS_REQUIRED"

    def test_required_false_empty_ok(self):
        """required=Falseで空配列は空リスト"""
        result = validate_and_parse_tags([])
        assert isinstance(result, list)
        assert result == []

    def test_invalid_namespace(self):
        """不正なnamespaceでINVALID_TAG_NAMESPACEエラー"""
        result = validate_and_parse_tags(["invalid:tag"])
        assert isinstance(result, dict)
        assert result["error"]["code"] == "INVALID_TAG_NAMESPACE"

    def test_empty_name(self):
        """name空文字でINVALID_TAG_NAMEエラー"""
        result = validate_and_parse_tags(["domain:"])
        assert isinstance(result, dict)
        assert result["error"]["code"] == "INVALID_TAG_NAME"

    def test_whitespace_name(self):
        """nameが空白のみでINVALID_TAG_NAMEエラー"""
        result = validate_and_parse_tags(["domain:   "])
        assert isinstance(result, dict)
        assert result["error"]["code"] == "INVALID_TAG_NAME"


# ========================================
# ensure_tag_ids テスト
# ========================================


class TestEnsureTagIds:
    """ensure_tag_idsのテスト"""

    def test_create_new_tags(self, temp_db):
        """新規タグをINSERT OR IGNOREしてIDを返す"""
        conn = get_connection()
        try:
            tag_ids = ensure_tag_ids(conn, [("domain", "test"), ("", "hooks")])
            conn.commit()
            assert len(tag_ids) == 2
            assert all(isinstance(tid, int) for tid in tag_ids)
        finally:
            conn.close()

    def test_idempotent(self, temp_db):
        """同じタグを2回呼んでも同じIDが返る"""
        conn = get_connection()
        try:
            ids1 = ensure_tag_ids(conn, [("domain", "test")])
            ids2 = ensure_tag_ids(conn, [("domain", "test")])
            conn.commit()
            assert ids1 == ids2
        finally:
            conn.close()

    def test_empty_list(self, temp_db):
        """空リストを渡すと空リストが返る"""
        conn = get_connection()
        try:
            tag_ids = ensure_tag_ids(conn, [])
            assert tag_ids == []
        finally:
            conn.close()

    def test_preserves_order(self, temp_db):
        """入力順序が保持される"""
        conn = get_connection()
        try:
            tags = [("mode", "design"), ("domain", "alpha"), ("", "zebra")]
            tag_ids = ensure_tag_ids(conn, tags)
            conn.commit()
            assert len(tag_ids) == 3
            # 各IDが異なることを確認
            assert len(set(tag_ids)) == 3
            # 再度呼んで同じ順序で返ることを確認
            tag_ids2 = ensure_tag_ids(conn, tags)
            assert tag_ids == tag_ids2
        finally:
            conn.close()


# ========================================
# link_tags テスト
# ========================================


class TestLinkTags:
    """link_tagsのテスト"""

    def test_link_tags_to_topic(self, temp_db):
        """topic_tagsにタグを紐付けできる"""
        from src.services.topic_service import add_topic
        topic = add_topic(title="Test", description="Test", tags=["domain:test"])

        conn = get_connection()
        try:
            # 追加のタグをリンク
            new_tag_ids = ensure_tag_ids(conn, [("", "extra")])
            link_tags(conn, "topic_tags", "topic_id", topic["topic_id"], new_tag_ids)
            conn.commit()

            # 確認
            tags = get_entity_tags(conn, "topic_tags", "topic_id", topic["topic_id"])
            assert "extra" in tags
            assert "domain:test" in tags
        finally:
            conn.close()


# ========================================
# format_tags テスト
# ========================================


class TestFormatTags:
    """format_tagsのテスト"""

    def test_namespace_and_bare(self):
        """namespace付きと素タグの混在"""
        rows = [
            MockRow("domain", "cc-memory"),
            MockRow("", "hooks"),
            MockRow("mode", "design"),
        ]
        result = format_tags(rows)
        assert result == ["domain:cc-memory", "hooks", "mode:design"]

    def test_sorted(self):
        """アルファベット順ソート"""
        rows = [
            MockRow("", "zebra"),
            MockRow("domain", "alpha"),
            MockRow("", "beta"),
        ]
        result = format_tags(rows)
        assert result == ["beta", "domain:alpha", "zebra"]

    def test_empty(self):
        """空のrows"""
        result = format_tags([])
        assert result == []
