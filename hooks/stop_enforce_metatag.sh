#!/bin/bash
#
# Stopフック: メタタグ・トピック管理の強制
#
# 処理フロー:
# 1. メタタグチェック → なければblock
# 2. トピック存在チェック → 存在しなければblock
# 2b. トピック名一致チェック → 不一致ならnudgeフラグを書く（blockしない）
# 3. トピック変更チェック → 前topicにdecision/logなければblock
# 4. approve

set -e

# ERRトラップ: set -eでスクリプトが中断される場合も必ずJSONを返す
# 出力なしだとClaude Codeがapproveにフォールバックし、メタタグ強制がスルーされるため
trap 'echo "{\"decision\": \"approve\", \"reason\": \"stop_enforce_metatag.sh internal error (line $LINENO)\"}" >&1; exit 0' ERR

# スクリプトのディレクトリを取得
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 状態・ログディレクトリの設定
STATE_DIR="${HOME}/.claude/.claude-code-memory/state"
LOG_DIR="${HOME}/.claude/.claude-code-memory/logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# stdinからJSON入力を読み込み
INPUT=$(cat)
echo "$INPUT" >> "$LOG_DIR/stop_hook_input.log"
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')

# 1. メタタグチェック
META_RESULT=$(cd "$PROJECT_ROOT" && uv run python "$SCRIPT_DIR/parse_meta_tag.py" "$TRANSCRIPT_PATH" 2>>"$LOG_DIR/uv_stderr.log")
META_EXIT_CODE=$?

if [ $META_EXIT_CODE -ne 0 ]; then
  # スクリプト実行エラー
  jq -n --arg reason "parse_meta_tag.py failed: $META_RESULT" '{decision: "block", reason: $reason}'
  exit 0
fi

META_FOUND=$(echo "$META_RESULT" | jq -r '.found')

if [ "$META_FOUND" != "true" ]; then
  echo '{"decision": "block", "reason": "応答の最後にメタタグを出力してください。フォーマット: <!-- [meta] subject: xxx (id: N) | topic: yyy (id: M) -->"}'
  exit 0
fi

CURRENT_TOPIC=$(echo "$META_RESULT" | jq -r '.topic_id')
CURRENT_TOPIC_NAME=$(echo "$META_RESULT" | jq -r '.topic_name')

# 2. トピック存在・名前一致チェック
TOPIC_CHECK=$(cd "$PROJECT_ROOT" && uv run python "$SCRIPT_DIR/check_topic_exists.py" "$CURRENT_TOPIC" "$CURRENT_TOPIC_NAME" 2>>"$LOG_DIR/uv_stderr.log")
TOPIC_CHECK_EXIT_CODE=$?

if [ $TOPIC_CHECK_EXIT_CODE -ne 0 ]; then
  # スクリプト実行エラー
  jq -n --arg reason "check_topic_exists.py failed: $TOPIC_CHECK" '{decision: "block", reason: $reason}'
  exit 0
fi

# SESSION_IDのスラッシュをアンダースコアに置換（パス安全化）
SESSION_ID_SAFE="${SESSION_ID//\//_}"

# jqパース失敗時はフェイルクローズ（blockで安全側に倒す）
# 注意: -e フラグは値がfalse/nullの場合に非ゼロ終了するため使わない
TOPIC_EXISTS=$(echo "$TOPIC_CHECK" | jq -r '.exists' 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$TOPIC_EXISTS" ]; then
  jq -n --arg reason "check_topic_exists.py output parse failed: $TOPIC_CHECK" '{decision: "block", reason: $reason}'
  exit 0
fi
if [ "$TOPIC_EXISTS" = "false" ]; then
  jq -n --arg topic "$CURRENT_TOPIC" '{decision: "block", reason: ("topic_id=" + $topic + " は存在しません。get_topics で正しいtopic_idを確認してください")}'
  exit 0
fi

# 2b. トピック名一致チェック（不一致時はnudge、blockしない）
# 注意: jq の // 演算子は false も代替対象にするため使わない
TOPIC_NAME_MATCH=$(echo "$TOPIC_CHECK" | jq -r '.name_match' 2>/dev/null)
if [ "$TOPIC_NAME_MATCH" = "false" ]; then
  ACTUAL_NAME=$(echo "$TOPIC_CHECK" | jq -r '.actual_name' 2>/dev/null)
  # nudgeフラグにtopic_idと正しい名前を書く
  jq -n --argjson tid "$CURRENT_TOPIC" --arg name "$ACTUAL_NAME" '{topic_id: $tid, actual_name: $name}' \
    > "${STATE_DIR}/nudge_topic_name_${SESSION_ID_SAFE}"
fi

# 3. トピック変更チェック
PREV_TOPIC_FILE="${STATE_DIR}/prev_topic_${SESSION_ID_SAFE}"
PREV_TOPIC=$(cat "$PREV_TOPIC_FILE" 2>/dev/null || echo "")

if [ -n "$PREV_TOPIC" ] && [ "$PREV_TOPIC" != "$CURRENT_TOPIC" ]; then
  # セッション開始直後のfirst_topic(topic_id=1)からの移動はスキップ
  if [ "$PREV_TOPIC" = "1" ]; then
    : # 何もしない（決定事項チェックをスキップ）
  else
    # 前のトピックにadd_decisionまたはadd_logが呼び出されたかチェック
    RECORDED_RESULT=$(cd "$PROJECT_ROOT" && uv run python "$SCRIPT_DIR/check_topic_recorded.py" "$PREV_TOPIC" "$TRANSCRIPT_PATH" 2>>"$LOG_DIR/uv_stderr.log")
    RECORDED_EXIT_CODE=$?

    if [ $RECORDED_EXIT_CODE -ne 0 ]; then
      # スクリプト実行エラー
      jq -n --arg reason "check_topic_recorded.py failed: $RECORDED_RESULT" '{decision: "block", reason: $reason}'
      exit 0
    fi

    if [ "$RECORDED_RESULT" = "false" ]; then
      jq -n --arg topic "$PREV_TOPIC" '{decision: "block", reason: ("トピックが変わりました。前のトピック(id=" + $topic + ")に決定事項(add_decision)またはログ(add_log)を記録してから移動してください")}'
      exit 0
    fi
  fi
fi

# 4. 現在のトピックを保存
echo "$CURRENT_TOPIC" > "$PREV_TOPIC_FILE"

# 5. approve
echo '{"decision": "approve"}'
exit 0
