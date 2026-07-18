#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/backup.py — 导出/备份/恢复/导入"""

import os
import io
import json
import zipfile
import datetime as _dt

import db
import parser_loader
import i18n
from flask import Blueprint, request, jsonify, Response, send_file

backup_bp = Blueprint("backup", __name__)

PARSERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")
VERSION = "1.0.1"


# ── Export helpers ──

def _export_source(s):
    return {k: s[k] for k in ("id", "name", "port", "parser_id", "enabled", "created_at")}


def _export_parser(p):
    return {k: p[k] for k in ("id", "name", "filename", "description", "created_at")}


def _export_parser_with_code(p):
    data = _export_parser(p)
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            data["code"] = f.read()
    else:
        data["code"] = ""
    return data


def _export_channel(c):
    return {k: c[k] for k in ("id", "name", "type", "config", "enabled", "created_at")}


def _export_template(t):
    return {k: t[k] for k in ("id", "name", "engine", "title_tpl", "content_tpl", "created_at")}


@backup_bp.route("/api/export/all", methods=["GET"])
def api_export_all():
    config_items = db._conn().execute("SELECT * FROM system_config").fetchall()
    payload = {
        "version": VERSION,
        "exported_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parsers": [_export_parser_with_code(p) for p in db.get_parsers()],
        "sources": [_export_source(s) for s in db.get_sources()],
        "channels": [_export_channel(c) for c in db.get_channels()],
        "templates": [_export_template(t) for t in db.get_templates()],
        "source_channels": db.get_all_source_channels(),
        "system_config": {r["key"]: r["value"] for r in config_items},
    }
    return jsonify(payload)


@backup_bp.route("/api/export/<item_type>/<int:item_id>", methods=["GET"])
def api_export_single(item_type, item_id):
    if item_type == "parser":
        item = db.get_parser(item_id)
        if not item:
            return jsonify({"error": i18n._("err.not_found")}), 404
        fpath = os.path.join(PARSERS_DIR, item["filename"])
        if not os.path.isfile(fpath):
            return jsonify({"error": i18n._("err.file_not_found_disk")}), 404
        return send_file(fpath, as_attachment=True, download_name=item["filename"])

    getter = {
        "source": (db.get_source, _export_source),
        "channel": (db.get_channel, _export_channel),
        "template": (db.get_template, _export_template),
    }
    pair = getter.get(item_type)
    if not pair:
        return jsonify({"error": i18n._("err.unknown_type").replace("{type}", item_type)}), 400
    fn_get, fn_export = pair
    item = fn_get(item_id)
    if not item:
        return jsonify({"error": i18n._("err.not_found")}), 404
    return jsonify(fn_export(item))


@backup_bp.route("/api/backup", methods=["GET"])
def api_backup():
    from config_manager import CONFIG_DIR, _CONFIG_FILES
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, fn in _CONFIG_FILES.items():
            p = os.path.join(CONFIG_DIR, fn)
            if os.path.isfile(p):
                zf.write(p, f"config/{fn}")
        for f in os.listdir(PARSERS_DIR):
            if f.endswith(".py"):
                zf.write(os.path.join(PARSERS_DIR, f), f"parsers/{f}")
        zf.writestr("version.txt", VERSION)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=ego_backup.zip"})


@backup_bp.route("/api/restore", methods=["POST"])
def api_restore():
    from config_manager import CONFIG_DIR
    if "file" not in request.files:
        return jsonify({"ok": False, "error": i18n._("err.upload_file_required")})

    file = request.files["file"]
    dry_run = request.args.get("dry_run") == "1"

    try:
        zf = zipfile.ZipFile(io.BytesIO(file.read()))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{i18n._('err.zip_parse_fail')} {e}"})

    config_files = [n for n in zf.namelist() if n.startswith("config/")]
    parser_files = [n for n in zf.namelist() if n.startswith("parsers/")]

    result = {"ok": True, "dry_run": dry_run, "config": config_files, "parsers": parser_files}

    if dry_run:
        return jsonify(result)

    for name in config_files:
        if name.endswith(".json"):
            target = os.path.join(CONFIG_DIR, name[len("config/"):])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(zf.read(name))

    for name in parser_files:
        if name.endswith(".py"):
            target = os.path.join(PARSERS_DIR, name[len("parsers/"):])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(zf.read(name))

    zf.close()

    try:
        import config_manager
        config_manager.load_all()
    except Exception as e:
        result["load_error"] = str(e)

    result["ok"] = True
    return jsonify(result)


# ── Import (legacy JSON) ──


