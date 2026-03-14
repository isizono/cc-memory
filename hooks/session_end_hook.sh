#!/bin/bash
#
# SessionEndフック: sync-memoryが未実行の場合にSonnetを自動起動してtranscriptを同期する
#
# 動作:
# 1. stdinからJSONを読み取り、transcript_pathを抽出
# 2. transcriptファイルの存在確認
# 3. sync-memory実行済みかどうかを確認（claude-code-memory:sync-memoryの有無で判定）
# 4. 未同期ならclaude -pをnohup &でバックグラウンド起動
# 5. ログを/tmp/claude-session-end.logに記録
#
# 出力: {"decision": "approve"}（常にapprove）
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/claude-session-end.log"

# ERRトラップ: エラー時も必ずJSONを返す
trap 'echo "{\"decision\": \"approve\"}" >&1; exit 0' ERR

# stdinからJSONを読み取る
INPUT=$(cat)

# transcript_pathを抽出
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

# ログ記録関数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "SessionEnd hook started. transcript_path=$TRANSCRIPT_PATH"

# transcriptファイルの存在確認
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    log "transcript_path is empty or file does not exist. Skipping."
    echo '{"decision": "approve"}'
    exit 0
fi

# sync-memory実行済みかどうかを確認
if grep -q 'claude-code-memory:sync-memory' "$TRANSCRIPT_PATH" 2>/dev/null; then
    log "sync-memory already executed. Skipping auto-sync."
    echo '{"decision": "approve"}'
    exit 0
fi

# ターン数チェック: assistantメッセージが2件以下の短いセッションはスキップ
ASSISTANT_COUNT=$(grep -c '"role":"assistant"' "$TRANSCRIPT_PATH" 2>/dev/null || true)
if [ "$ASSISTANT_COUNT" -le 2 ]; then
    log "Short session (assistant_count=$ASSISTANT_COUNT). Skipping auto-sync."
    echo '{"decision": "approve"}'
    exit 0
fi

log "sync-memory not found in transcript. Launching claude -p for auto-sync."

# claude -p をnohup &でバックグラウンド起動
# stdout: /dev/null, stderr: ログファイルにリダイレクト
#
# unset CLAUDECODE: Claude Codeは起動時にCLAUDECODE=1を設定する。
# 子プロセスのclaude -pがこの変数を継承すると、ネスト検出により
# SessionEnd hookが再発火し無限ループになる可能性があるため、unsetする。
export TRANSCRIPT_PATH SCRIPT_DIR LOG_FILE
nohup bash -c '
    unset CLAUDECODE
    cat "$TRANSCRIPT_PATH" | claude -p \
        --model sonnet \
        --permission-mode dontAsk \
        --system-prompt "$(cat "${SCRIPT_DIR}/auto_sync_prompt.txt")" \
        "以下はClaude Codeセッションのtranscriptです。sync-memory手順に従って解析・記録してください。"
' > /dev/null 2>> "$LOG_FILE" &

log "claude -p launched in background (pid=$!)."

echo '{"decision": "approve"}'
