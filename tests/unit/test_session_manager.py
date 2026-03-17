"""session_managerモジュールのユニットテスト"""
import threading
import time

from src.services.session_manager import SessionManager


class TestRegisterUnregister:
    def test_register_new_session(self):
        """新規セッション登録でTrueを返す"""
        mgr = SessionManager()
        assert mgr.register("s1") is True
        assert mgr.active_count == 1

    def test_register_duplicate_session(self):
        """同じセッションIDの再登録はFalseを返す"""
        mgr = SessionManager()
        mgr.register("s1")
        assert mgr.register("s1") is False
        assert mgr.active_count == 1

    def test_register_multiple_sessions(self):
        """複数セッションの登録"""
        mgr = SessionManager()
        mgr.register("s1")
        mgr.register("s2")
        mgr.register("s3")
        assert mgr.active_count == 3

    def test_unregister_existing_session(self):
        """登録済みセッションの解除でTrueを返す"""
        mgr = SessionManager()
        mgr.register("s1")
        assert mgr.unregister("s1") is True
        assert mgr.active_count == 0

    def test_unregister_nonexistent_session(self):
        """未登録セッションの解除はFalseを返す"""
        mgr = SessionManager()
        assert mgr.unregister("s1") is False

    def test_session_ids_returns_copy(self):
        """session_idsはコピーを返す"""
        mgr = SessionManager()
        mgr.register("s1")
        mgr.register("s2")
        ids = mgr.session_ids
        assert ids == {"s1", "s2"}
        # コピーなので元に影響しない
        ids.add("s3")
        assert mgr.active_count == 2


class TestGraceTimer:
    def test_shutdown_after_grace_period(self):
        """セッション0 → 猶予期間経過 → shutdownコールバックが呼ばれる"""
        shutdown_called = threading.Event()
        mgr = SessionManager(grace_period_sec=1)
        mgr.set_shutdown_callback(shutdown_called.set)
        mgr.start_watchdog()

        # 1秒の猶予期間 + マージン
        assert shutdown_called.wait(timeout=3) is True
        assert mgr.is_shutdown_requested is True

    def test_grace_timer_cancelled_by_register(self):
        """猶予期間中にセッション登録があるとタイマーがキャンセルされる"""
        shutdown_called = threading.Event()
        mgr = SessionManager(grace_period_sec=10)
        mgr.set_shutdown_callback(shutdown_called.set)
        mgr.start_watchdog()

        # 1秒後にセッション登録（grace_period=10sなので十分余裕がある）
        time.sleep(1)
        mgr.register("s1")

        # キャンセル後、少し待ってもshutdownは呼ばれない
        assert shutdown_called.wait(timeout=3) is False
        assert mgr.is_shutdown_requested is False

    def test_grace_timer_restarted_on_last_unregister(self):
        """最後のセッション解除で猶予タイマーが再開される"""
        shutdown_called = threading.Event()
        mgr = SessionManager(grace_period_sec=1)
        mgr.set_shutdown_callback(shutdown_called.set)

        mgr.register("s1")
        # セッション解除 → 猶予タイマー開始
        mgr.unregister("s1")

        assert shutdown_called.wait(timeout=3) is True
        assert mgr.is_shutdown_requested is True

    def test_no_shutdown_if_sessions_remain(self):
        """セッションが残っていればshutdownは呼ばれない"""
        shutdown_called = threading.Event()
        mgr = SessionManager(grace_period_sec=1)
        mgr.set_shutdown_callback(shutdown_called.set)

        mgr.register("s1")
        mgr.register("s2")
        mgr.unregister("s1")

        # 猶予期間+マージンを待ってもshutdownは呼ばれない
        assert shutdown_called.wait(timeout=3) is False
        assert mgr.is_shutdown_requested is False

    def test_register_during_grace_resets_timer(self):
        """猶予期間中にregister→unregisterすると猶予がリセットされる"""
        shutdown_called = threading.Event()
        mgr = SessionManager(grace_period_sec=3)
        mgr.set_shutdown_callback(shutdown_called.set)
        mgr.start_watchdog()

        # 1秒後にregister→即unregister（タイマーリセット）
        time.sleep(1)
        mgr.register("s1")
        mgr.unregister("s1")

        # リセット後の新しい猶予期間（3秒）の前にはshutdownされない
        time.sleep(1)
        assert mgr.is_shutdown_requested is False

        # 合計で猶予期間分待てばshutdownされる
        assert shutdown_called.wait(timeout=5) is True
