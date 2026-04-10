"""harness_service: add_decisionsレスポンスへの推奨行動hint注入のテスト"""

import os
import tempfile
import pytest

from src.db import init_database, get_connection
from src.services.topic_service import add_topic
from src.services.decision_service import add_decisions
from src.services.discussion_log_service import add_logs
from src.services.tag_service import _injected_tags
from src.services.harness_service import (
    get_recommendations,
    HINT_LOGS_SPARSE,
    HINT_CONSISTENCY_CHECK,
    MIN_DECISIONS_FOR_LOG_HINT,
    DL_RATIO_THRESHOLD,
    MIN_DECISIONS_FOR_CONSISTENCY,
)
import src.services.embedding_service as emb


DEFAULT_TAGS = ["domain:test"]


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


@pytest.fixture
def topic(temp_db):
    """テスト用トピックを作成する"""
    return add_topic(title="テストトピック", description="テスト用", tags=DEFAULT_TAGS)


@pytest.fixture
def mock_embedding_server(monkeypatch):
    """embedding生成をスキップする"""
    monkeypatch.setattr(emb, '_server_initialized', False)
    yield


def _add_n_decisions(topic_id: int, n: int):
    """指定数のdecisionsをまとめて追加する"""
    batch_size = 10
    for start in range(0, n, batch_size):
        count = min(batch_size, n - start)
        items = [
            {"topic_id": topic_id, "decision": f"決定{start + i + 1}", "reason": "テスト"}
            for i in range(count)
        ]
        add_decisions(items)


def _add_n_logs(topic_id: int, n: int):
    """指定数のlogsをまとめて追加する"""
    batch_size = 10
    for start in range(0, n, batch_size):
        count = min(batch_size, n - start)
        items = [
            {"topic_id": topic_id, "content": f"ログ{start + i + 1}"}
            for i in range(count)
        ]
        add_logs(items)


class TestCondition3LogsSparse:
    """条件#3: decisions対比でlogsが薄いときにhintが出る"""

    def test_decisions_2_logs_0_no_hint(self, topic, mock_embedding_server):
        """decisions 2件・logs 0件ではhintが出ない"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 2)
        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE not in hints

    def test_decisions_3_logs_0_fires(self, topic, mock_embedding_server):
        """decisions 3件・logs 0件で条件#3が発火する"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 3)
        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE in hints

    def test_dl_ratio_above_threshold_fires(self, topic, mock_embedding_server):
        """d/l比が3.0を超えるとhintが出る（例: decisions 7件・logs 2件 = 3.5）"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 7)
        _add_n_logs(tid, 2)
        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE in hints

    def test_dl_ratio_at_threshold_no_hint(self, topic, mock_embedding_server):
        """d/l比がちょうど3.0ではhintが出ない"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 6)
        _add_n_logs(tid, 2)
        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE not in hints

    def test_dl_ratio_below_threshold_no_hint(self, topic, mock_embedding_server):
        """d/l比が3.0未満ではhintが出ない（例: decisions 5件・logs 2件 = 2.5）"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 5)
        _add_n_logs(tid, 2)
        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE not in hints

    def test_retracted_decisions_excluded(self, topic, mock_embedding_server):
        """retractされたdecisionsはカウントに含めない"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 3)

        # 1件retractして有効2件にする → hintは出ない
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE decisions SET retracted_at = datetime('now') WHERE id = (SELECT id FROM decisions WHERE topic_id = ? AND retracted_at IS NULL LIMIT 1)",
                (tid,),
            )
            conn.commit()
        finally:
            conn.close()

        hints = get_recommendations(tid)
        assert HINT_LOGS_SPARSE not in hints

    def test_retracted_logs_excluded(self, topic, mock_embedding_server):
        """retractされたlogsはカウントに含めない: retractで発火/非発火が切り替わる"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 6)
        _add_n_logs(tid, 2)
        # d/l = 6/2 = 3.0（閾値ちょうど） → 発火しない
        assert HINT_LOGS_SPARSE not in get_recommendations(tid)

        # 1件retractして有効logs=1 → d/l = 6/1 = 6.0 > 3.0 → 発火する
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE discussion_logs SET retracted_at = datetime('now') WHERE id = (SELECT id FROM discussion_logs WHERE topic_id = ? AND retracted_at IS NULL LIMIT 1)",
                (tid,),
            )
            conn.commit()
        finally:
            conn.close()
        assert HINT_LOGS_SPARSE in get_recommendations(tid)


class TestCondition4Consistency:
    """条件#4: decision数が15以上でセッション初回のhint"""

    def test_decisions_14_no_hint(self, topic, mock_embedding_server):
        """decisions 14件ではhintが出ない"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 14)
        hints = get_recommendations(tid, shown_consistency_hint=False)
        assert HINT_CONSISTENCY_CHECK not in hints

    def test_decisions_15_fires(self, topic, mock_embedding_server):
        """decisions 15件で条件#4が発火する"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 15)
        hints = get_recommendations(tid, shown_consistency_hint=False)
        assert HINT_CONSISTENCY_CHECK in hints

    def test_shown_flag_suppresses(self, topic, mock_embedding_server):
        """shown_consistency_hint=Trueのときは条件#4が出ない"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 15)
        hints = get_recommendations(tid, shown_consistency_hint=True)
        assert HINT_CONSISTENCY_CHECK not in hints


class TestBothConditions:
    """条件#3と#4が同時該当するケース"""

    def test_both_fire(self, topic, mock_embedding_server):
        """decisions 15件・logs 0件で両方発火する"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 15)
        hints = get_recommendations(tid, shown_consistency_hint=False)
        assert HINT_LOGS_SPARSE in hints
        assert HINT_CONSISTENCY_CHECK in hints
        assert len(hints) == 2

    def test_no_hints_when_healthy(self, topic, mock_embedding_server):
        """健全な状態ではhintが出ない（decisions 10件・logs 5件 = d/l 2.0）"""
        tid = topic["topic_id"]
        _add_n_decisions(tid, 10)
        _add_n_logs(tid, 5)
        hints = get_recommendations(tid, shown_consistency_hint=False)
        assert len(hints) == 0

    def test_zero_decisions_no_hint(self, topic, mock_embedding_server):
        """decisions 0件ではhintが出ない"""
        tid = topic["topic_id"]
        hints = get_recommendations(tid)
        assert len(hints) == 0
