-- Migration 032: materialsにsourceカラムを追加
--
-- depends: 0031_add_retracted_at
--
-- 背景:
--   materialの出自（どこから来た情報か）を明示的に記録する。
--   tool description指示だけではLLMがソース記載を従わない問題への対策として、
--   API必須化で物理的にソース未記載のmaterialが作成されないようにする。
--
-- 変更内容:
--   materialsテーブルにsource TEXT NOT NULL DEFAULT 'unknown'を追加。
--   既存データはDEFAULT値 'unknown' で動作する。

ALTER TABLE materials ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown';
