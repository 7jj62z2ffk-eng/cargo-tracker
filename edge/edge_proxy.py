#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
嘉旅官网 · 货物跟踪公网代理 (Edge Proxy)
=========================================
解决 TrackingMCP 的 CORS 限制：其 API 只放行 https://trackingmcp.com，
GitHub Pages 上的官网（浏览器端）无法直连，必须经过本代理转发。

设计要点
--------
1. CORS 严格白名单：仅放行 excellent-voyage.cn 及其子域，其他一律拒绝。
2. SQLite 缓存：TrackingMCP 免费额度极紧（每日 1 次实时查询/访客），
   相同单号在 TTL 内直接读缓存，既快又不烧额度。
3. 多类型自动重试：container_id -> bill_of_lading -> booking_id 依次尝试，
   命中即返回，并把实际命中的类型记录下来。
4. 限流：按客户端 IP 限流，防止单号被恶意刷量。
5. 深链兜底：即使代理查不到，也返回船司官网跟踪页链接，让用户能自助查全量轨迹。
6. 零依赖：仅用 Python 标准库，可直接扔到 NAS 上跑。

接口
----
GET  /api/track?ref=<单号>&type=<container_id|bill_of_lading|booking_id>
     -> { ok, carrier, status, eta, origin, destination, events[], deep_link, ... }
GET  /api/health
     -> { ok: true, service, cache_entries, uptime }

