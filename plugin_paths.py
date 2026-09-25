#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
插件目录布局 —— 内置插件与用户插件分离。

    parsers_builtin/    内置解析器（随镜像发布，不打 Volume）
    parsers/            用户上传的解析器（Docker 中挂 Volume 持久化）
    channels_builtin/   内置通道插件（随镜像发布，不打 Volume）
    channels/           用户上传的通道插件（Docker 中挂 Volume 持久化）

为什么必须分开：
  - 用户目录挂 Volume 后，容器重建不会丢用户插件；
  - 内置目录**不能**打 Volume —— named volume 首次创建会把镜像里该路径的内容
    拷进去，之后以卷为准，镜像升级再也更新不到内置插件。
  - 两者同目录时无解：要么丢用户文件，要么内置永远升不上去。

解析顺序：**用户目录优先**，其次内置目录。同名上传直接拒绝
（`conflict_reason`），避免"本地放了个同名插件，以为改了却没生效"这类难排查问题。

文件名在数据库里始终是裸名（如 `emby.py`），路径由本模块解析 —— 因此拆分目录
不影响已有配置。
"""

import os

_ROOT = os.path.dirname(os.path.abspath(__file__))

PARSERS_USER = os.path.join(_ROOT, "parsers")
PARSERS_BUILTIN = os.path.join(_ROOT, "parsers_builtin")
CHANNELS_USER = os.path.join(_ROOT, "channels")
CHANNELS_BUILTIN = os.path.join(_ROOT, "channels_builtin")

_DIRS = {
    "parser": ("PARSERS_USER", "PARSERS_BUILTIN"),
    "channel": ("CHANNELS_USER", "CHANNELS_BUILTIN"),
}

_KIND_LABEL = {"parser": "解析器", "channel": "通道插件"}


def _dirs(kind):
    """按名字在调用时取目录，而不是在导入时固化 —— 便于测试替换目录、
    也便于将来用环境变量覆盖。"""
    try:
        names = _DIRS[kind]
    except KeyError:
        raise ValueError(f"unknown plugin kind: {kind!r} (expect 'parser' or 'channel')")
    return tuple(globals()[n] for n in names)


def ensure_user_dirs():
    """确保用户插件目录存在（裸机运行时它们不在版本库里）。"""
    for kind in _DIRS:
        os.makedirs(_dirs(kind)[0], exist_ok=True)


def user_dir(kind):
    return _dirs(kind)[0]


def builtin_dir(kind):
    return _dirs(kind)[1]


def resolve(kind, filename):
    """按「用户 → 内置」顺序解析插件文件，返回绝对路径；找不到返回 None。"""
    if not filename or os.path.basename(filename) != filename:
        # 只接受裸文件名，挡掉 ../ 之类的路径穿越
        return None
    for d in _dirs(kind):
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return None


def channel_filename(channel_type):
    """通道实例里存的是**类型名**（wechat_work_bot），插件文件是 `类型名.py`。

    统一在这里转换，避免各处自行拼 `.py` 而漏掉某一处。
    """
    t = str(channel_type or "").strip()
    if not t:
        return ""
    return t if t.endswith(".py") else f"{t}.py"


def source_of(kind, filename):
    """返回 'user' / 'builtin' / None（不存在）。"""
    user, builtin = _dirs(kind)
    if os.path.isfile(os.path.join(user, filename)):
        return "user"
    if os.path.isfile(os.path.join(builtin, filename)):
        return "builtin"
    return None


def is_builtin(kind, filename):
    return source_of(kind, filename) == "builtin"


def conflict_reason(kind, filename):
    """上传前的同名校验。

    返回 None 表示可以上传；否则返回给用户看的原因。
    规则：与内置插件同名 → 拒绝（要改动内置插件请另存为别的名字，
    或直接在本地改源码后重建镜像），避免"以为生效了其实没有"。
    """
    if not filename:
        return "缺少文件名"
    if os.path.basename(filename) != filename:
        return "文件名不合法"
    if not filename.endswith(".py") or filename.startswith("_"):
        return "只接受不以 _ 开头的 .py 文件"
    if is_builtin(kind, filename):
        return (f"与内置{_KIND_LABEL[kind]} `{filename}` 同名。"
                f"请改用其他文件名（内置{_KIND_LABEL[kind]}随镜像更新，"
                f"同名文件不会被使用）")
    return None


def list_plugins(kind):
    """列出两类目录中的插件（同名时用户版本优先），按文件名排序。

    每项：{"filename", "name", "source", "path", "exists"}
    """
    user, builtin = _dirs(kind)
    out = {}
    for source, d in (("builtin", builtin), ("user", user)):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".py") or f.startswith("_") or f == "__init__.py":
                continue
            # 用户目录后扫，自然覆盖同名的内置项（用户优先）
            out[f] = {
                "filename": f,
                "name": f.replace(".py", ""),
                "source": source,
                "path": os.path.join(d, f),
                "exists": os.path.isfile(os.path.join(d, f)),
            }
    return [out[k] for k in sorted(out)]


def read_source_meta(path, prefix):
    """从插件源码里读 `{prefix}_NAME/_DESC/_VERSION`（正则，不执行代码）。

    解析器用这个（避免为了列表接口去执行每个文件）；通道直接用类属性。
    """
    import re
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception:
        return {}

    def grab(key):
        m = re.search(r'^\s*%s\s*=\s*["\'](.+?)["\']' % key, src, re.M)
        return m.group(1) if m else ""

    return {
        "name": grab(f"{prefix}_NAME"),
        "desc": grab(f"{prefix}_DESC"),
        "version": grab(f"{prefix}_VERSION"),
    }
