"""hook共通: 状態ファイル管理クラス HookState

hookが利用する状態ファイル（prev_topic, block_count, nudge_counter, nudge_pending,
context_retrieved, approved_turns, activity_checkin, skill_skip）の読み書きを一元管理する。
標準ライブラリのみに依存。
"""
from pathlib import Path


class HookState:
    BASE_DIR = Path.home() / ".claude" / ".claude-code-memory" / "state"

    def __init__(self, session_id: str):
        self._session_id_safe = session_id.replace("/", "_")
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

    # --- private helpers ---

    def _path(self, prefix: str) -> Path:
        return self.BASE_DIR / f"{prefix}_{self._session_id_safe}"

    def _read_int(self, path: Path, default: int = 0) -> int:
        try:
            return int(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return default

    def _read_str(self, path: Path) -> str | None:
        try:
            value = path.read_text().strip()
            return value if value else None
        except FileNotFoundError:
            return None

    def _write(self, path: Path, value: str) -> None:
        path.write_text(value)

    def _delete(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    # --- prev_topic ---

    def get_prev_topic(self) -> str | None:
        """state/prev_topic_{session_id_safe} を読む。
        ファイルなし -> None"""
        return self._read_str(self._path("prev_topic"))

    def set_prev_topic(self, topic_name: str) -> None:
        """state/prev_topic_{session_id_safe} に書き込む"""
        self._write(self._path("prev_topic"), topic_name)

    # --- block_count ---

    def get_block_count(self) -> int:
        """state/block_count_{session_id_safe} を読む。
        ファイルなし or 内容が不正 -> 0"""
        return self._read_int(self._path("block_count"), 0)

    def increment_block_count(self) -> int:
        """インクリメントして書き込み、新しい値を返す"""
        new_val = self.get_block_count() + 1
        self._write(self._path("block_count"), str(new_val))
        return new_val

    def reset_block_count(self) -> None:
        """ファイル削除（missing_ok=True）"""
        self._delete(self._path("block_count"))

    # --- nudge_counter ---

    def get_nudge_counter(self) -> int:
        """state/nudge_counter_{session_id_safe} を読む。
        ファイルなし or 内容が不正 -> 0"""
        return self._read_int(self._path("nudge_counter"), 0)

    def increment_nudge_counter(self) -> int:
        """インクリメントして書き込み、新しい値を返す"""
        new_val = self.get_nudge_counter() + 1
        self._write(self._path("nudge_counter"), str(new_val))
        return new_val

    def reset_nudge_counter(self) -> None:
        """ファイル削除（missing_ok=True）"""
        self._delete(self._path("nudge_counter"))

    # --- nudge_pending ---

    def set_nudge_pending(self) -> None:
        """state/nudge_pending_{session_id_safe} に '1' を書く"""
        self._write(self._path("nudge_pending"), "1")

    def pop_nudge_pending(self) -> bool:
        """ファイルが存在すれば削除して True、なければ False"""
        try:
            self._path("nudge_pending").unlink()
            return True
        except FileNotFoundError:
            return False

    # --- approved_turns ---

    def get_approved_turns(self) -> int:
        """approve済みターン数を取得。block時はインクリメントされない。"""
        return self._read_int(self._path("approved_turns"), 0)

    def increment_approved_turns(self) -> int:
        """approve済みターン数を+1して返す。ステップ7(approve)でのみ呼ばれる。"""
        new_val = self.get_approved_turns() + 1
        self._write(self._path("approved_turns"), str(new_val))
        return new_val

    # --- activity_checkin ---

    def has_activity_checkin(self) -> bool:
        """activity check-in済みフラグを確認。one-shot block制御に使用。"""
        return self._path("activity_checkin").exists()

    def set_activity_checkin(self) -> None:
        """activity check-in済みフラグを設定。以後のcheck-inチェックをスキップする。"""
        self._write(self._path("activity_checkin"), "1")

    # --- skill_skip_remaining ---

    def get_skill_skip_remaining(self) -> int:
        """スキル実行中のスキップ残りターン数を取得。"""
        return self._read_int(self._path("skill_skip"), 0)

    def set_skill_skip_remaining(self, n: int) -> None:
        """スキル実行中のスキップ残りターン数を設定。0以下の場合はファイル削除。"""
        if n <= 0:
            self._delete(self._path("skill_skip"))
        else:
            self._write(self._path("skill_skip"), str(n))

    # --- context_retrieved ---

    def has_context_retrieval(self) -> bool:
        """context_retrieved フラグファイルが存在するかチェック。
        セッション中に一度でもget系APIを呼んだらTrue。"""
        return self._path("context_retrieved").exists()

    def set_context_retrieved(self) -> None:
        """context_retrieved フラグファイルを作成する。"""
        self._write(self._path("context_retrieved"), "1")

    # --- clear_session ---

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        """BASE_DIR内の全状態ファイルを削除する"""
        session_id_safe = session_id.replace("/", "_")
        if not cls.BASE_DIR.exists():
            return
        for f in cls.BASE_DIR.glob(f"*_{session_id_safe}"):
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    import json
    import os
    import sys

    if os.environ.get("HOOK_STATE_DIR"):
        HookState.BASE_DIR = Path(os.environ["HOOK_STATE_DIR"])

    if len(sys.argv) >= 2 and sys.argv[1] == "clear":
        data = json.loads(sys.stdin.read())
        session_id = data.get("session_id", "")
        if session_id:
            HookState.clear_session(session_id)
