---
name: check-in
description: アクティビティにcheck-inして関連情報を集約取得する
---

# check-in

指定されたアクティビティに対して `check_in` ツールを呼び出してください。

## 手順

1. 引数で `activity_id` が指定されていればそのまま使う
2. 指定されていなければ `get_activities()` で候補を表示し、ユーザーに選んでもらう
3. `check_in(activity_id=...)` を呼び出す
4. 返ってきた結果をもとに、以下の2セクション構成でユーザーに要約を伝える

## 出力フォーマット

```
check-in: {activity.title}

## 概要
{activity の description（なければ「(説明なし)」）}
topic: {topic.title}（topic がある場合のみ）

## 現在地
status: {activity.status} | mode: {タグから抽出した mode 値、なければ「(未設定)」} | notes: {tag_notes の件数}件 | 資材: {materials の件数}件 | decisions: {recent_decisions の件数}件
{recent_decisions の各タイトルを箇条書き（最大5件、件数が多い場合は「他N件」と補足）}
```
