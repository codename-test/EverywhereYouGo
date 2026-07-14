#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Emby / Jellyfin Webhook 解析器（内置参考）。
"""
import json


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    data = json.loads(raw_body)
    item = data.get("Item", {})
    event = data.get("Event", "未知事件")

    name = item.get("Name", "未知")
    item_type = item.get("Type", "")
    year = item.get("ProductionYear", "")
    overview = item.get("Overview", "") or ""

    # 剧集信息
    series_name = item.get("SeriesName", "")
    season = ""
    episode = ""
    episode_name = ""
    if item_type == "Episode":
        season = str(item.get("ParentIndexNumber", ""))
        episode = str(item.get("IndexNumber", ""))
        episode_name = item.get("Name", "")

    title = f"🎬 {name}"
    if year:
        title += f" ({year})"

    event_map = {
        "library.new":         "📥 新媒体入库",
        "playback.start":      "▶️ 开始播放",
        "playback.stop":       "⏹️ 停止播放",
        "item.rate":           "⭐ 用户评分",
        "user.authenticated":  "🔑 用户登录",
    }
    event_cn = event_map.get(event, f"📢 {event}")

    # 构建 content
    lines = []
    if item_type == "Movie":
        lines.append(f"🎬 {name} ({year})" if year else f"🎬 {name}")
    elif item_type == "Episode":
        ep_str = f"S{season}E{episode} {episode_name}" if season and episode else name
        se_str = series_name or "未知剧集"
        lines.append(f"📺 {se_str}")
        lines.append(f"   {ep_str}")
    else:
        lines.append(f"📦 {name}")

    if overview:
        lines.append("")
        lines.append(overview[:300])

    return {
        "title":   title,
        "content": "\n".join(lines),
        "summary": overview[:100] if overview else f"{event_cn}: {name}",
        "url":     data.get("Server", {}).get("Url", ""),
        "image_url": "",

        "route_tags": {
            "event":      event,
            "media_type": item_type,
        },

        "tags": {
            "event_cn":     event_cn,
            "name":         name,
            "year":         str(year),
            "series_name":  series_name,
            "season":       season,
            "episode":      episode,
            "episode_name": episode_name,
            "overview":     overview,
        },
    }
