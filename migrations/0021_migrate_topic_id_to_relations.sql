-- depends: 0020_add_relation_tables

-- 既存 activities.topic_id データを topic_activity_relations に移行
INSERT OR IGNORE INTO topic_activity_relations (topic_id, activity_id)
SELECT topic_id, id FROM activities WHERE topic_id IS NOT NULL;

-- topic_id カラムを DROP (SQLite 3.35.0+)
ALTER TABLE activities DROP COLUMN topic_id;
