## 開発フロー

- 実装前に関連トピックのget_decisionsを取得し、ユーザーに仕様確認を取ってから着手する
- cc-memoryプラグインがある場合、コードベース調査の前にまず既存記録で文脈を取得すること

## コミット規約

Conventional Commits形式（scopeなし）。typeは英語、subjectは日本語。

- `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
- 例: `feat: searchにrecency boost追加`
- bodyは変更理由が自明でない場合のみ

## ブランチ戦略

- main直push禁止（pre-pushフックで防止）
- mainの作業ディレクトリ（プロジェクトルート）でコード変更を行わないこと。ファイル編集は必ずworktree内で行う
- ブランチ作業は必ずgit worktreeで行うこと（作業ディレクトリで直接checkoutしない）
- worktreeは`.trees/`配下に作成する
- ブランチは必ずorigin/mainの最新から切る
- 命名: `feature/<要約>`, `fix/<要約>`, `docs/<要約>`（英語ケバブケース）

## PRマージ後の反映手順

cc-memoryはローカルディレクトリをmarketplaceとして登録しており、mainブランチからプラグインキャッシュが生成される。PRマージ後は以下を実行する:

1. `git pull origin main`
2. マージ済みworktreeを削除: `git worktree remove .trees/<name>`
3. ローカルブランチを削除: `git branch -D <branch>`
4. プラグインキャッシュを削除: `rm -rf ~/.claude/plugins/cache/claude-code-memory-marketplace/`
5. `__pycache__` を削除: `find . -type d -name __pycache__ -exec rm -rf {} +`
6. Claude Codeを再起動
