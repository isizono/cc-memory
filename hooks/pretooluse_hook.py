"""PreToolUse hook: nudgeリマインダー注入

処理フロー:
1. stdin読み込み → JSON parse（session_id取得）
2. session_idが空/null → 空JSON出力して終了
3. HookState(session_id)を生成
4. 記録リマインダーnudge → system-reminder注入
5. 何もなし → 空JSON出力
"""
import json
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from hooks.hook_state import HookState


def _make_hook_output(message: str) -> dict:
    """PreToolUse hookのsystem-reminder注入用JSON構造を返す"""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


_RECORD_NUDGE_MESSAGE = (
    "<system-reminder>"
    "Self-check before continuing: "
    "(1) Does your current topic still match the conversation? "
    "If the discussion has shifted, create a new topic with add_topic. "
    "(2) Have you and the user reached any agreements that should be recorded? "
    "Examples: design choices, naming conventions, scope boundaries, "
    "implementation approaches, or trade-off resolutions. "
    "If yes, record them now with add_decision before proceeding. "
    "(3) Has there been substantive discussion worth preserving? "
    "Use add_log to capture the flow of conversation — "
    "arguments considered, options explored, and reasoning behind choices. "
    "Decisions record conclusions; logs preserve the path that led there."
    "</system-reminder>"
)


def main() -> None:
    try:
        # 環境変数によるテスト用オーバーライド
        if os.environ.get("HOOK_STATE_DIR"):
            HookState.BASE_DIR = Path(os.environ["HOOK_STATE_DIR"])

        # 1. stdin読み込み
        raw = sys.stdin.read()
        data = json.loads(raw)
        session_id = data.get("session_id", "")

        # 2. session_idが空/null → 空JSON出力
        if not session_id:
            print("{}")
            return

        # 3. HookState生成
        state = HookState(session_id)

        # 4. 記録リマインダーnudge
        if state.pop_nudge_pending():
            print(json.dumps(_make_hook_output(_RECORD_NUDGE_MESSAGE), ensure_ascii=False))
            return

        # 5. 何もなし
        print("{}")

    except Exception as e:
        # フェイルオープン: 例外時は空JSON + stderrログ
        print(f"pretooluse_hook.py error: {e}", file=sys.stderr)
        print("{}")


if __name__ == "__main__":
    main()
