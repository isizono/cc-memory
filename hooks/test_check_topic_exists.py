"""check_topic_exists.py のユニットテスト"""

import json
import os
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.db import get_connection, init_database, execute_query


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
    """check_topic_exists の動作テスト"""

    def test_existing_topic_returns_true(self, temp_db, capsys):
        """存在するtopic_idの場合、trueを返す"""
        # 直接クエリでテスト
        rows = execute_query(
            "SELECT id FROM discussion_topics WHERE id = ?",
            (100,),
        )
        assert len(rows) > 0

    def test_non_existing_topic_returns_false(self, temp_db):
        """存在しないtopic_idの場合、falseを返す"""
        rows = execute_query(
            "SELECT id FROM discussion_topics WHERE id = ?",
            (99999,),
        )
        assert len(rows) == 0

    def test_multiple_topics_exist(self, temp_db):
        """複数トピックがある場合、それぞれ正しくチェックできる"""
        # topic_id=100 は存在する
        rows_100 = execute_query(
            "SELECT id FROM discussion_topics WHERE id = ?",
            (100,),
        )
        assert len(rows_100) > 0

        # topic_id=200 も存在する
        rows_200 = execute_query(
            "SELECT id FROM discussion_topics WHERE id = ?",
            (200,),
        )
        assert len(rows_200) > 0

        # topic_id=300 は存在しない
        rows_300 = execute_query(
            "SELECT id FROM discussion_topics WHERE id = ?",
            (300,),
        )
        assert len(rows_300) == 0


class TestCheckTopicExistsScript:
    """check_topic_exists.py スクリプトとしての動作テスト"""

    def _run_script(self, args, temp_db):
        return subprocess.run(
            [sys.executable, "hooks/check_topic_exists.py", *args],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env={**os.environ, "DISCUSSION_DB_PATH": temp_db},
        )

    def test_script_existing_topic_without_name(self, temp_db):
        """存在するtopic_id、名前なし → exists=trueのみ（name_matchなし）"""
        result = self._run_script(["100"], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": True}

    def test_script_existing_topic_with_matching_name(self, temp_db):
        """存在するtopic_id、名前一致 → exists=true, name_match=true"""
        result = self._run_script(["100", "Test Topic"], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": True, "name_match": True}

    def test_script_existing_topic_with_wrong_name(self, temp_db):
        """存在するtopic_id、名前不一致 → exists=true, name_match=false, actual_name付き"""
        result = self._run_script(["100", "Wrong Name"], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": True, "name_match": False, "actual_name": "Test Topic"}

    def test_script_non_existing_topic(self, temp_db):
        """存在しないtopic_id → exists=false"""
        result = self._run_script(["99999"], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": False}

    def test_script_non_existing_topic_with_name(self, temp_db):
        """存在しないtopic_id + 名前引数あり → exists=false"""
        result = self._run_script(["99999", "Some Name"], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": False}

    def test_script_with_invalid_topic_id(self, temp_db):
        """不正なtopic_idでエラー"""
        result = self._run_script(["invalid"], temp_db)
        assert result.returncode == 1
        assert "Invalid topic_id" in result.stderr

    def test_script_with_no_args(self, temp_db):
        """引数なしでexists=false"""
        result = self._run_script([], temp_db)
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"exists": False}
