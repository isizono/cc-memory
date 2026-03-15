"""PreToolUse hook: nudgeリマインダー注入（イベント駆動版）

処理フロー:
1. stdin読み込み → JSON parse（session_id取得）
2. session_idが空/null → 空JSON出力して終了
3. events.jsonl全読み
4. 未消費のnudgeイベント判定 → system-reminder注入
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


_ACTIVITY_NUDGE_MESSAGE = (
    "<system-reminder>"
    "You just recorded a decision. Consider whether it implies follow-up work "
    "(design discussion, implementation, investigation) that should be tracked "
    "as a new activity. If so, create one with add_activity. "
    "Ignore if not applicable."
    "</system-reminder>"
)

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

        # 3. events.jsonl全読み
        state = HookState(session_id)
        events = state.read_events()

        if not events:
            print("{}")
            return

        # 4. 未消費のnudgeイベント判定
        # 最新のnudgeイベントを探す（consumed=Trueでないもの）
        for e in reversed(events):
            if e.get("e") != "nudge":
                continue
            if e.get("consumed"):
                continue

            # nudgeを消費済みにマーク
            e["consumed"] = True
            _rewrite_events(state, events)

            if e.get("type") == "activity":
                print(json.dumps(_make_hook_output(_ACTIVITY_NUDGE_MESSAGE), ensure_ascii=False))
                return
            elif e.get("type") == "record":
                print(json.dumps(_make_hook_output(_RECORD_NUDGE_MESSAGE), ensure_ascii=False))
                return

        # 5. 何もなし
        print("{}")

    except Exception as e:
        # フェイルオープン: 例外時は空JSON + stderrログ
        print(f"pretooluse_hook.py error: {e}", file=sys.stderr)
        print("{}")


def _rewrite_events(state: HookState, events: list[dict]) -> None:
    """events.jsonlを全書き換えする（nudge消費マーク用）。
    tempfile + os.replace()でアトミックに書き換える。"""
    import os
    import tempfile

    dir_ = state.events_path.parent
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8") as f:
        tmp = f.name
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    os.replace(tmp, state.events_path)


if __name__ == "__main__":
    main()
