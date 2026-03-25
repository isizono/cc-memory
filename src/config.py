"""cc-memory 設定モジュール。環境変数で定数をオーバーライド可能にする。"""
import os

# --- Database ---
# CCM_DB_PATH を優先、なければ既存の DISCUSSION_DB_PATH にフォールバック
DB_PATH: str | None = os.environ.get("CCM_DB_PATH") or os.environ.get("DISCUSSION_DB_PATH")

# --- Activity ---
HEARTBEAT_TIMEOUT_MINUTES: int = int(os.environ.get("CCM_HEARTBEAT_TIMEOUT", "20"))
SNOOZE_DURATION_DAYS: int = int(os.environ.get("CCM_SNOOZE_DURATION_DAYS", "3"))

# --- Active Context 表示 ---
IN_PROGRESS_LIMIT: int = int(os.environ.get("CCM_IN_PROGRESS_LIMIT", "3"))
PENDING_LIMIT: int = int(os.environ.get("CCM_PENDING_LIMIT", "2"))

# --- Search ---
# Recency boost の減衰率。半年(182日)で約0.80倍、1年(365日)で約0.66倍
RECENCY_DECAY_RATE: float = float(os.environ.get("CCM_RECENCY_DECAY_RATE", "0.0014"))

# --- Snapshot ---
SNAPSHOT_INTERVAL_HOURS: int = int(os.environ.get("CCM_SNAPSHOT_INTERVAL", "12"))
SNAPSHOT_MAX_COUNT: int = int(os.environ.get("CCM_SNAPSHOT_MAX_COUNT", "5"))
SNAPSHOT_ANOMALY_THRESHOLD: int = int(os.environ.get("CCM_SNAPSHOT_ANOMALY_THRESHOLD", "100"))

# --- Sync Memory ---
SYNC_DISABLE_RETROSPECTIVE: bool = os.environ.get(
    "CCM_SYNC_DISABLE_RETROSPECTIVE", "false"
).lower() in ("true", "1")
