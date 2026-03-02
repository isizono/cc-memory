"""hooks/hook_topic.py のユニットテスト"""
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from src.db import get_connection, init_database
from hooks.hook_topic import check_topic_exists


@pytest.fixture
def temp_db():
    """テスト用の一時的なデータベースを作成する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        init_database()

        # init_databaseで作成されたfirst_subjectのIDを取得
        conn = get_connection()
        try:
            cursor = conn.execute("SELECT id FROM subjects WHERE name = 'first_subject'")
            row = cursor.fetchone()
            subject_id = row[0] if row else 1

            # テスト用のトピックを追加作成
            conn.execute(
                "INSERT INTO discussion_topics (id, subject_id, title, description) VALUES (100, ?, 'Test Topic', 'Description')",
                (subject_id,)
            )
            conn.execute(
                "INSERT INTO discussion_topics (id, subject_id, title, description) VALUES (200, ?, 'Another Topic', 'Description')",
                (subject_id,)
            )
            conn.commit()
        finally:
            conn.close()

        yield db_path

        # クリーンアップ
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]


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
            with pytest.raises(Exception):
                check_topic_exists(100)
