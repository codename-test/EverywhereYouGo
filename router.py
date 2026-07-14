#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
路由引擎。
根据 condition_expr 匹配 route_tags，决定分发到哪些渠道+模板组合。
"""

import log
from typing import List, Tuple

# 条件表达式求值（安全子集）
try:
    from simpleeval import simple_eval
    HAS_SIMPLEEVAL = True
except ImportError:
    HAS_SIMPLEEVAL = False
    log.logger.warning("simpleeval not installed, condition expressions disabled")


def match_rules(source_channels: list, route_tags: dict) -> list:
    """
    从 source_channels 列表中筛选匹配的绑定。

    Args:
        source_channels: db.get_source_channels() 返回的列表 (dict)
        route_tags:      解析器返回的 route_tags

    Returns:
        匹配的 source_channel 列表，按 priority 升序
    """
    matched = []
    for sc in source_channels:
        if not sc.get("enabled"):
            continue
        condition = (sc.get("condition_expr") or "").strip()
        if condition:
            if _eval_condition(condition, route_tags):
                matched.append(sc)
        else:
            # 空条件 = 默认匹配
            matched.append(sc)
    return sorted(matched, key=lambda x: x.get("priority", 0))


def _eval_condition(expr: str, route_tags: dict) -> bool:
    """安全求值条件表达式。"""
    if not HAS_SIMPLEEVAL:
        log.logger.warning(f"Cannot evaluate condition (simpleeval missing): {expr}")
        return False

    # 将 route_tags 的每个 key 作为变量传给 simpleeval
    names = {}
    for k, v in route_tags.items():
        # 仅允许简单类型
        if isinstance(v, (str, int, float, bool)):
            names[k] = v

    try:
        return bool(simple_eval(expr, names=names))
    except Exception as e:
        log.logger.error(f"Condition eval error: {expr} -> {e}")
        return False
