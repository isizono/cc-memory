"""SessionStart hook: セッションレベル文脈注入

DBから直接クエリしてセッション開始時のコンテキストを注入する。
- アクティビティ一覧（active = in_progress + pending）
- トピック一覧
- リマインダー（active=1）
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをパスに追加（src.db等の参照用）
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.db import get_connection, row_to_dict
from src.services.activity_service import HEARTBEAT_TIMEOUT_MINUTES

# アクティブコンテキスト用の定数
IN_PROGRESS_LIMIT = 3
PENDING_LIMIT = 2


def _calc_elapsed_days(updated_at_str: str) -> int:
    """updated_atからの経過日数を計算する。"""
    try:
        updated = datetime.fromisoformat(updated_at_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - updated).days
    except (ValueError, TypeError):
        return 0


def _get_active_domains_with_conn(conn) -> list[dict]:
    """アクティブなアクティビティ（in_progress/pending）があるdomain:タグを取得する（conn共有版）。

    Returns:
        [{"tag_id": int, "name": str}, ...]（name順ソート）
    """
    rows = conn.execute(
        """
        SELECT DISTINCT t.id AS tag_id, t.name
        FROM tags t
        JOIN activity_tags at ON t.id = at.tag_id
        JOIN activities a ON at.activity_id = a.id
        WHERE t.namespace = 'domain'
          AND a.status IN ('in_progress', 'pending')
        ORDER BY t.name
        """,
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def _get_active_domains() -> list[dict]:
    """アクティブなアクティビティがあるdomain:タグを取得する（テスト用公開API）。

    hook本体は_get_active_domains_with_conn()を使用する。
    """
    conn = get_connection()
    try:
        return _get_active_domains_with_conn(conn)
    finally:
        conn.close()


def _get_active_activities_by_tag_with_conn(conn, tag_id: int) -> list[dict]:
    """domain:タグに紐づくホットアクティビティ（conn共有版）。

    Returns:
        [{"id": int, "title": str, "status": str, "updated_at": str, "is_heartbeat_active": bool}, ...]
        （in_progress優先、updated_at降順）
    """
    rows = conn.execute(
        """
        SELECT a.id, a.title, a.status, a.updated_at,
               CASE WHEN a.last_heartbeat_at > datetime('now', '-' || ? || ' minutes') THEN 1 ELSE 0 END AS is_heartbeat_active
        FROM activities a
        JOIN activity_tags at ON a.id = at.activity_id
        WHERE at.tag_id = ?
          AND a.status IN ('in_progress', 'pending')
        ORDER BY CASE a.status WHEN 'in_progress' THEN 0 ELSE 1 END,
                 a.updated_at DESC
        """,
        (HEARTBEAT_TIMEOUT_MINUTES, tag_id),
    ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        d["is_heartbeat_active"] = bool(d["is_heartbeat_active"])
        result.append(d)
    return result


def _get_active_activities_by_tag(tag_id: int) -> list[dict]:
    """domain:タグに紐づくホットアクティビティを取得する（テスト用公開API）。

    hook本体は_get_active_activities_by_tag_with_conn()を使用する。
    """
    conn = get_connection()
    try:
        return _get_active_activities_by_tag_with_conn(conn, tag_id)
    finally:
        conn.close()


def _build_activities_section(conn) -> str:
    """アクティビティ一覧をdomain:タグごとに組み立てる。"""
    domains = _get_active_domains_with_conn(conn)

    if not domains:
        return ""

    domain_sections = []
    for domain in domains:
        tag_id = domain["tag_id"]
        name = domain["name"]

        activities = _get_active_activities_by_tag_with_conn(conn, tag_id)
        if not activities:
            continue

        heartbeat_activities = [a for a in activities if a.get("is_heartbeat_active")]
        normal_activities = [a for a in activities if not a.get("is_heartbeat_active")]

        in_progress = [a for a in normal_activities if a["status"] == "in_progress"]
        pending = [a for a in normal_activities if a["status"] == "pending"]

        shown_ip = in_progress[:IN_PROGRESS_LIMIT]
        shown_pending = pending[:PENDING_LIMIT]
        overflow = len(normal_activities) - len(shown_ip) - len(shown_pending)

        lines = [f"## {name} (domain)"]

        if heartbeat_activities:
            lines.append("作業中（別セッション）:")
            for a in heartbeat_activities:
                days = _calc_elapsed_days(a["updated_at"])
                lines.append(f"- [{a['id']}] {a['title']} ({days}d)")

        for a in shown_ip:
            days = _calc_elapsed_days(a["updated_at"])
            lines.append(f"\u25cf [{a['id']}] {a['title']} ({days}d)")
        for a in shown_pending:
            days = _calc_elapsed_days(a["updated_at"])
            lines.append(f"\u25cb [{a['id']}] {a['title']} ({days}d)")

        if overflow > 0:
            lines.append(f"  (+{overflow}件)")

        domain_sections.append("\n".join(lines))

    if not domain_sections:
        return ""

    parts = ["# アクティビティ一覧", ""]
    parts.append(domain_sections[0])
    for section in domain_sections[1:]:
        parts.append("")
        parts.append(section)

    return "\n".join(parts) + "\n"


def _build_topics_section(conn) -> str:
    """トピック一覧を組み立てる（最近作成された順、上位10件）。"""
    rows = conn.execute(
        """
        SELECT id, title
        FROM discussion_topics
        ORDER BY created_at DESC
        LIMIT 10
        """,
    ).fetchall()

    if not rows:
        return ""

    lines = ["# トピック一覧（最新10件）"]
    for r in rows:
        lines.append(f"- [{r['id']}] {r['title']}")

    return "\n".join(lines) + "\n"


def _build_reminders_section(conn) -> str:
    """リマインダー一覧を組み立てる。"""
    rows = conn.execute(
        "SELECT content FROM reminders WHERE active = 1"
    ).fetchall()

    if not rows:
        return ""

    lines = ["# リマインダー"]
    for r in rows:
        lines.append(f"- {r['content']}")

    return "\n".join(lines) + "\n"


def _build_session_context() -> str:
    """DBからセッション開始時のコンテキストを組み立てる。

    各セクションは独立してtry/exceptで保護し、
    一部のセクションが失敗しても残りは返す。
    """
    conn = get_connection()
    try:
        sections = []
        builders = [
            _build_activities_section,
            _build_topics_section,
            _build_reminders_section,
        ]
        for builder in builders:
            try:
                result = builder(conn)
                if result:
                    sections.append(result)
            except Exception:
                # セクション単位で失敗を許容し、残りのセクションは返す
                pass

        if not sections:
            return ""

        context = "\n".join(sections)
        context += "\n詳細はsearch / get_decisions / get_logs / check_in等で取得してください。"
        return context

    finally:
        conn.close()


def main() -> None:
    try:
        sys.stdin.read()  # stdinを消費（session_id等が渡されるが今は不使用）

        context = _build_session_context()

        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
        print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(f"session_start_hook.py error: {e}", file=sys.stderr)
        print("{}")


if __name__ == "__main__":
    main()
