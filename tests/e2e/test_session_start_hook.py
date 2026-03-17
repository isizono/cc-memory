"""hooks/session_start_hook.py の E2E テスト

subprocess.run で session_start_hook.py を呼び出し、stdin→stdout の入出力をテスト。
DISCUSSION_DB_PATH 環境変数でテスト用DBを指定する。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.db import init_database, get_connection

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _run_session_start_hook(db_path: str) -> dict:
    """session_start_hook.pyを実行してJSON出力を返す"""
    env = {**os.environ, "DISCUSSION_DB_PATH": db_path}

    result = subprocess.run(
        [sys.executable, "hooks/session_start_hook.py"],
        input="{}",
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    stdout = result.stdout.strip()
    assert stdout, f"session_start_hook.py produced no output. stderr: {result.stderr}"
    return json.loads(stdout)


def _seed_activity(title: str, status: str = "pending", domain: str = "test") -> int:
    """テスト用アクティビティを作成"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO activities (title, description, status) VALUES (?, ?, ?)",
            (title, "desc", status),
        )
        activity_id = cursor.lastrowid

        # domain:タグを取得または作成
        tag_row = conn.execute(
            "SELECT id FROM tags WHERE namespace = 'domain' AND name = ?",
            (domain,),
        ).fetchone()
        if tag_row:
            tag_id = tag_row["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO tags (namespace, name) VALUES ('domain', ?)",
                (domain,),
            )
            tag_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO activity_tags (activity_id, tag_id) VALUES (?, ?)",
            (activity_id, tag_id),
        )
        conn.commit()
        return activity_id
    finally:
        conn.close()


def _seed_topic(title: str) -> int:
    """テスト用トピックを作成"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO discussion_topics (title, description) VALUES (?, ?)",
            (title, "desc"),
        )
        topic_id = cursor.lastrowid
        conn.commit()
        return topic_id
    finally:
        conn.close()


def _seed_reminder(content: str, active: int = 1) -> int:
    """テスト用リマインダーを作成"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO reminders (content, active) VALUES (?, ?)",
            (content, active),
        )
        reminder_id = cursor.lastrowid
        conn.commit()
        return reminder_id
    finally:
        conn.close()


class TestSessionStartHookBasic:
    """基本的なhook出力テスト"""

    def test_output_structure(self, temp_db):
        """hook出力がhookSpecificOutput構造を持つ"""
        result = _run_session_start_hook(temp_db)

        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "additionalContext" in result["hookSpecificOutput"]

    def test_empty_db_returns_static_guide_only(self, temp_db):
        """データが空の場合、静的な検索フローガイドのみ出力される"""
        # 初期データを削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM reminders")
            conn.execute("DELETE FROM discussion_topics")
            conn.execute("DELETE FROM activities")
            conn.commit()
        finally:
            conn.close()

        result = _run_session_start_hook(temp_db)

        context = result["hookSpecificOutput"]["additionalContext"]
        assert "検索フロー" in context
        assert "アクティビティ一覧" not in context
        assert "リマインダー" not in context


class TestSessionStartHookActivities:
    """アクティビティ一覧の注入テスト"""

    def test_activities_section_present(self, temp_db):
        """アクティブなアクティビティがあればアクティビティ一覧セクションが含まれる"""
        _seed_activity( "[作業] テスト実装", status="in_progress")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "# アクティビティ一覧" in context
        assert "テスト実装" in context

    def test_pending_activity_shown(self, temp_db):
        """pendingアクティビティも表示される"""
        _seed_activity( "[設計] 設計作業", status="pending")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "設計作業" in context

    def test_completed_activity_not_shown(self, temp_db):
        """completedアクティビティは表示されない"""
        _seed_activity( "[作業] 完了済み", status="completed")

        # 初期リマインダー削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM reminders")
            conn.commit()
        finally:
            conn.close()

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "完了済み" not in context


class TestSessionStartHookTopicsRemoved:
    """トピック一覧が廃止されていることのテスト"""

    def test_topics_section_not_present(self, temp_db):
        """トピックがあってもトピック一覧セクションは表示されない"""
        _seed_topic("テストトピック")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "# トピック一覧" not in context
        assert "テストトピック" not in context


class TestSessionStartHookDuplicateActivities:
    """複数domainに属するアクティビティの重複排除テスト"""

    def _seed_activity_multi_domain(self, title: str, domains: list[str], status: str = "in_progress") -> int:
        """複数domainに属するアクティビティを作成"""
        conn = get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO activities (title, description, status) VALUES (?, ?, ?)",
                (title, "desc", status),
            )
            activity_id = cursor.lastrowid

            for domain in domains:
                tag_row = conn.execute(
                    "SELECT id FROM tags WHERE namespace = 'domain' AND name = ?",
                    (domain,),
                ).fetchone()
                if tag_row:
                    tag_id = tag_row["id"]
                else:
                    cursor = conn.execute(
                        "INSERT INTO tags (namespace, name) VALUES ('domain', ?)",
                        (domain,),
                    )
                    tag_id = cursor.lastrowid

                conn.execute(
                    "INSERT INTO activity_tags (activity_id, tag_id) VALUES (?, ?)",
                    (activity_id, tag_id),
                )
            conn.commit()
            return activity_id
        finally:
            conn.close()

    def test_multi_domain_activity_shown_once(self, temp_db):
        """複数domainに属するアクティビティは1回だけ表示される"""
        activity_id = self._seed_activity_multi_domain(
            "[作業] 重複テスト", ["alpha", "beta"]
        )

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        # アクティビティIDが1回だけ出現する
        assert context.count(f"[{activity_id}]") == 1


class TestSessionStartHookReminders:
    """リマインダーの注入テスト"""

    def test_reminders_section_present(self, temp_db):
        """アクティブなリマインダーがあればリマインダーセクションが含まれる"""
        _seed_reminder( "テスト用リマインダー")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "# リマインダー" in context
        assert "テスト用リマインダー" in context

    def test_inactive_reminder_not_shown(self, temp_db):
        """inactive(active=0)のリマインダーは表示されない"""
        _seed_reminder( "無効なリマインダー", active=0)

        # 他のアクティブリマインダーも削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM reminders WHERE active = 1")
            conn.commit()
        finally:
            conn.close()

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "無効なリマインダー" not in context


class TestSessionStartHookFooter:
    """フッターの確認"""

    def test_footer_present_when_content_exists(self, temp_db):
        """コンテンツがある場合、フッターの案内文が含まれる"""
        _seed_activity("[作業] フッターテスト用", status="in_progress")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "詳細はsearch / get_decisions / get_logs / check_in等で取得してください" in context


class TestSessionStartHookErrorHandling:
    """エラーハンドリングのテスト"""

    def test_invalid_db_returns_empty_json(self):
        """不正なDBパスでも空JSONを出力してクラッシュしない"""
        env = {**os.environ, "DISCUSSION_DB_PATH": "/nonexistent/path/db.sqlite"}

        result = subprocess.run(
            [sys.executable, "hooks/session_start_hook.py"],
            input="{}",
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        stdout = result.stdout.strip()
        assert stdout, "should produce some output"
        parsed = json.loads(stdout)
        # エラー時は空JSON
        assert parsed == {}
