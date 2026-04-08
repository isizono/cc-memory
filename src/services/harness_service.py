"""ツールレスポンスへの推奨行動hint注入サービス"""

from src.db import get_connection

# --- hint文言定数 ---

HINT_LOGS_SPARSE = (
    "このトピックはdecisionsに対してlogsが少ないです。"
    "議論の経緯をadd_logsで記録してください。"
    "決定事項だけでは、なぜその結論に至ったかが将来のセッションで失われます。"
)

HINT_CONSISTENCY_CHECK = (
    "このトピックにはdecisionsが多数蓄積されています。"
    "新しい決定が既存の決定事項と矛盾していないか、get_decisionsで確認してください。"
)

# --- 閾値定数 ---

# 条件#3: logs蓄積不足
MIN_DECISIONS_FOR_LOG_HINT = 3  # logs==0のとき、この件数以上で発火
DL_RATIO_THRESHOLD = 3.0  # d/l比がこの値を超えたら発火

# 条件#4: 整合性確認
MIN_DECISIONS_FOR_CONSISTENCY = 15


def _count_decisions_and_logs(conn, topic_id: int) -> tuple[int, int]:
    """topicのdecisions数とlogs数を取得する（retracted除外）"""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM decisions WHERE topic_id = ? AND retracted_at IS NULL",
        (topic_id,),
    ).fetchone()
    decision_count = row["cnt"] if row else 0

    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM discussion_logs WHERE topic_id = ? AND retracted_at IS NULL",
        (topic_id,),
    ).fetchone()
    log_count = row["cnt"] if row else 0

    return decision_count, log_count


def get_recommendations(topic_id: int, shown_consistency_hint: bool = False) -> list[str]:
    """add_decisionsレスポンスに付与する推奨行動hintを返す。

    Args:
        topic_id: 対象トピックID
        shown_consistency_hint: セッション内で条件#4のhintを既に表示済みか

    Returns:
        hintメッセージのリスト（0〜2件）
    """
    hints: list[str] = []

    conn = get_connection()
    try:
        decision_count, log_count = _count_decisions_and_logs(conn, topic_id)
    finally:
        conn.close()

    # 条件#3: logs蓄積不足
    if decision_count >= MIN_DECISIONS_FOR_LOG_HINT and log_count == 0:
        hints.append(HINT_LOGS_SPARSE)
    elif log_count > 0 and decision_count / log_count > DL_RATIO_THRESHOLD:
        hints.append(HINT_LOGS_SPARSE)

    # 条件#4: 整合性確認（セッション内初回のみ）
    if not shown_consistency_hint and decision_count >= MIN_DECISIONS_FOR_CONSISTENCY:
        hints.append(HINT_CONSISTENCY_CHECK)

    return hints
