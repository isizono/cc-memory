-- depends: 0022_add_detail_reminders

-- TODO 1: material_tags テーブル作成
CREATE TABLE material_tags (
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (material_id, tag_id)
);

-- TODO 2: topic_material_relations テーブル作成
CREATE TABLE topic_material_relations (
    topic_id INTEGER NOT NULL REFERENCES discussion_topics(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (topic_id, material_id)
);

-- TODO 3: activity_material_relations テーブル作成
CREATE TABLE activity_material_relations (
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (activity_id, material_id)
);

-- TODO 4: 逆方向参照用インデックス作成
CREATE INDEX idx_material_tags_tag ON material_tags(tag_id);
CREATE INDEX idx_topic_material_relations_material ON topic_material_relations(material_id);
CREATE INDEX idx_activity_material_relations_material ON activity_material_relations(material_id);

-- TODO 5: 既存データ移行 — activity_id → activity_material_relations
INSERT OR IGNORE INTO activity_material_relations (activity_id, material_id)
SELECT activity_id, id FROM materials WHERE activity_id IS NOT NULL;

-- TODO 6: 既存データ移行 — activity_tags → material_tags（activityのタグをコピー）
INSERT OR IGNORE INTO material_tags (material_id, tag_id)
SELECT m.id, at.tag_id
FROM materials m
JOIN activity_tags at ON at.activity_id = m.activity_id
WHERE m.activity_id IS NOT NULL;

-- TODO 7: relations_view 再作成（DROP VIEW + CREATE VIEW with materialセクション追加）
DROP VIEW relations_view;
CREATE VIEW relations_view AS
  SELECT topic_id_1 AS source_id, 'topic' AS source_type,
         topic_id_2 AS target_id, 'topic' AS target_type, created_at
  FROM topic_relations
  UNION ALL
  SELECT topic_id_2, 'topic', topic_id_1, 'topic', created_at
  FROM topic_relations
  UNION ALL
  SELECT topic_id, 'topic', activity_id, 'activity', created_at
  FROM topic_activity_relations
  UNION ALL
  SELECT activity_id, 'activity', topic_id, 'topic', created_at
  FROM topic_activity_relations
  UNION ALL
  SELECT activity_id_1, 'activity', activity_id_2, 'activity', created_at
  FROM activity_relations
  UNION ALL
  SELECT activity_id_2, 'activity', activity_id_1, 'activity', created_at
  FROM activity_relations
  UNION ALL
  SELECT topic_id, 'topic', material_id, 'material', created_at
  FROM topic_material_relations
  UNION ALL
  SELECT material_id, 'material', topic_id, 'topic', created_at
  FROM topic_material_relations
  UNION ALL
  SELECT activity_id, 'activity', material_id, 'material', created_at
  FROM activity_material_relations
  UNION ALL
  SELECT material_id, 'material', activity_id, 'activity', created_at
  FROM activity_material_relations;

-- TODO 8: materials.activity_id カラム DROP（先にインデックスを削除）
DROP INDEX IF EXISTS idx_materials_activity_id;
ALTER TABLE materials DROP COLUMN activity_id;

-- TODO 9: reminders #6, #7 の内容更新
UPDATE reminders SET content = 'logには資材のIDと概要だけを記載する。「SA案を採択した」というdecisionだけ残しても、SA案そのものはセッション終了で揮発する。資材として保存しておけば、search(type_filter="material")で検索でき、get_materialで全文取得できる。' WHERE id = 6;
UPDATE reminders SET content = 'materialは決定事項と違って「双方の合意」が不要な成果物の保存。調査結果・分析結果・ドキュメント等の成果物が出た時点で、ユーザーに確認せずadd_materialを呼ぶ。タグは必須。' WHERE id = 7;
