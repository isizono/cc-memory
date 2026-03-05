## 開発フロー

- 実装前に関連トピックのget_decisionsを取得し、ユーザーに仕様確認を取ってから着手する
- cc-memoryプラグインがある場合、コードベース調査の前にまず既存記録で文脈を取得すること

## コミット規約

Conventional Commits形式（scopeなし）。typeは英語、subjectは日本語。

- `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
- 例: `feat: tasksテーブルのスキーマを実装`
- bodyは変更理由が自明でない場合のみ

## ブランチ戦略

- main直push禁止（pre-pushフックで防止）
- mainの作業ディレクトリ（プロジェクトルート）でコード変更を行わないこと。ファイル編集は必ずworktree内で行う
- ブランチ作業は必ずgit worktreeで行うこと（作業ディレクトリで直接checkoutしない）
- worktreeは`.trees/`配下に作成する
- ブランチは必ずorigin/mainの最新から切る
- 命名: `feature/<要約>`, `fix/<要約>`, `docs/<要約>`（英語ケバブケース）
