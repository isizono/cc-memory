---
name: check-in
description: アクティビティにcheck-inして関連情報を集約取得する
---

# check-in

指定されたアクティビティに対して `check_in` ツールを呼び出し、関連情報を調べてアクティビティの全体像と進捗を把握してください。check-in後にユーザーが「やって」と言えばすぐ作業・議論を開始できる状態にすることがゴールです。

## 手順

1. 引数で `activity_id` が指定されていればそのまま使う
2. 指定されていなければ `get_activities()` で候補を表示し、ユーザーに選んでもらう
3. `check_in(activity_id=...)` を呼び出す
4. `get_logs`・`get_decisions`・`search` などで関連情報を取得し、概要と進捗を把握する
5. 把握した内容を以下の2セクション構成でユーザーに伝える

## 出力フォーマット

```
check-in: {activity.title}

## 概要
{タスクの背景・目的・やることがユーザーに伝わる程度にまとめる。activity.descriptionと関連情報をもとに構成する}

## 進捗
status: {activity.status} | intent: {タグから抽出した intent 値、なければ省略}
{logs・decisions・materialsなどから読み取れる、実際にどこまで進んでいるか・何が残っているかの要約}
```
