---
name: checkin
description: アクティビティにcheck-inして関連情報を集約取得する
---

# checkin

指定されたアクティビティに対して `check_in` ツールを呼び出してください。

## 手順

1. 引数で `activity_id` が指定されていればそのまま使う
2. 指定されていなければ `get_activities()` で候補を表示し、ユーザーに選んでもらう
3. `check_in(activity_id=...)` を呼び出す
4. 返ってきた `summary` フィールドをそのまま出力する
