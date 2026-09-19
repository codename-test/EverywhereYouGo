#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""API 入参校验（improvement #27）。

自管理场景下，输入写错的代价主要由 operator 自己承担，所以这里**不引入**
JSON Schema 重型框架，只做一组轻量助手，把「缺字段 / 类型不对 / 取值越界」
这类会直接变成 500 或写出无效配置的情况，转成明确的 400 + 可读错误。

用法：
    from api.validation import require_name, optional_port, ValidationError

    name = require_name(data)          # 缺了会抛 ValidationError
    port = optional_port(data)

`api/__init__.py` 注册了 ValidationError 的统一错误处理，自动返回 400，
因此业务代码里直接 raise 即可。
"""

import re

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
TEMPLATE_ENGINES = ("jinja2", "simple")


class ValidationError(ValueError):
    """入参不合法（API 层统一转 400）。"""


def _body(data):
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data


def require_name(data, key="name", max_len=100):
    """必填的非空字符串字段（自动 strip）。"""
    v = _body(data).get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"{key} is required and must be a non-empty string")
    v = v.strip()
    if len(v) > max_len:
        raise ValidationError(f"{key} too long (max {max_len})")
    return v


def optional_str(data, key, max_len=500, default=None, allow_empty=True):
    v = _body(data).get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise ValidationError(f"{key} must be a string")
    if not allow_empty and not v.strip():
        raise ValidationError(f"{key} must not be empty")
    if len(v) > max_len:
        raise ValidationError(f"{key} too long (max {max_len})")
    return v


def optional_int(data, key, min_value=None, max_value=None, default=None):
    v = _body(data).get(key, default)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ValidationError(f"{key} must be an integer")
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValidationError(f"{key} must be an integer")
    if min_value is not None and v < min_value:
        raise ValidationError(f"{key} must be >= {min_value}")
    if max_value is not None and v > max_value:
        raise ValidationError(f"{key} must be <= {max_value}")
    return v


def optional_port(data, key="port"):
    """端口：1-65535，空/None 表示不启用端口模式。"""
    return optional_int(data, key, 1, 65535, default=None)


def optional_flag(data, key, default=None):
    """0/1 标志位。"""
    v = _body(data).get(key, default)
    if v is None:
        return default
    if isinstance(v, bool):
        return 1 if v else 0
    if v in (0, 1, "0", "1"):
        return int(v)
    raise ValidationError(f"{key} must be 0 or 1")


def optional_slug(data, key="slug"):
    """路径路由用的 slug：字母数字下划线连字符。"""
    v = _body(data).get(key)
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _SLUG_RE.match(v):
        raise ValidationError(f"{key} must match [A-Za-z0-9_-]{{1,64}}")
    return v


def optional_enum(data, key, allowed, default=None):
    v = _body(data).get(key, default)
    if v is None:
        return default
    if v not in allowed:
        raise ValidationError(f"{key} must be one of {sorted(allowed)}")
    return v


def optional_hhmm(data, key, default=None):
    """HH:MM 时刻（用于免打扰时段）。"""
    v = _body(data).get(key, default)
    if v is None:
        return default
    if not isinstance(v, str) or not _HHMM_RE.match(v):
        raise ValidationError(f"{key} must be HH:MM (00:00-23:59)")
    return v


def optional_id_list(data, key="ids"):
    """整数 ID 列表。"""
    v = _body(data).get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValidationError(f"{key} must be a list")
    out = []
    for i in v:
        if isinstance(i, bool) or not isinstance(i, (int, str)):
            raise ValidationError(f"{key} must contain only integers")
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            raise ValidationError(f"{key} must contain only integers")
    return out