运行
----
python3 edge_proxy.py --port 8788
"""

import argparse
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------- 配置

TRACKINGMCP_URL = "https://api.trackingmcp.com/v1/track/public"

# 允许访问本代理的来源。留空表示不校验（同源部署时无需此项）。
ALLOWED_ORIGINS = [
    "https://www.excellent-voyage.cn",
    "https://excellent-voyage.cn",
    "http://www.excellent-voyage.cn",
    "http://excellent-voyage.cn",
    "http://localhost:8765",          # 本地联调
    "http://127.0.0.1:8765",
]

# 缓存有效期（秒）。免费额度紧张，默认 6 小时。
CACHE_TTL = int(os.environ.get("TRACK_CACHE_TTL", 6 * 3600))

# 限流：单 IP 每 60 秒最多查询次数
RATE_LIMIT = int(os.environ.get("TRACK_RATE_LIMIT", 10))
RATE_WINDOW = 60

# 上游请求超时
UPSTREAM_TIMEOUT = 25

DB_PATH = os.environ.get("TRACK_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_cache.db"))

# 参考号类型尝试顺序
REF_TYPES = ["container_id", "bill_of_lading", "booking_id"]

# ---------------------------------------------------------------- 船司深链

# 船公司 SCAC/前缀 -> (官网跟踪页模板, 中文名)
# {ref} 会被替换成运单号
CARRIER_LINKS = {
    "MAEU": ("https://www.maersk.com/tracking/{ref}", "Maersk 马士基"),
    "MSCU": ("https://www.msc.com/en/track-a-shipment?agencyPath=mwi&trackingNumber={ref}", "MSC 地中海航运"),
    "CMDU": ("https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=BL&Reference={ref}", "CMA CGM 达飞"),
    "COSU": ("https://elines.cma-cgm.com/en/online-business/edi/edi-shipment-tracking?BLReference={ref}", "COSCO 中远海运"),
    "HLCU": ("https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution?searchBy=BL&reference={ref}", "Hapag-Lloyd 赫伯罗特"),
    "ZIMU": ("https://www.zim.com/tools/track-a-shipment?consnumber={ref}", "ZIM 以星"),
    "ONEY": ("https://www.one-line.com/en/track-and-trace?reference={ref}", "ONE 海洋网联"),
    "EGLV": ("https://www.evgu.com/en/track-trace?blno={ref}", "Evergreen 长荣"),
    "HDMU": ("https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution?searchBy=BL&reference={ref}", "Hapag-Lloyd 赫伯罗特"),
    "YMLU": ("https://www.yangming.com/e-service/Track_Trace/ctm_tracking.aspx?BLADINGNO={ref}", "Yang Ming 阳明"),
    "OOLU": ("https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/cargotracking.aspx?ctSearchType=BL&ctShipmentNumber={ref}", "OOCL 东方海外"),
    "HLCX": ("https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution?searchBy=BL&reference={ref}", "Hapag-Lloyd 赫伯罗特"),
    "ESPU": ("https://www.emirates.com/en/cargo/track/", "Emirates Shipping"),
    "GETU": ("https://www.gte.com.cn/track/trace?ctno={ref}", "GET 泛亚航运"),
    "RCLU": ("https://www.rcl-europe.com/track/pages/trackshipment.action?blNumber={ref}", "RCL 达登"),
}

# 箱号前缀 -> SCAC（用于自动识别船司）
PREFIX_TO_CARRIER = {
    "MSKU": "MAEU", "MSCU": "MSCU", "MSNU": "MSCU", "MRKU": "MAEU", "MRNU": "MAEU",
    "MRDU": "MAEU", "MSDU": "MAEU", "MSZU": "MAEU",
    "CMAU": "CMDU", "CMDU": "CMDU", "CMDU": "CMDU", "CMA": "CMDU",
    "CSNU": "COSU", "CSQU": "COSU", "COHU": "COSU", "COSU": "COSU", "CBHU": "COSU",
    "HLXU": "HLCU", "HLCU": "HLCU", "HLBU": "HLCU", "HLCX": "HLCX",
    "ZIMU": "ZIMU", "ZCSU": "ZIMU", "ZIM": "ZIMU",
    "ONEU": "ONEY", "ONE": "ONEY", "TGHU": "ONEY",
    "EGHU": "EGLV", "EGLV": "EGLV", "EGHU": "EGLV",
    "TEMU": "MSCU", "TGHU": "ONEY",
    "FCIU": "HLCU", "OOLU": "OOLU", "UACU": "OOLU", "YMLU": "YMLU",
    "GESU": "GETU", "HNSU": "GETU",
    "APZU": "APLU", "APLU": "APLU", "TEMU": "APLU",
    "TCNU": "TGHU", "TGHU": "TGHU",
}

# 状态 -> (中文标签, CSS 颜色类)
STATUS_MAP = {
    "booked": ("已订舱", "blue"),
    "empty": ("空箱", "gray"),
    "loaded": ("已装船", "blue"),
    "in_transit": ("运输中", "orange"),
    "transit": ("运输中", "orange"),
    "at_transshipment": ("中转中", "orange"),
    "transshipment": ("中转中", "orange"),
    "sailed": ("已开航", "blue"),
    "departed": ("已离港", "blue"),
    "arrived": ("已到港", "green"),
    "discharged": ("已卸船", "green"),
    "delivered": ("已送达", "green"),
    "returned_empty": ("已还空箱", "green"),
    "gate_in": ("进场", "blue"),
    "gate_out": ("出场", "blue"),
    "customs_hold": ("海关查验", "red"),
    "exception": ("异常", "red"),
    "unknown": ("状态未知", "gray"),
}

# ---------------------------------------------------------------- 工具

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def detect_carrier(ref):
    """按箱号前4位识别船公司 SCAC。"""
    p = (ref or "").strip().upper()[:4]
    if p in PREFIX_TO_CARRIER:
        return PREFIX_TO_CARRIER[p]
    if p in CARRIER_LINKS:
        return p
    return None


def build_deep_link(ref, carrier_code=None):
    """生成船司官网跟踪深链；识别不到则返回搜索引擎兜底链接。"""
    ref = (ref or "").strip()
    code = carrier_code or detect_carrier(ref)
    if code and code in CARRIER_LINKS:
        tpl, _name = CARRIER_LINKS[code]
        return tpl.replace("{ref}", ref)
    return "https://www.google.com/search?q=" + urllib.parse.quote(
        "%s tracking" % ref)


def carrier_name(code):
    if code and code in CARRIER_LINKS:
        return CARRIER_LINKS[code][1]
    return None


# ---------------------------------------------------------------- 缓存

_cache_lock = threading.Lock()
_conn = None


def db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " ref TEXT PRIMARY KEY, ref_type TEXT, payload TEXT,"
            " carrier_code TEXT, created_at REAL)"
        )
        _conn.commit()
    return _conn


def cache_get(ref):
    row = db().execute(
        "SELECT payload, created_at FROM cache WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    payload, created = row
    if time.time() - created > CACHE_TTL:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def cache_put(ref, ref_type, payload, carrier_code):
    db().execute(
        "INSERT OR REPLACE INTO cache(ref, ref_type, payload, carrier_code, created_at)"
        " VALUES(?,?,?,?,?)",
        (ref, ref_type, json.dumps(payload, ensure_ascii=False), carrier_code, time.time()))
    db().commit()


def cache_count():
    try:
        return db().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    except Exception:
        return 0


# ---------------------------------------------------------------- 限流

_rate = {}
_rate_lock = threading.Lock()


def rate_ok(ip):
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            _rate[ip] = hits
            return False
        hits.append(now)
        _rate[ip] = hits
        return True


# ---------------------------------------------------------------- 上游

def _opener():
    # 读取系统代理环境变量（NAS/本机无代理时自动直连）
    try:
        handler = urllib.request.ProxyHandler()
        return urllib.request.build_opener(handler)
    except Exception:
        return urllib.request.build_opener()


def call_upstream(ref, ref_type, api_key=None):
    """调用 TrackingMCP 公开端点。返回原始 JSON。"""
    body = {"reference": ref, "reference_type": ref_type}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "EV-Cargo-Tracker/1.0"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(TRACKINGMCP_URL, data=data, headers=headers, method="POST")
    try:
        with _opener().open(req, timeout=UPSTREAM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": {"message": "HTTP %s" % e.code}}
    except Exception as e:
        return {"ok": False, "error": {"message": str(e)}}


# ---------------------------------------------------------------- 归一化

def normalize(raw, ref):
    """把 TrackingMCP 原始响应整理成前端要的字段。"""
    if not raw or raw.get("ok") is False:
        reason = (raw or {}).get("reason") or (raw or {}).get("code")
        msg = (raw or {}).get("message") or (raw or {}).get("error", {}).get("message") \
            if isinstance((raw or {}).get("error"), dict) else (raw or {}).get("message")
        return {
            "ok": False,
            "error": msg or "查询失败",
            "reason": reason,
            "deep_link": build_deep_link(ref),
        }

    d = raw.get("data") or raw
    events_raw = raw.get("events") or d.get("events") or []

    events = []
    for ev in events_raw:
        if not isinstance(ev, dict):
            continue
        events.append({
            "event_time": ev.get("datetime") or ev.get("event_time"),
            "location": ev.get("location"),
            "unlocode": ev.get("unlocode"),
            "description": ev.get("description"),
            "event_code": ev.get("event_code"),
            "transport_mode": ev.get("transport_mode"),
            "actual": bool(ev.get("actual")),
        })
    events.sort(key=lambda x: (x.get("event_time") or ""))

    status = (d.get("status") or "unknown").lower()
    label, color = STATUS_MAP.get(status, ("状态未知", "gray"))

    ccode = d.get("carrier_code") or detect_carrier(ref)
    cname = d.get("carrier_name") or carrier_name(ccode)

    return {
        "ok": True,
        "identifier": d.get("identifier") or ref,
        "identifier_type": d.get("identifier_type"),
        "carrier_code": ccode,
        "carrier_name": cname,
        "status": status,
        "status_label": label,
        "status_color": color,
        "eta": d.get("eta"),
        "eta_is_estimated": d.get("eta_is_estimated"),
        "origin": d.get("origin"),
        "destination": d.get("destination"),
        "vessel_name": (d.get("vessel") or {}).get("name") if isinstance(d.get("vessel"), dict) else d.get("vessel_name"),
        "events": events,
        "deep_link": build_deep_link(ref, ccode),
        "search_link": build_deep_link(ref, ccode),
        "updated_at": now_iso(),
        "preview": bool(raw.get("preview")),
        "demo": bool(raw.get("demo")),
    }


# ---------------------------------------------------------------- 业务

def track(ref, ref_type=None, api_key=None):
    """查询单号。命中缓存直接返回；否则按多种类型重试。"""
    ref = (ref or "").strip().upper()
    if not ref:
        return {"ok": False, "error": "请输入单号"}

    cached = cache_get(ref)
    if cached:
        cached["cached"] = True
        return cached

    types = [ref_type] if ref_type else REF_TYPES
    last = None
    for t in types:
        raw = call_upstream(ref, t, api_key)
        norm = normalize(raw, ref)
        if norm.get("ok"):
            norm["used_type"] = t
            norm["cached"] = False
            cache_put(ref, t, norm, norm.get("carrier_code"))
            return norm
        last = norm
        # 命中"额度用尽"就不必再换类型重试了
        if norm.get("reason") == "visitor_used":
            break

    if last is None:
        last = {"ok": False, "error": "查询失败"}
    # 查不到也返回深链，用户可自行去船司官网查全量轨迹
    last["deep_link"] = build_deep_link(ref)
    return last


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "EVTrackEdge/1.0"

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # 非浏览器请求（curl/服务端调用）不限制
        return origin in ALLOWED_ORIGINS

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/api/health":
            self._json({"ok": True, "service": "ev-track-edge",
                         "cache_entries": cache_count(),
                         "time": now_iso()})
            return

        if path == "/api/track":
            if not self._origin_ok():
                self._json({"ok": False, "error": "origin not allowed"}, 403)
                return
            ip = self.client_address[0]
            if not rate_ok(ip):
                self._json({"ok": False, "error": "请求过于频繁，请稍后再试"}, 429)
                return
            ref = (q.get("ref") or q.get("reference") or q.get("no") or [""])[0]
            rtype = (q.get("type") or q.get("reference_type") or [None])[0]
            if rtype not in REF_TYPES:
                rtype = None
            api_key = os.environ.get("TRACKINGMCP_API_KEY") or None
            self._json(track(ref, rtype, api_key))
            return

        self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (now_iso(), fmt % args), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8788)))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    db()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("EV Track Edge Proxy listening on %s:%d (cache_ttl=%ds, db=%s)"
          % (args.host, args.port, CACHE_TTL, DB_PATH), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye", flush=True)


if __name__ == "__main__":
    main()
