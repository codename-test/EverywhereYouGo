#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Emby/Jellyfin 解析器（参考实现）。
明确列出每个字段的来源路径和变量名。
"""
import json


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    if not raw_body or not raw_body.strip():
        raise ValueError("请求体为空")
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        raise ValueError(f"请求体不是合法 JSON: {raw_body[:200]}")
    
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    media_type = data.get("Item", {}).get("Type", "")
    year = data.get("Item", {}).get("ProductionYear", "")
    overview = data.get("Item", {}).get("Overview", "")
    server_name = data.get("Server", {}).get("Name", "")
    server_url = data.get("Server", {}).get("Url", "")
    
    title = name if name else event

    return {
        "title": str(title),
        "event": str(event),
        "name": str(name),
        "media_type": str(media_type),
        "year": str(year),
        "overview": str(overview),
        "server_name": str(server_name),
        "server_url": str(server_url),
    }
