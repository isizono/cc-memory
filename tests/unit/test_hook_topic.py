"""hooks/hook_topic.py のユニットテスト"""
import sqlite3
from unittest.mock import patch

import pytest

from src.db import get_connection, init_database
from hooks.hook_topic import check_topic_exists, check_topic_has_tags


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """テスト用の一時的なデータベースを作成する"""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DISCUSSION_DB_PATH", db_path)
    init_database()

    # テスト用のトピックを追加作成（subject_id は migration 0010 で削除済み）
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO discussion_topics (id, title, description) VALUES (100, 'Test Topic', 'Description')"
        )
        conn.execute(
            "INSERT INTO discussion_topics (id, title, description) VALUES (200, 'Another Topic', 'Description')"
        )
        # タグなしトピック
        conn.execute(
            "INSERT INTO discussion_topics (id, title, description) VALUES (300, 'No Tags Topic', 'Description')"
        )
        # テスト用タグを追加
        conn.execute("INSERT OR IGNORE INTO tags (id, namespace, name) VALUES (1, 'domain', 'test')")
        conn.execute("INSERT INTO topic_tags (topic_id, tag_id) VALUES (100, 1)")
        conn.execute("INSERT INTO topic_tags (topic_id, tag_id) VALUES (200, 1)")
        conn.commit()
    finally:
        conn.close()

    yield db_path


class TestCheckTopicExists:
    """check_topic_exists関数のテスト"""

    def test_existing_topic_without_name(self, temp_db):
        """存在するtopic（名前なし） -> {"exists": True}"""
        result = check_topic_exists(100)
        assert result == {"exists": True}

    def test_existing_topic_with_matching_name(self, temp_db):
        """存在するtopic + 名前一致 -> {"exists": True, "name_match": True}"""
        result = check_topic_exists(100, "Test Topic")
        assert result == {"exists": True, "name_match": True}

    def test_existing_topic_with_wrong_name(self, temp_db):
        """存在するtopic + 名前不一致 -> {"exists": True, "name_match": False, "actual_name": "..."}"""
        result = check_topic_exists(100, "Wrong Name")
        assert result == {"exists": True, "name_match": False, "actual_name": "Test Topic"}

    def test_non_existing_topic(self, temp_db):
        """存在しないtopic -> {"exists": False}"""
        result = check_topic_exists(99999)
        assert result == {"exists": False}

    def test_db_error_raises_exception(self):
        """DB接続エラー -> 例外raise"""
        with patch("src.db.execute_query", side_effect=sqlite3.OperationalError("mocked DB error")):
            with pytest.raises(sqlite3.OperationalError):
                check_topic_exists(100)


class TestCheckTopicHasTags:
    """check_topic_has_tags関数のテスト"""

    def test_topic_with_tags(self, temp_db):
        """タグがあるトピック -> True"""
        assert check_topic_has_tags(100) is True

    def test_topic_without_tags(self, temp_db):
        """タグがないトピック -> False"""
        assert check_topic_has_tags(300) is False

    def test_nonexistent_topic(self, temp_db):
        """存在しないトピック -> False（行がないのでCOUNT=0）"""
        assert check_topic_has_tags(99999) is False

    def test_db_error_raises_exception(self):
        """DB接続エラー -> 例外raise"""
        with patch("src.db.execute_query", side_effect=sqlite3.OperationalError("mocked DB error")):
            with pytest.raises(sqlite3.OperationalError):
                check_topic_has_tags(100)
