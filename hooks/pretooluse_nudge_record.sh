#!/bin/bash
#
# PreToolUse hook: nudgeリマインダー注入
#
# 処理フロー:
# 1. topic名不一致フラグがあればtopic名修正nudgeを注入してフラグ消去
# 2. nudge_pendingフラグがあれば記録リマインダーを注入してフラグ消去
# 3. どちらもなければ何もしない（空JSON出力）
#
# 注意: 両方のフラグがある場合はtopic名nudgeを優先（1ターンに1つ）

STATE_DIR="${HOME}/.claude/.claude-code-memory/state"
mkdir -p "$STATE_DIR"

# stdinからJSON入力を読み込み
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')

# SESSION_IDが空またはnullの場合はスキップ
if [ -z "$SESSION_ID" ] || [ "$SESSION_ID" = "null" ]; then
  echo '{}'
  exit 0
fi

# SESSION_IDのスラッシュをアンダースコアに置換（パス安全化）
SESSION_ID_SAFE="${SESSION_ID//\//_}"
TOPIC_NAME_FILE="${STATE_DIR}/nudge_topic_name_${SESSION_ID_SAFE}"
PENDING_FILE="${STATE_DIR}/nudge_pending_${SESSION_ID_SAFE}"

# 1. topic名不一致nudge（優先）
if [ -f "$TOPIC_NAME_FILE" ]; then
  TOPIC_ID=$(jq -r '.topic_id' "$TOPIC_NAME_FILE" 2>/dev/null)
  ACTUAL_NAME=$(jq -r '.actual_name' "$TOPIC_NAME_FILE" 2>/dev/null)

  # パース結果を検証してからフラグ削除（失敗時はフォールバック）
  if [ -n "$TOPIC_ID" ] && [ -n "$ACTUAL_NAME" ] && [ "$TOPIC_ID" != "null" ] && [ "$ACTUAL_NAME" != "null" ]; then
    rm -f "$TOPIC_NAME_FILE"

    # <>" をサニタイズ（system-reminderタグのinjection・引用符破壊防止）
    ACTUAL_NAME_SAFE=$(echo "$ACTUAL_NAME" | tr -d '<>"')

    NUDGE_MSG="<system-reminder>The topic name in your meta tag does not match the database. Topic #${TOPIC_ID} is actually named \"${ACTUAL_NAME_SAFE}\". Please use the correct topic name in your next meta tag, or verify the topic_id with get_topics if you intended a different topic.</system-reminder>"

    jq -n --arg ctx "$NUDGE_MSG" '{
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": $ctx
      }
    }'
    exit 0
  fi
  # パース失敗時はフラグを削除してフォールバック（nudge_pendingの処理に続行）
  rm -f "$TOPIC_NAME_FILE"
fi

# 2. 記録リマインダーnudge
if [ -f "$PENDING_FILE" ]; then
  rm -f "$PENDING_FILE"

  NUDGE_MSG="<system-reminder>Self-check before continuing: (1) Does your current topic still match the conversation? If the discussion has shifted, create a new topic with add_topic. (2) Have you and the user reached any agreements that should be recorded? Examples: design choices, naming conventions, scope boundaries, implementation approaches, or trade-off resolutions. If yes, record them now with add_decision before proceeding.</system-reminder>"

  jq -n --arg ctx "$NUDGE_MSG" '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "additionalContext": $ctx
    }
  }'
  exit 0
fi

# 3. 何もしない
echo '{}'
exit 0
