-- Migration 025: reminders→habitsリネーム
--
-- depends: 0024_tag_description
--
-- 変更内容:
--   - reminders → habits テーブルリネーム

ALTER TABLE reminders RENAME TO habits;
