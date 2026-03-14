"""ハートビート更新 — stop_hookから呼ばれるDB書き込みの隔離モジュール"""
from src.db import get_connection


def update_heartbeat(activity_id: int) -> None:
    """last_heartbeat_at を現在時刻に更新する"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE activities SET last_heartbeat_at = datetime('now') WHERE id = ?",
            (activity_id,),
        )
        conn.commit()
    finally:
        conn.close()
