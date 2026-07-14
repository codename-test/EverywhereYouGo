#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Emby/Jellyfin 解析器（参考实现）。
明确列出每个字段的来源路径和变量名。
"""
import json


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    
    # 从 JSON 中提取每个字段，定义明确的变量名
    event = data.get("Event", "")
    name = data.get("Item", {}).get("Name", "")
    media_type = data.get("Item", {}).get("Type", "")
    year = data.get("Item", {}).get("ProductionYear", "")
    overview = data.get("Item", {}).get("Overview", "")
    server_name = data.get("Server", {}).get("Name", "")
    server_url = data.get("Server", {}).get("Url", "")
    
    # 选择最合适的字段作为标题
    title = name if name else event
    
    # 将变量按 Markdown 列表格式组织成内容
    content_lines = [
        f"- **event**: {event}",
        f"- **name**: {name}",
        f"- **media_type**: {media_type}",
        f"- **year**: {year}",
        f"- **overview**: {overview}",
        f"- **server_name**: {server_name}",
        f"- **server_url**: {server_url}",
    ]
    content = "\n".join(content_lines)
    
    # 返回所有变量（全部转为字符串）
    return {
        "title": str(title),
        "content": str(content),
        "event": str(event),
        "name": str(name),
        "media_type": str(media_type),
        "year": str(year),
        "overview": str(overview),
        "server_name": str(server_name),
        "server_url": str(server_url),
    }
