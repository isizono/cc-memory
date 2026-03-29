-- depends: 0027_add_shelved_status

CREATE TABLE activity_dependencies (
    dependent_id  INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    dependency_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (dependent_id, dependency_id),
    CHECK (dependent_id != dependency_id)
);

CREATE INDEX idx_activity_dependencies_dependency ON activity_dependencies(dependency_id);

DROP VIEW relations_view;
CREATE VIEW relations_view AS
  SELECT topic_id_1 AS source_id, 'topic' AS source_type,
         topic_id_2 AS target_id, 'topic' AS target_type,
         'related' AS relation_type, created_at
  FROM topic_relations
  UNION ALL
  SELECT topic_id_2, 'topic', topic_id_1, 'topic', 'related', created_at
  FROM topic_relations
  UNION ALL
  SELECT topic_id, 'topic', activity_id, 'activity', 'related', created_at
  FROM topic_activity_relations
  UNION ALL
  SELECT activity_id, 'activity', topic_id, 'topic', 'related', created_at
  FROM topic_activity_relations
  UNION ALL
  SELECT activity_id_1, 'activity', activity_id_2, 'activity', 'related', created_at
  FROM activity_relations
  UNION ALL
  SELECT activity_id_2, 'activity', activity_id_1, 'activity', 'related', created_at
  FROM activity_relations
  UNION ALL
  SELECT topic_id, 'topic', material_id, 'material', 'related', created_at
  FROM topic_material_relations
  UNION ALL
  SELECT material_id, 'material', topic_id, 'topic', 'related', created_at
  FROM topic_material_relations
  UNION ALL
  SELECT activity_id, 'activity', material_id, 'material', 'related', created_at
  FROM activity_material_relations
  UNION ALL
  SELECT material_id, 'material', activity_id, 'activity', 'related', created_at
  FROM activity_material_relations
  UNION ALL
  -- depends_on: 非対称（双方向化しない）
  SELECT dependent_id, 'activity', dependency_id, 'activity',
         'depends_on', created_at
  FROM activity_dependencies;
