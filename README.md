# claude-code-memory

Claude Codeの記憶を外部DBに保存し、セッション間で知識・決定事項・タスクを永続化するプラグイン。

## 機能

- **トピック管理**: 議論トピックを階層構造で管理
- **決定事項記録**: 合意形成した内容を記録・検索
- **タスク管理**: 作業タスクのステータス管理
- **ナレッジ保存**: 調査結果をマークダウンファイルとして保存
- **横断検索**: トピック・決定事項・タスクをキーワードで一括検索

## インストール

### 前提条件

- [uv](https://docs.astral.sh/uv/) がインストールされていること
- Claude Code v2.0.12以上

### インストール手順

```bash
# 1. マーケットプレイスを追加
claude plugin marketplace add isizono/claude-code-memory

# 2. プラグインをインストール
claude plugin install claude-code-memory
```

### 開発版を試す場合

```bash
# リポジトリをクローン
git clone https://github.com/isizono/claude-code-memory.git

# プラグインディレクトリを指定して起動
claude --plugin-dir /path/to/claude-code-memory
```

## 提供されるMCPツール

| カテゴリ | ツール | 説明 |
|---------|--------|------|
| サブジェクト | `add_subject`, `list_subjects`, `update_subject` | サブジェクト管理 |
| トピック | `add_topic`, `get_topics` | 議論トピック管理 |
| ログ | `add_log`, `get_logs` | 議論ログ記録 |
| 決定 | `add_decision`, `get_decisions` | 決定事項管理 |
| タスク | `add_task`, `get_tasks`, `update_task` | タスク管理 |
| 検索 | `search`, `get_by_id` | FTS5横断検索 |
| ナレッジ | `add_knowledge` | ナレッジファイル保存 |

## 提供されるスキル

| スキル | 説明 |
|--------|------|
| `/research` | 調査タスクをサブエージェントに委譲 |
| `/task-delegate` | 複数タスクをまとめて実装→レビュー |
| `/task-iterate` | 探索的タスクを試行錯誤で実行 |

## ディレクトリ構成

```
claude-code-memory/
├── .claude-plugin/
│   └── plugin.json          # プラグインマニフェスト
├── .mcp.json                 # MCPサーバー設定
├── hooks/
│   └── hooks.json            # フック設定
├── scripts/                  # フックスクリプト
├── commands/                 # スラッシュコマンド
├── skills/                   # スキル定義
├── rules/                    # ルール定義
├── src/                      # MCPサーバー本体
└── schema.sql                # DBスキーマ
```

## 設定

### 環境変数

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `DISCUSSION_DB_PATH` | SQLiteデータベースのパス | `~/.claude/.claude-code-memory/discussion.db` |
| `KNOWLEDGE_ROOT` | ナレッジファイルの保存先 | `~/.claude/knowledge/` |

## ライセンス

MIT
