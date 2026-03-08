-- Migration 009: タグ基盤の作成とsubjectsデータの移行
--
-- depends: 0008_add_log_search_index
--
-- 変更内容:
--   - tags テーブル作成（namespace + name のユニーク制約）
--   - 中間テーブル4つ作成（topic_tags, task_tags, decision_tags, log_tags）
--   - tag_vec 仮想テーブル作成（ベクトル検索用）
--   - 既存 subjects データを domain:タグに変換・移行
--   - discussion_topics の subject_id → topic_tags への紐付けコピー
--   - tasks の subject_id → task_tags への紐付けコピー

-- 1. tags テーブル作成
CREATE TABLE tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace TEXT NOT NULL DEFAULT '' CHECK(namespace IN ('', 'domain', 'scope', 'mode')),
  name TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(namespace, name)
);

-- 2. 中間テーブル作成

-- topic_tags
CREATE TABLE topic_tags (
  topic_id INTEGER NOT NULL REFERENCES discussion_topics(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (topic_id, tag_id)
);

-- task_tags
CREATE TABLE task_tags (
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, tag_id)
);

-- decision_tags
CREATE TABLE decision_tags (
  decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (decision_id, tag_id)
);

-- log_tags
CREATE TABLE log_tags (
  log_id INTEGER NOT NULL REFERENCES discussion_logs(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (log_id, tag_id)
);

-- 3. tag_vec 仮想テーブル作成（ベクトル検索用）
CREATE VIRTUAL TABLE tag_vec USING vec0(
  embedding float[384]
);

-- 4. データ移行: subjects → domain:タグに変換
INSERT OR IGNORE INTO tags (namespace, name)
SELECT 'domain', LOWER(REPLACE(REPLACE(name, ' ', '-'), '_', '-'))
FROM subjects;

-- 5. データ移行: topic_tags 紐付けコピー
INSERT OR IGNORE INTO topic_tags (topic_id, tag_id)
SELECT dt.id, t.id
FROM discussion_topics dt
JOIN subjects s ON dt.subject_id = s.id
JOIN tags t ON t.namespace = 'domain'
  AND t.name = LOWER(REPLACE(REPLACE(s.name, ' ', '-'), '_', '-'));

-- 6. データ移行: task_tags 紐付けコピー
INSERT OR IGNORE INTO task_tags (task_id, tag_id)
SELECT tk.id, t.id
FROM tasks tk
JOIN subjects s ON tk.subject_id = s.id
JOIN tags t ON t.namespace = 'domain'
  AND t.name = LOWER(REPLACE(REPLACE(s.name, ' ', '-'), '_', '-'));