def _import_preview(data, mode="insert"):
    existing = {
        "parsers": {p["id"]: p for p in db.get_parsers()},
        "sources": {s["id"]: s for s in db.get_sources()},
        "channels": {c["id"]: c for c in db.get_channels()},
        "templates": {t["id"]: t for t in db.get_templates()},
    }
    diff = {"parsers": [], "sources": [], "channels": [], "templates": [],
            "source_channels": [], "system_config": []}
    errors = []
    deps = {"missing": [], "auto_import": []}

    for p in data.get("parsers", []):
        pid = p.get("id")
        name = p.get("name", "?")
        exist = existing["parsers"].get(pid)
        if exist:
            if mode == "overwrite":
                diff["parsers"].append({"action": "update", "id": pid, "name": name})
            else:
                diff["parsers"].append({"action": "skip", "id": pid, "name": name,
                                        "reason": i18n._("import.already_exists")})
        else:
            diff["parsers"].append({"action": "insert", "id": pid, "name": name})

    for s in data.get("sources", []):
        sid = s.get("id")
        name = s.get("name", "?")
        exist = existing["sources"].get(sid)
        if exist:
            if mode == "overwrite":
                diff["sources"].append({"action": "update", "id": sid, "name": name})
            else:
                diff["sources"].append({"action": "skip", "id": sid, "name": name,
                                        "reason": i18n._("import.already_exists")})
        else:
            diff["sources"].append({"action": "insert", "id": sid, "name": name})

    for c in data.get("channels", []):
        cid = c.get("id")
        name = c.get("name", "?")
        exist = existing["channels"].get(cid)
        if exist:
            if mode == "overwrite":
                diff["channels"].append({"action": "update", "id": cid, "name": name})
            else:
                diff["channels"].append({"action": "skip", "id": cid, "name": name,
                                         "reason": i18n._("import.already_exists")})
        else:
            diff["channels"].append({"action": "insert", "id": cid, "name": name})

    for t in data.get("templates", []):
        tid = t.get("id")
        name = t.get("name", "?")
        exist = existing["templates"].get(tid)
        if exist:
            if mode == "overwrite":
                diff["templates"].append({"action": "update", "id": tid, "name": name})
            else:
                diff["templates"].append({"action": "skip", "id": tid, "name": name,
                                          "reason": i18n._("import.already_exists")})
        else:
            diff["templates"].append({"action": "insert", "id": tid, "name": name})

    for sc in data.get("source_channels", []):
        sid = sc.get("source_id")
        cid = sc.get("channel_id")
        tid = sc.get("template_id")
        sc_id = sc.get("id")
        missing_deps = []
        if sid not in existing["sources"]:
            missing_deps.append(f"source_id={sid}")
        if cid not in existing["channels"]:
            missing_deps.append(f"channel_id={cid}")
        if tid not in existing["templates"]:
            missing_deps.append(f"template_id={tid}")
        if missing_deps:
            diff["source_channels"].append({
                "action": "warn", "id": sc_id,
                "deps": missing_deps,
                "auto_resolve": i18n._("import.mark_invalid") if mode == "insert" else i18n._("import.auto_import_missing")
            })
            deps["missing"].append({"id": sc_id, "deps": missing_deps})
        else:
            diff["source_channels"].append({"action": "insert", "id": sc_id})

    for k in data.get("system_config", {}):
        diff["system_config"].append({"action": "upsert", "key": k})

    return {"diff": diff, "deps": deps, "errors": errors}


