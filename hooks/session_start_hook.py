"""SessionStart hook: コンテキスト取得リマインダー注入"""
import json
import sys

_SESSION_START_MESSAGE = (
    "<system-reminder>"
    "Session started. Before composing your first response, retrieve past context. "
    "Steps: (1) Use get_topics to review the topic list. "
    "(2) If a relevant topic exists, use get_by_ids to fetch details. "
    "Otherwise, create a new topic with add_topic. "
    "(3) Use get_decisions / get_logs as needed for further details. "
    "Skipping this will cause the stop hook to block you."
    "</system-reminder>"
)


def main() -> None:
    try:
        sys.stdin.read()  # stdinを消費（session_id等が渡されるが今は不使用）
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _SESSION_START_MESSAGE,
            }
        }
        print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(f"session_start_hook.py error: {e}", file=sys.stderr)
        print("{}")


if __name__ == "__main__":
    main()
