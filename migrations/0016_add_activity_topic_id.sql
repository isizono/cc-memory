-- Migration 016: activitiesテーブルにtopic_idカラムを復活
--
-- depends: 0015_tag_canonical
--
-- 変更内容:
--   - activities.topic_id カラム追加（nullable, FK → discussion_topics.id）
--   - migration 0010で削除されたtopic_idを1:N関係で再追加

ALTER TABLE activities ADD COLUMN topic_id INTEGER REFERENCES discussion_topics(id);
