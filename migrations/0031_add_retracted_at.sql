-- Migration 031: decisions, discussion_logsにretracted_atカラムを追加
--
-- depends: 0030_add_search_index_created_at
--
-- 背景:
--   誤った決定事項やログを論理削除（取り消し）するための準備。
--   NULLが有効（未取り消し）、値ありが取り消し済み。
--
-- 変更内容:
--   decisions, discussion_logsの2テーブルにretracted_at TIMESTAMP NULLを追加

ALTER TABLE decisions ADD COLUMN retracted_at TIMESTAMP NULL;
ALTER TABLE discussion_logs ADD COLUMN retracted_at TIMESTAMP NULL;
