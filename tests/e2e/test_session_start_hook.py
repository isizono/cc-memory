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
    import src.config
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["DISCUSSION_DB_PATH"] = db_path
        src.config.DB_PATH = db_path
        init_database()
        yield db_path
        if "DISCUSSION_DB_PATH" in os.environ:
            del os.environ["DISCUSSION_DB_PATH"]
        src.config.DB_PATH = None


def _run_session_start_hook(
    db_path: str,
    extra_env: dict | None = None,
    env_remove: list[str] | None = None,
) -> dict:
    """session_start_hook.pyを実行してJSON出力を返す"""
    env = {**os.environ, "DISCUSSION_DB_PATH": db_path}
    if extra_env:
        env.update(extra_env)
    if env_remove:
        for key in env_remove:
            env.pop(key, None)

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


def _seed_habit(content: str, active: int = 1) -> int:
    """テスト用振る舞いを作成"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO habits (content, active) VALUES (?, ?)",
            (content, active),
        )
        habit_id = cursor.lastrowid
        conn.commit()
        return habit_id
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
        """データが空の場合、静的なコンテキスト取得フローガイドのみ出力される"""
        # 初期データを削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM habits")
            conn.execute("DELETE FROM discussion_topics")
            conn.execute("DELETE FROM activities")
            conn.commit()
        finally:
            conn.close()

        result = _run_session_start_hook(temp_db)

        context = result["hookSpecificOutput"]["additionalContext"]
        assert "コンテキスト取得フロー" in context
        assert "補助ツール・概念" in context
        assert "# アクティビティ一覧" not in context
        assert "振る舞い" not in context


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

        # 初期振る舞いデータ削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM habits")
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


class TestSessionStartHookHabits:
    """振る舞いの注入テスト"""

    def test_habits_section_present(self, temp_db):
        """アクティブな振る舞いがあれば振る舞いセクションが含まれる"""
        _seed_habit("テスト用振る舞い")

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "# 振る舞い" in context
        assert "テスト用振る舞い" in context

    def test_inactive_habit_not_shown(self, temp_db):
        """inactive(active=0)の振る舞いは表示されない"""
        _seed_habit("無効な振る舞い", active=0)

        # 他のアクティブな振る舞いも削除
        conn = get_connection()
        try:
            conn.execute("DELETE FROM habits WHERE active = 1")
            conn.commit()
        finally:
            conn.close()

        result = _run_session_start_hook(temp_db)
        context = result["hookSpecificOutput"]["additionalContext"]

        assert "無効な振る舞い" not in context


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


class TestSessionStartHookSyncPolicy:
    """sync_policyの注入テスト"""

    def test_sync_policy_shown_when_set(self, temp_db):
        """CCM_SYNC_POLICY設定時にsync_policyセクションが出力される"""
        result = _run_session_start_hook(
            temp_db, extra_env={"CCM_SYNC_POLICY": "PRマージ済みは自動で閉じて"}
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        assert "# sync_policy" in context
        assert "PRマージ済みは自動で閉じて" in context

    def test_sync_policy_hidden_when_unset(self, temp_db):
        """CCM_SYNC_POLICY未設定時にsync_policyセクションが出力されない"""
        result = _run_session_start_hook(
            temp_db, env_remove=["CCM_SYNC_POLICY"]
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        assert "# sync_policy" not in context

    def test_sync_policy_hidden_when_empty(self, temp_db):
        """CCM_SYNC_POLICY空文字時にsync_policyセクションが出力されない"""
        result = _run_session_start_hook(
            temp_db, extra_env={"CCM_SYNC_POLICY": ""}
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        assert "# sync_policy" not in context
