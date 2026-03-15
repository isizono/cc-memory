"""launcher.pyのユニットテスト

デーモン起動ロジック、セッションライフサイクル管理、ヘルスチェックを検証する。
stdio <-> HTTP ブリッジは統合テストで検証する。
"""
import json
import subprocess
import urllib.error
import urllib.request

import pytest

from src import launcher


class TestIsServerRunning:
    def test_returns_true_when_server_responds_200(self, monkeypatch):
        """サーバーが200を返す場合はTrueを返す"""

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: FakeResponse(),
        )
        assert launcher._is_server_running() is True

    def test_returns_true_on_405(self, monkeypatch):
        """405 (Method Not Allowed) もサーバー起動済みと見なす"""

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url=req.full_url, code=405, msg="Method Not Allowed",
                hdrs={}, fp=None,
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._is_server_running() is True

    def test_returns_true_on_400(self, monkeypatch):
        """400 (Bad Request) もサーバー起動済みと見なす"""

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url=req.full_url, code=400, msg="Bad Request",
                hdrs={}, fp=None,
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._is_server_running() is True

    def test_returns_false_on_connection_error(self, monkeypatch):
        """接続エラーの場合はFalseを返す"""

        def fake_urlopen(req, timeout=None):
            raise ConnectionRefusedError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._is_server_running() is False

    def test_returns_false_on_500(self, monkeypatch):
        """500エラーの場合はFalseを返す"""

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url=req.full_url, code=500, msg="Internal Server Error",
                hdrs={}, fp=None,
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._is_server_running() is False


class TestStartHttpServer:
    def test_calls_popen_with_correct_args(self, monkeypatch):
        """正しい引数でsubprocess.Popenが呼ばれる"""
        called_with = {}

        class FakePopen:
            def __init__(self, args, **kwargs):
                called_with["args"] = args
                called_with["kwargs"] = kwargs

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        result = launcher._start_http_server()

        assert result is True
        assert called_with["args"][1:] == ["-m", "src.main", "--transport", "http"]
        assert called_with["kwargs"]["start_new_session"] is True
        assert called_with["kwargs"]["stdout"] == subprocess.DEVNULL
        assert called_with["kwargs"]["stderr"] == subprocess.DEVNULL
        assert called_with["kwargs"]["cwd"] == launcher._PROJECT_ROOT

    def test_returns_false_on_oserror(self, monkeypatch):
        """OSErrorの場合はFalseを返す"""

        def fake_popen(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert launcher._start_http_server() is False


class TestEnsureServerRunning:
    def test_returns_true_if_already_running(self, monkeypatch):
        """既にサーバーが起動している場合はTrueを即座に返す"""
        monkeypatch.setattr(launcher, "_is_server_running", lambda: True)
        assert launcher._ensure_server_running() is True

    def test_starts_server_and_waits(self, monkeypatch):
        """サーバーを起動し、起動確認を待つ"""
        call_count = {"check": 0}

        def fake_is_running():
            call_count["check"] += 1
            # 最初の呼び出し（_ensure_server_running冒頭）はFalse
            # 3回目の呼び出し（待機ループ2回目）でTrue
            return call_count["check"] >= 3

        monkeypatch.setattr(launcher, "_is_server_running", fake_is_running)
        monkeypatch.setattr(launcher, "_start_http_server", lambda: True)
        monkeypatch.setattr(launcher.time, "sleep", lambda _: None)

        assert launcher._ensure_server_running() is True

    def test_returns_false_on_start_failure(self, monkeypatch):
        """起動失敗でFalseを返す"""
        monkeypatch.setattr(launcher, "_is_server_running", lambda: False)
        monkeypatch.setattr(launcher, "_start_http_server", lambda: False)
        assert launcher._ensure_server_running() is False

    def test_returns_false_on_timeout(self, monkeypatch):
        """タイムアウトでFalseを返す"""
        monkeypatch.setattr(launcher, "_is_server_running", lambda: False)
        monkeypatch.setattr(launcher, "_start_http_server", lambda: True)
        monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
        assert launcher._ensure_server_running() is False


class TestSessionRegistration:
    def test_register_success(self, monkeypatch):
        """セッション登録が成功する"""

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps({"registered": True, "active_sessions": 1}).encode()

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: FakeResponse(),
        )
        assert launcher._register_session() is True

    def test_register_failure(self, monkeypatch):
        """セッション登録が失敗する"""

        def fake_urlopen(req, timeout=None):
            raise ConnectionRefusedError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._register_session() is False

    def test_unregister_success(self, monkeypatch):
        """セッション解除が成功する"""

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps({"unregistered": True, "active_sessions": 0}).encode()

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: FakeResponse(),
        )
        assert launcher._unregister_session() is True

    def test_unregister_failure(self, monkeypatch):
        """セッション解除が失敗する"""

        def fake_urlopen(req, timeout=None):
            raise ConnectionRefusedError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert launcher._unregister_session() is False


class TestCleanup:
    def test_cleanup_calls_unregister(self, monkeypatch):
        """クリーンアップでunregisterが呼ばれる"""
        called = {"unregister": False}

        def fake_unregister():
            called["unregister"] = True
            return True

        monkeypatch.setattr(launcher, "_unregister_session", fake_unregister)
        # _cleanup_doneをリセット
        monkeypatch.setattr(launcher, "_cleanup_done", False)
        launcher._cleanup()
        assert called["unregister"] is True

    def test_cleanup_idempotent(self, monkeypatch):
        """クリーンアップは2回呼んでも1回しか実行されない"""
        call_count = {"unregister": 0}

        def fake_unregister():
            call_count["unregister"] += 1
            return True

        monkeypatch.setattr(launcher, "_unregister_session", fake_unregister)
        monkeypatch.setattr(launcher, "_cleanup_done", False)
        launcher._cleanup()
        launcher._cleanup()
        assert call_count["unregister"] == 1


class TestSessionId:
    def test_session_id_is_valid_uuid(self):
        """セッションIDが有効なUUIDである"""
        import uuid
        # ValueError が出なければOK
        uuid.UUID(launcher._session_id)

    def test_session_id_is_string(self):
        """セッションIDが文字列である"""
        assert isinstance(launcher._session_id, str)


class TestProjectRoot:
    def test_project_root_points_to_package_root(self):
        """_PROJECT_ROOTがパッケージルートを指している"""
        import os
        assert os.path.isdir(launcher._PROJECT_ROOT)
        assert os.path.isfile(os.path.join(launcher._PROJECT_ROOT, "pyproject.toml"))
