#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
嘉旅供应链 · 货物跟踪 (Cargo Tracker)
本地自托管货物跟踪工具。零外部依赖，仅用 Python 标准库。
- HTTP 服务 + SQLite 本地库（数据持久化，可长期使用）
- 通过 TrackingMCP 公开端点的真实数据（229 家船公司，无需 key）
- 可选填入免费 API key 解锁组合跟踪 / 滞箱预警
- 内置主流船公司官网跟踪深链

运行: python3 app.py [端口]   默认端口 8765
访问: http://127.0.0.1:8765
"""

import json
import os
import re
import sqlite3
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cargo.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TRACKINGMCP_BASE = "https://api.trackingmcp.com"
DEFAULT_PORT = 8765

# ---------------------------------------------------------------------------
# 船公司深链模板（known=可直跳官网；None=回退到 Google 搜索该箱号）
# SCAC 代码同时用于「按箱号前缀自动识别船公司」
# ---------------------------------------------------------------------------
CARRIERS = {
    "MAEU": {"name": "Maersk",            "url": "https://www.maersk.com/tracking/{ref}"},
    "MSCU": {"name": "MSC",               "url": "https://www.msc.com/track-a-shipment?ContainerNumber={ref}"},
    "CMDU": {"name": "CMA CGM",           "url": "https://www.cma-cgm.com/ebusiness/tracking?TrackingNumber={ref}&TrackingType=Container"},
    "COSU": {"name": "COSCO",             "url": "https://elines.coscoshipping.com/ebusiness/tracking?number={ref}"},
    "HLCU": {"name": "Hapag-Lloyd",       "url": "https://www.hapag-lloyd.com/en/online-business/track/track.html?blno={ref}"},
    "ZIMU": {"name": "ZIM",               "url": "https://www.zim.com/tools/track?search={ref}"},
    "EGLV": {"name": "Evergreen",         "url": None},
    "ONEY": {"name": "ONE",               "url": None},
    "OOLU": {"name": "OOCL",              "url": None},
    "YMLU": {"name": "Yang Ming",         "url": None},
    "HDMU": {"name": "HMM",               "url": None},
    "WHLC": {"name": "Wan Hai",           "url": None},
    "PCIU": {"name": "PIL",               "url": None},
    "SUDU": {"name": "Hamburg Sud",       "url": "https://www.maersk.com/tracking/{ref}"},
}

# 集装箱号前 4 位（船东代码）-> SCAC 别名，覆盖常见实际前缀
PREFIX_MAP = {
    "MSKU": "MAEU", "MAEU": "MAEU", "MRKU": "MAEU", "MRSU": "MAEU", "MMCU": "MAEU",
    "MSCU": "MSCU", "MEDU": "MSCU",
    "CMDU": "CMDU", "CMAU": "CMDU", "CGMU": "CMDU",
    "COSU": "COSU", "CCLU": "COSU", "COAU": "COSU",
    "HLCU": "HLCU", "HLCX": "HLCU", "HLXU": "HLCU",
    "ONEY": "ONEY", "ONEU": "ONEY", "ONCU": "ONEY",
    "EGLV": "EGLV", "EGHU": "EGLV", "EMHU": "EGLV", "EISU": "EGLV",
    "ZIMU": "ZIMU",
    "OOLU": "OOLU", "OOCU": "OOLU",
    "YMLU": "YMLU", "YMSU": "YMLU", "YMBU": "YMLU",
    "HDMU": "HDMU", "HMMU": "HDMU",
    "WHLC": "WHLC", "WHSU": "WHLC",
    "PCIU": "PCIU", "PCNU": "PCIU",
    "SUDU": "SUDU",
}

# 状态 -> (中文标签, 颜色类)
STATUS_MAP = {
    "in_transit":   ("在途",   "blue"),
    "planned":      ("已订舱/计划中", "gray"),
    "discharged":   ("已卸船", "teal"),
    "available":    ("可提货", "teal"),
    "delivered":    ("已交付", "green"),
    "returned_empty": ("已查到", "green"),
    "unknown":      ("未知",   "gray"),
}
EXCEPTION_CODES = {"HOLD", "DELAY", "ROLLED", "EXCEPTION", "DEMURRAGE", "DETENTION"}


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = db_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_no TEXT NOT NULL,
            kind TEXT,
            carrier_code TEXT,
            carrier_name TEXT,
            customer TEXT,
            origin TEXT,
            destination TEXT,
            notes TEXT,
            api_identifier TEXT,
            last_status TEXT,
            last_eta TEXT,
            last_vessel TEXT,
            last_updated TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_id INTEGER,
            event_time TEXT,
            location TEXT,
            description TEXT,
            event_code TEXT,
            actual INTEGER,
            FOREIGN KEY(shipment_id) REFERENCES shipments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()


def get_config():
    conn = db_conn()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_config(data: dict):
    conn = db_conn()
    for k, v in data.items():
        conn.execute("INSERT INTO config(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()
    conn.close()


def detect_carrier(tracking_no: str):
    """通过箱号前 4 位（船东代码）识别船公司。"""
    prefix = (tracking_no or "").strip().upper()[:4]
    scac = PREFIX_MAP.get(prefix) or (prefix if prefix in CARRIERS else None)
    if scac and scac in CARRIERS:
        return scac, CARRIERS[scac]["name"]
    return None, None


# ---------------------------------------------------------------------------
# TrackingMCP 调用
# ---------------------------------------------------------------------------
def _build_opener():
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or
             os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = _build_opener()
    return _OPENER


def track_reference(reference, ref_type=None, carrier_code=None, api_key=None):
    url = TRACKINGMCP_BASE + "/v1/track/public"
    body = {"reference": reference}
    if ref_type:
        body["reference_type"] = ref_type
    if carrier_code:
        body["carrier_code"] = carrier_code
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "CargoTracker/1.0"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _opener().open(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": {"message": f"HTTP {e.code}"}}
    except Exception as e:
        return {"ok": False, "error": {"message": str(e)}}


def _is_quota_blocked(payload):
    """判断返回是否因免费额度/试用用尽而需换类型重试。"""
    if not payload:
        return False
    if payload.get("code") == "DEMO_ONLY":
        return True
    reason = (payload.get("reason") or "")
    msg = (payload.get("message") or "")
    if "visitor_used" in reason or "free live lookup" in msg:
        return True
    return False


# 依次尝试的类型（preferred 排在最前）。container_id 能自动识别实际类型，
# 且在免费额度用尽时仍能返回路线/ETA 预览，因此作为兜底首选。
_TYPE_ORDER = ("container_id", "bill_of_lading", "booking_id")


def track_reference_multi(reference, preferred_kind=None, carrier_code=None, api_key=None):
    types = []
    if preferred_kind and preferred_kind not in types:
        types.append(preferred_kind)
    for t in _TYPE_ORDER:
        if t not in types:
            types.append(t)
    last = None
    for t in types:
        payload = track_reference(reference, t, carrier_code, api_key)
        if _is_quota_blocked(payload):
            last = payload
            continue  # 换类型重试
        if payload and payload.get("ok"):
            payload = dict(payload)
            payload["_used_type"] = t
            return payload
        last = payload
    return last or {"ok": False, "error": {"message": "all reference types failed"}}


def normalize(payload, reference):
    """把 TrackingMCP 返回整理成统一结构。"""
    if not payload or not payload.get("ok"):
        msg = ""
        if payload:
            msg = payload.get("error", {}).get("message") or payload.get("message", "")
        return {"ok": False, "error": msg or "无返回", "reference": reference}

    d = payload.get("data", {}) or {}
    events_raw = d.get("events", []) or []
    events = []
    for e in events_raw:
        events.append({
            "event_time": e.get("datetime"),
            "location": e.get("location") or e.get("unlocode"),
            "description": e.get("description"),
            "event_code": (e.get("event_code") or "").upper(),
            "actual": e.get("actual"),
        })

    identifier = d.get("identifier") or reference
    carrier_code = d.get("carrier_code")
    carrier_name = d.get("carrier_name")
    status = (d.get("status") or "unknown").lower()
    vessel = (d.get("vessel") or {}).get("name")

    deep_link = None
    if carrier_code and carrier_code in CARRIERS and CARRIERS[carrier_code]["url"]:
        deep_link = CARRIERS[carrier_code]["url"].format(ref=identifier)
    search_link = f"https://www.google.com/search?q={carrier_name or ''} {identifier} tracking".strip()

    # 异常检测
    is_exception = False
    for ev in events:
        if ev["event_code"] in EXCEPTION_CODES:
            is_exception = True
            break

    return {
        "ok": True,
        "identifier": identifier,
        "identifier_type": d.get("identifier_type"),
        "carrier_code": carrier_code,
        "carrier_name": carrier_name,
        "status": status,
        "status_label": STATUS_MAP.get(status, ("未知", "gray"))[0],
        "eta": d.get("eta"),
        "eta_is_estimated": d.get("eta_is_estimated"),
        "origin": d.get("origin"),
        "destination": d.get("destination"),
        "vessel_name": vessel,
        "is_exception": is_exception,
        "events": events,
        "deep_link": deep_link,
        "search_link": search_link,
        "used_type": payload.get("_used_type"),
        "raw": d,
    }


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 安静日志

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _shipment_dict(self, row, with_events=False):
        s = dict(row)
        if with_events:
            conn = db_conn()
            evs = conn.execute(
                "SELECT event_time, location, description, event_code, actual "
                "FROM events WHERE shipment_id=? ORDER BY event_time", (s["id"],)
            ).fetchall()
            conn.close()
            s["events"] = [dict(e) for e in evs]
        return s

    # ---------------- GET ----------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", ""):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/shipments":
            return self._api_list_shipments()
        if path == "/api/config":
            return self._send_json(get_config())
        if path == "/api/carriers":
            return self._send_json(CARRIERS)
        m = re.match(r"^/api/shipments/(\d+)$", path)
        if m:
            return self._api_shipment_detail(int(m.group(1)))
        return self._send_json({"error": "not found"}, 404)

    # ---------------- POST ----------------
    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/shipments":
            return self._api_create_shipment()
        if path == "/api/track":
            return self._api_quick_track()
        if path == "/api/config":
            return self._api_update_config()
        m = re.match(r"^/api/shipments/(\d+)/track$", path)
        if m:
            return self._api_track_shipment(int(m.group(1)))
        return self._send_json({"error": "not found"}, 404)

    # ---------------- DELETE ----------------
    def do_DELETE(self):
        path = self.path.split("?")[0]
        m = re.match(r"^/api/shipments/(\d+)$", path)
        if m:
            return self._api_delete_shipment(int(m.group(1)))
        return self._send_json({"error": "not found"}, 404)

    # ---------------- static ----------------
    def _serve_static(self, name):
        fname = os.path.basename(name)
        fpath = os.path.join(STATIC_DIR, fname)
        if not os.path.isfile(fpath):
            return self._send_json({"error": "not found"}, 404)
        ext = os.path.splitext(fpath)[1]
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(fpath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- API impl ----------------
    def _api_list_shipments(self):
        conn = db_conn()
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM events e WHERE e.shipment_id=s.id) AS event_count "
            "FROM shipments s ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        out = [self._shipment_dict(r) for r in rows]
        self._send_json(out)

    def _api_shipment_detail(self, sid):
        conn = db_conn()
        row = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        conn.close()
        if not row:
            return self._send_json({"error": "not found"}, 404)
        self._send_json(self._shipment_dict(row, with_events=True))

    def _api_create_shipment(self):
        b = self._read_body()
        tracking_no = (b.get("tracking_no") or "").strip()
        if not tracking_no:
            return self._send_json({"error": "tracking_no required"}, 400)
        kind = b.get("kind") or "container"
        carrier_code = b.get("carrier_code") or None
        carrier_name = b.get("carrier_name") or None
        if not carrier_code:
            carrier_code, carrier_name = detect_carrier(tracking_no)
        conn = db_conn()
        cur = conn.execute(
            "INSERT INTO shipments(tracking_no, kind, carrier_code, carrier_name, "
            "customer, origin, destination, notes) VALUES(?,?,?,?,?,?,?,?)",
            (tracking_no, kind, carrier_code, carrier_name,
             b.get("customer"), b.get("origin"), b.get("destination"), b.get("notes")),
        )
        sid = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        conn.close()
        self._send_json(self._shipment_dict(row), 201)

    def _api_delete_shipment(self, sid):
        conn = db_conn()
        conn.execute("DELETE FROM shipments WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        self._send_json({"ok": True})

    def _api_quick_track(self):
        b = self._read_body()
        reference = (b.get("reference") or "").strip()
        if not reference:
            return self._send_json({"error": "reference required"}, 400)
        cfg = get_config()
        api_key = cfg.get("api_key")
        payload = track_reference_multi(reference, b.get("kind"), b.get("carrier_code"), api_key)
        self._send_json(normalize(payload, reference))

    def _api_track_shipment(self, sid):
        conn = db_conn()
        row = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        conn.close()
        if not row:
            return self._send_json({"error": "not found"}, 404)
        s = dict(row)
        cfg = get_config()
        api_key = cfg.get("api_key")
        payload = track_reference_multi(s["tracking_no"], s["kind"], s["carrier_code"], api_key)
        norm = normalize(payload, s["tracking_no"])
        if norm.get("ok"):
            conn = db_conn()
            conn.execute("DELETE FROM events WHERE shipment_id=?", (sid,))
            for ev in norm["events"]:
                conn.execute(
                    "INSERT INTO events(shipment_id, event_time, location, description, event_code, actual) "
                    "VALUES(?,?,?,?,?,?)",
                    (sid, ev.get("event_time"), ev.get("location"),
                     ev.get("description"), ev.get("event_code"),
                     1 if ev.get("actual") else 0),
                )
            used = norm.get("used_type")
            conn.execute(
                "UPDATE shipments SET api_identifier=?, last_status=?, last_eta=?, "
                "last_vessel=?, carrier_code=COALESCE(?, carrier_code), "
                "carrier_name=COALESCE(?, carrier_name), "
                "kind=COALESCE(?, kind), "
                "last_updated=? WHERE id=?",
                (norm.get("identifier"), norm.get("status"), norm.get("eta"),
                 norm.get("vessel_name"), norm.get("carrier_code"), norm.get("carrier_name"),
                 used,  # 把命中类型写回，下次直接用正确类型查询
                 datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), sid),
            )
            conn.commit()
            conn.close()
        self._send_json(norm)

    def _api_update_config(self):
        b = self._read_body()
        set_config(b)
        self._send_json({"ok": True, "config": get_config()})


def main():
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 56)
    print("嘉旅供应链 · 货物跟踪 已启动")
    print(f"  本地访问: {url}")
    print(f"  数据库:   {DB_PATH}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.shutdown()


if __name__ == "__main__":
    main()
