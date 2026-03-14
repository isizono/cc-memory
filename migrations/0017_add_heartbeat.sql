-- depends: 0016_add_activity_topic_id
ALTER TABLE activities ADD COLUMN last_heartbeat_at TEXT;
