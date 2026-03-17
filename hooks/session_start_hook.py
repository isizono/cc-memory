"""SessionStart hook: セッションレベル文脈注入

サービス層経由でDBからデータを取得し、セッション開始時のコンテキストを注入する。
- アクティビティ一覧（active = in_progress + pending）
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

from src.config import IN_PROGRESS_LIMIT, PENDING_LIMIT
from src.db import get_connection
from src.services.activity_service import (
    get_active_domains_with_conn,
    get_active_activities_by_tag_with_conn,
)
from src.services.reminder_service import get_active_reminder_contents_with_conn



def _calc_elapsed_days(updated_at_str: str) -> int:
    """updated_atからの経過日数を計算する。"""
    try:
        updated = datetime.fromisoformat(updated_at_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - updated).days
    except (ValueError, TypeError):
        return 0


def _build_activities_section(conn) -> str:
    """アクティビティ一覧をdomain:タグごとに組み立てる。"""
    domains = get_active_domains_with_conn(conn)

    if not domains:
        return ""

    seen_ids: set[int] = set()
    domain_sections = []
    for domain in domains:
        tag_id = domain["tag_id"]
        name = domain["name"]

        activities = get_active_activities_by_tag_with_conn(conn, tag_id)
        # 他domainで既出のアクティビティを除外（複数domain所属時の重複排除）
        activities = [a for a in activities if a["id"] not in seen_ids]
        if not activities:
            continue
        for a in activities:
            seen_ids.add(a["id"])

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


def _build_reminders_section(conn) -> str:
    """リマインダー一覧を組み立てる。"""
    contents = get_active_reminder_contents_with_conn(conn)

    if not contents:
        return ""

    lines = ["# リマインダー"]
    for content in contents:
        lines.append(f"- {content}")

    return "\n".join(lines) + "\n"


def _build_session_context() -> str:
    """サービス層経由でセッション開始時のコンテキストを組み立てる。

    各セクションは独立してtry/exceptで保護し、
    一部のセクションが失敗しても残りは返す。
    """
    conn = get_connection()
    try:
        sections = []
        builders = [
            _build_activities_section,
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
