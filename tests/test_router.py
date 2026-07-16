# tests/test_router.py
"""路由条件匹配测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from router import match_rules


class TestEmptyCondition:
    """空条件 = 默认匹配所有。"""

    def test_empty_condition_matches(self):
        bindings = [{"enabled": True, "condition_expr": "", "priority": 0}]
        msg = {"event": "library.new", "name": "test"}
        assert len(match_rules(bindings, msg)) == 1

    def test_whitespace_condition_matches(self):
        bindings = [{"enabled": True, "condition_expr": "  ", "priority": 0}]
        msg = {"event": "library.new"}
        assert len(match_rules(bindings, msg)) == 1


class TestSimpleConditions:
    """简单条件表达式。"""

    def test_equality_match(self):
        bindings = [{"enabled": True, "condition_expr": "event == 'library.new'", "priority": 0}]
        msg = {"event": "library.new", "name": "test"}
        assert len(match_rules(bindings, msg)) == 1

    def test_equality_no_match(self):
        bindings = [{"enabled": True, "condition_expr": "event == 'library.new'", "priority": 0}]
        msg = {"event": "playback.stop", "name": "test"}
        assert len(match_rules(bindings, msg)) == 0

    def test_inequality(self):
        bindings = [{"enabled": True, "condition_expr": "event != 'test'", "priority": 0}]
        msg = {"event": "library.new"}
        assert len(match_rules(bindings, msg)) == 1

    def test_in_operator_not_supported(self):
        """simpleeval 默认不支持 List/Tuple 字面量，in 操作符会失败。"""
        bindings = [{"enabled": True, "condition_expr": "media_type in ['Movie', 'Series']", "priority": 0}]
        msg = {"media_type": "Movie"}
        # 表达式求值失败，router 捕获异常返回 False
        assert len(match_rules(bindings, msg)) == 0

    def test_in_operator_workaround(self):
        """用 or 条件替代 in 操作符。"""
        bindings = [{"enabled": True,
                     "condition_expr": "media_type == 'Movie' or media_type == 'Series'",
                     "priority": 0}]
        msg = {"media_type": "Movie"}
        assert len(match_rules(bindings, msg)) == 1


class TestCompoundConditions:
    """复合条件表达式。"""

    def test_and_both_true(self):
        bindings = [{"enabled": True,
                     "condition_expr": "event == 'library.new' and media_type == 'Movie'",
                     "priority": 0}]
        msg = {"event": "library.new", "media_type": "Movie"}
        assert len(match_rules(bindings, msg)) == 1

    def test_and_one_false(self):
        bindings = [{"enabled": True,
                     "condition_expr": "event == 'library.new' and media_type == 'Movie'",
                     "priority": 0}]
        msg = {"event": "library.new", "media_type": "Series"}
        assert len(match_rules(bindings, msg)) == 0

    def test_or_one_true(self):
        bindings = [{"enabled": True,
                     "condition_expr": "event == 'library.new' or event == 'test'",
                     "priority": 0}]
        msg = {"event": "test"}
        assert len(match_rules(bindings, msg)) == 1

    def test_or_both_false(self):
        bindings = [{"enabled": True,
                     "condition_expr": "event == 'library.new' or event == 'test'",
                     "priority": 0}]
        msg = {"event": "playback.stop"}
        assert len(match_rules(bindings, msg)) == 0

    def test_grouped_expression(self):
        bindings = [{"enabled": True,
                     "condition_expr": "(event == 'library.new' or event == 'test') and media_type == 'Movie'",
                     "priority": 0}]
        msg = {"event": "test", "media_type": "Movie"}
        assert len(match_rules(bindings, msg)) == 1


class TestBindingFilters:
    """绑定过滤和排序。"""

    def test_disabled_binding_skipped(self):
        bindings = [{"enabled": False, "condition_expr": "", "priority": 0}]
        msg = {"event": "library.new"}
        assert len(match_rules(bindings, msg)) == 0

    def test_mixed_enabled_disabled(self):
        bindings = [
            {"enabled": True, "condition_expr": "", "priority": 0},
            {"enabled": False, "condition_expr": "", "priority": 1},
        ]
        msg = {"event": "library.new"}
        assert len(match_rules(bindings, msg)) == 1

    def test_priority_sorting(self):
        bindings = [
            {"enabled": True, "condition_expr": "", "priority": 5},
            {"enabled": True, "condition_expr": "", "priority": 1},
            {"enabled": True, "condition_expr": "", "priority": 3},
        ]
        msg = {"event": "library.new"}
        result = match_rules(bindings, msg)
        assert [r["priority"] for r in result] == [1, 3, 5]

    def test_multiple_matching_bindings(self):
        bindings = [
            {"enabled": True, "condition_expr": "event == 'library.new'", "priority": 0},
            {"enabled": True, "condition_expr": "", "priority": 1},
        ]
        msg = {"event": "library.new"}
        assert len(match_rules(bindings, msg)) == 2


class TestEdgeCases:
    """边界情况。"""

    def test_empty_bindings(self):
        assert match_rules([], {"event": "test"}) == []

    def test_invalid_expression_returns_false(self):
        bindings = [{"enabled": True, "condition_expr": "invalid syntax !!!", "priority": 0}]
        msg = {"event": "test"}
        assert len(match_rules(bindings, msg)) == 0

    def test_missing_field_in_msg(self):
        bindings = [{"enabled": True, "condition_expr": "nonexistent == 'value'", "priority": 0}]
        msg = {"event": "test"}
        # simpleeval 会因变量不存在而抛异常，router 捕获后返回 False
        assert len(match_rules(bindings, msg)) == 0

    def test_numeric_comparison(self):
        bindings = [{"enabled": True, "condition_expr": "year > 2020", "priority": 0}]
        msg = {"year": 2024}
        assert len(match_rules(bindings, msg)) == 1

    def test_boolean_comparison(self):
        bindings = [{"enabled": True, "condition_expr": "active == True", "priority": 0}]
        msg = {"active": True}
        assert len(match_rules(bindings, msg)) == 1