def _import_execute(data, mode="insert"):
    summary = {
        "parsers": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "sources": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "channels": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "templates": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "source_channels": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "system_config": {"updated": 0, "errors": 0},
        "parser_files": {"written": 0, "errors": 0},
    }
    errors = []
    existing_ids = {}

    existing_ids["parsers"] = {p["filename"]: p for p in db.get_parsers()}
    for p in data.get("parsers", []):
        fn = p.get("filename", "")
        exist = existing_ids["parsers"].get(fn)
        try:
            if exist and mode == "insert":
                summary["parsers"]["skipped"] += 1
                continue
            if p.get("code"):
                fpath = os.path.join(PARSERS_DIR, fn)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(p["code"])
                summary["parser_files"]["written"] += 1
            if exist:
                db.update_parser(exist["id"], name=p["name"], description=p.get("description", ""))
                summary["parsers"]["updated"] += 1
            else:
                pid = db.create_parser(p["name"], fn, p.get("description", ""))
                if pid:
                    if p.get("code"):
                        try:
                            parser_loader.load_parser(fn)
                        except Exception:
                            pass
                    summary["parsers"]["inserted"] += 1
                else:
                    summary["parsers"]["errors"] += 1
                    errors.append(i18n._("import.parser_exists").replace("{name}", fn))
        except Exception as e:
            summary["parsers"]["errors"] += 1
            errors.append(i18n._("import.parser_error").replace("{name}", fn).replace("{error}", str(e)[:200]))

    existing_ids["sources"] = {s["port"]: s for s in db.get_sources()}
    for s in data.get("sources", []):
        port = s.get("port")
        exist = existing_ids["sources"].get(port)
        try:
            if exist and mode == "insert":
                summary["sources"]["skipped"] += 1
                continue
            if exist:
                db.update_source(exist["id"], name=s["name"], parser_id=s.get("parser_id"),
                                 enabled=s.get("enabled", 1))
                summary["sources"]["updated"] += 1
            else:
                db.create_source(s["name"], port, s.get("parser_id"), s.get("enabled", 1))
                summary["sources"]["inserted"] += 1
        except Exception as e:
            summary["sources"]["errors"] += 1
            errors.append(i18n._("import.source_error").replace("{name}", s.get("name", "?")).replace("{error}", str(e)[:200]))

    existing_ids["channels"] = {c["name"]: c for c in db.get_channels()}
    for c in data.get("channels", []):
        name = c.get("name", "")
        exist = existing_ids["channels"].get(name)
        try:
            if exist and mode == "insert":
                summary["channels"]["skipped"] += 1
                continue
            if exist:
                db.update_channel(exist["id"], name=name, type=c.get("type"),
                                  config=c.get("config"), enabled=c.get("enabled", 1))
                summary["channels"]["updated"] += 1
            else:
                db.create_channel(name, c["type"], c.get("config", "{}"), c.get("enabled", 1))
                summary["channels"]["inserted"] += 1
        except Exception as e:
            summary["channels"]["errors"] += 1
            errors.append(i18n._("import.channel_error").replace("{name}", name).replace("{error}", str(e)[:200]))

    existing_ids["templates"] = {t["name"]: t for t in db.get_templates()}
    for t in data.get("templates", []):
        name = t.get("name", "")
        exist = existing_ids["templates"].get(name)
        try:
            if exist and mode == "insert":
                summary["templates"]["skipped"] += 1
                continue
            if exist:
                db.update_template(exist["id"], name=name, engine=t.get("engine", "jinja2"),
                                   title_tpl=t.get("title_tpl", ""), content_tpl=t.get("content_tpl", ""))
                summary["templates"]["updated"] += 1
            else:
                db.create_template(name, t.get("engine", "jinja2"),
                                   t.get("title_tpl", ""), t.get("content_tpl", ""))
                summary["templates"]["inserted"] += 1
        except Exception as e:
            summary["templates"]["errors"] += 1
            errors.append(i18n._("import.template_error").replace("{name}", name).replace("{error}", str(e)[:200]))

    for sc in data.get("source_channels", []):
        try:
            if mode == "insert":
                db.create_source_channel(
                    sc["source_id"], sc["channel_id"], sc["template_id"],
                    sc.get("condition_expr", ""), sc.get("priority", 0),
                    sc.get("enabled", 1), sc.get("urgent", 0),
                    sc.get("dedup_key_expr", ""), sc.get("dedup_window", 3600))
                summary["source_channels"]["inserted"] += 1
            else:
                existing_scs = db.get_source_channels(sc["source_id"])
                match = [x for x in existing_scs if x["channel_id"] == sc["channel_id"]]
                if match:
                    db.update_source_channel(match[0]["id"],
                        template_id=sc["template_id"],
                        condition_expr=sc.get("condition_expr", ""),
                        priority=sc.get("priority", 0),
                        enabled=sc.get("enabled", 1),
                        urgent=sc.get("urgent", 0),
                        dedup_key_expr=sc.get("dedup_key_expr", ""),
                        dedup_window=sc.get("dedup_window", 3600))
                    summary["source_channels"]["updated"] += 1
                else:
                    db.create_source_channel(
                        sc["source_id"], sc["channel_id"], sc["template_id"],
                        sc.get("condition_expr", ""), sc.get("priority", 0),
                        sc.get("enabled", 1), sc.get("urgent", 0),
                        sc.get("dedup_key_expr", ""), sc.get("dedup_window", 3600))
                    summary["source_channels"]["inserted"] += 1
        except Exception as e:
            summary["source_channels"]["errors"] += 1
            errors.append(i18n._("import.source_channel_error").replace("{name}", sc.get("id", "?")).replace("{error}", str(e)[:200]))

    for k, v in data.get("system_config", {}).items():
        try:
            db.set_config(k, v)
            summary["system_config"]["updated"] += 1
        except Exception as e:
            summary["system_config"]["errors"] += 1
            errors.append(i18n._("import.config_error").replace("{name}", k).replace("{error}", str(e)[:200]))

    return {"status": "ok" if not errors else "partial", "summary": summary, "errors": errors}


@backup_bp.route("/api/import", methods=["POST"])
def api_import():
    data = request.json
    if not data:
        return jsonify({"error": i18n._("err.invalid_json")}), 400

    mode = request.args.get("mode", "insert")
    dry_run = request.args.get("dry_run", "")

    if dry_run in ("1", "true", "yes"):
        result = _import_preview(data, mode)
        result["dry_run"] = True
        result["mode"] = mode
        return jsonify(result)

    preview = _import_preview(data, mode)
    result = _import_execute(data, mode)
    result["preview"] = preview
    return jsonify(result)
