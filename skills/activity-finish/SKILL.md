---
name: activity-finish
description: 【必須】アクティビティを完了にする。「/af」「/activity-finish」「アクティビティ終わり」「この作業完了」「クローズして」など、現在のアクティビティを終了・完了させる意図で発動する。このスキルを経由せずにupdate_activity(status="completed")を直接呼んではいけない。
---

# activity-finish

現在のアクティビティを完了にする。

## 手順

1. `update_activity` で現在のアクティビティを完了にする
   - `status` を `"completed"` にする
   - `description` の末尾に完了記録を追記する（例: `\n\n## 完了\nYYYY-MM-DD ユーザーにより完了`）
   - ログは残さない（軽量にサクッと終わらせる）
2. 完了したことをユーザーに一言伝える

## 注意

- `/af`が呼ばれたこと自体が「終わった」の根拠。エージェント側で「本当に終わった？」等の判定ロジックは入れない
- sync-memory的な記録の棚卸しはしない。あくまで軽量な完了操作
