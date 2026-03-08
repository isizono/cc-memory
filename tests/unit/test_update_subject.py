"""update_subjectのユニットテスト

subjects テーブルは Contract migration (0010) で削除済みのため、
全テストをスキップする。
"""
import pytest

pytestmark = pytest.mark.skip("subjects table removed by Contract migration #0010")


def test_update_name():
    pass


def test_update_description():
    pass


def test_all_none_returns_validation_error():
    pass


def test_not_found():
    pass


def test_empty_name():
    pass


def test_whitespace_name():
    pass


def test_empty_description():
    pass


def test_duplicate_name():
    pass
