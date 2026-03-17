#!/bin/bash
#
# PostToolUseフック: add_decisions後にアクティビティ追加をリマインドする
#
# 発火条件:
# - settings.jsonのmatcherにより、add_decisions呼び出し時のみ実行される
#

set -e

# ERRトラップ: エラー時も必ずJSONを返す
trap 'echo "{\"decision\": \"approve\"}" >&1; exit 0' ERR

echo '{"decision": "approve", "message": "決定事項を記録しました。関連するアクティビティの追加はありますか？（add_activityで追加できます）"}'
