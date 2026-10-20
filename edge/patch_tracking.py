#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把官网 docs/tracking.html 的假数据查询逻辑替换为真实 API 查询。"""
import io
import re
import sys

import os
PATH = os.environ.get("TARGET_HTML", "/tmp/ev-site-probe/docs/tracking.html")

with io.open(PATH, encoding="utf-8") as f:
    lines = f.readlines()

# 定位 <script> 开标签（第 492 行，索引 491）到 </script>（第 641 行，索引 640）
start = None
end = None
for i, ln in enumerate(lines):
    stripped = ln.strip().rstrip('\r')
    if i > 400 and stripped == "<script>" and start is None:
        start = i
    if start is not None and "</script>" in ln:
        end = i
        break

if start is None or end is None:
    sys.exit("未定位到 script 块: start=%s end=%s" % (start, end))

print("替换区间: 行 %d .. %d" % (start + 1, end + 1))

NEW_JS = r'''    <script>
    // ==================== 货物追踪 · 真实数据查询 ====================
    // 数据来源：嘉旅自建边缘代理（解决 TrackingMCP 的 CORS 限制）
    // 代理不可用时自动降级到船公司官网跟踪页，保证客户始终有下一步。

    // 代理端点候选：按顺序尝试，第一个成功即用。隧道地址变更时可在此增补。
    const TRACK_ENDPOINTS = [
        'https://firewire-vbulletin-twin-answers.trycloudflare.com',
        'https://sunrise-allows-countries-zope.trycloudflare.com'
    ];

    // 箱号前缀 -> 船公司官网跟踪页模板（代理不可用时的本地兜底）
    const CARRIER_DEEP_LINKS = {
        MSKU: 'https://www.maersk.com/tracking/{ref}', MRKU: 'https://www.maersk.com/tracking/{ref}',
        MSNU: 'https://www.msc.com/en/track-a-shipment?agencyPath=mwi&trackingNumber={ref}',
        MSCU: 'https://www.msc.com/en/track-a-shipment?agencyPath=mwi&trackingNumber={ref}',
        CMAU: 'https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=BL&Reference={ref}',
        CMDU: 'https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=BL&Reference={ref}',
        CSNU: 'https://elines.cma-cgm.com/en/online-business/edi/edi-shipment-tracking?BLReference={ref}',
        COSU: 'https://elines.cma-cgm.com/en/online-business/edi/edi-shipment-tracking?BLReference={ref}',
        HLXU: 'https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution?searchBy=BL&reference={ref}',
        HLCU: 'https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution?searchBy=BL&reference={ref}',
        ZIMU: 'https://www.zim.com/tools/track-a-shipment?consnumber={ref}',
        ONEU: 'https://www.one-line.com/en/track-and-trace?reference={ref}',
        EGHU: 'https://www.evgu.com/en/track-trace?blno={ref}',
        OOLU: 'https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/cargotracking.aspx?ctSearchType=BL&ctShipmentNumber={ref}',
        YMLU: 'https://www.yangming.com/e-service/Track_Trace/ctm_tracking.aspx?BLADINGNO={ref}',
        TGHU: 'https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/cargotracking.aspx?ctSearchType=BL&ctShipmentNumber={ref}'
    };

    const CARRIER_NAMES = {
        MAEU: 'Maersk 马士基', MSCU: 'MSC 地中海航运', CMDU: 'CMA CGM 达飞',
        COSU: 'COSCO 中远海运', HLCU: 'Hapag-Lloyd 赫伯罗特', ZIMU: 'ZIM 以星',
        ONEY: 'ONE 海洋网联', EGLV: 'Evergreen 长荣', OOLU: 'OOCL 东方海外',
        YMLU: 'Yang Ming 阳明'
    };

    // 状态 -> 官网现有样式类
    const STATUS_CLASS = {
        in_transit: 'status-transit', transit: 'status-transit', sailed: 'status-transit',
        departed: 'status-transit', loaded: 'status-transit', gate_in: 'status-transit',
        booked: 'status-pending', empty: 'status-pending', pending: 'status-pending',
        arrived: 'status-delivered', discharged: 'status-delivered', delivered: 'status-delivered',
        returned_empty: 'status-delivered'
    };

    // 判断当前界面语言，中文显示中文状态名
    function isZh() {
        return (typeof EVI18n !== 'undefined' && EVI18n.currentLang !== 'en');
    }

    // 状态中文名映射（代理返回 status_label 已是中文，英文时这里转英文）
    const STATUS_EN = {
        booked: 'Booked', empty: 'Empty', loaded: 'Loaded', in_transit: 'In Transit',
        transit: 'In Transit', sailed: 'Sailed', departed: 'Departed', arrived: 'Arrived',
        discharged: 'Discharged', delivered: 'Delivered', returned_empty: 'Container Returned',
        gate_in: 'Gate In', gate_out: 'Gate Out', customs_hold: 'Customs Hold',
        exception: 'Exception', unknown: 'Unknown'
    };

    function localDeepLink(ref) {
        const p = (ref || '').toUpperCase().slice(0, 4);
        if (CARRIER_DEEP_LINKS[p]) return CARRIER_DEEP_LINKS[p].replace('{ref}', ref);
        return 'https://www.google.com/search?q=' + encodeURIComponent(ref + ' tracking');
    }

    function carrierLabel(code) {
        return CARRIER_NAMES[code] || code || '';
    }

    // 依次尝试各代理端点
    async function queryViaProxy(ref) {
        for (const base of TRACK_ENDPOINTS) {
            try {
                const ctrl = new AbortController();
                const timer = setTimeout(() => ctrl.abort(), 12000);
                const resp = await fetch(base + '/api/track?ref=' + encodeURIComponent(ref), {
                    signal: ctrl.signal
                });
                clearTimeout(timer);
                if (!resp.ok) continue;
                const data = await resp.json();
                if (data && (data.ok || data.deep_link)) return data;
            } catch (e) {
                // 该端点不可用，尝试下一个
            }
        }
        return null;
    }

    function fillSample(e, waybill) {
        e.preventDefault();
        document.getElementById('trackingInput').value = waybill;
        trackCargo();
    }

    function showNotFound(ref, deepLink) {
        const notFound = document.getElementById('notFound');
        const fb = document.getElementById('fallbackActions');
        const link = document.getElementById('carrierLink');
        if (deepLink) {
            link.href = deepLink;
            fb.style.display = 'block';
        } else {
            fb.style.display = 'none';
        }
        notFound.classList.add('show');
        notFound.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    async function trackCargo() {
        const input = document.getElementById('trackingInput');
        const waybill = input.value.trim().toUpperCase();
        const loading = document.getElementById('loading');
        const result = document.getElementById('trackingResult');
        const notFound = document.getElementById('notFound');
        const fb = document.getElementById('fallbackActions');

        if (!waybill) {
            input.focus();
            input.style.borderColor = '#ff4d4f';
            setTimeout(() => input.style.borderColor = '', 2000);
            return;
        }

        result.classList.remove('show');
        notFound.classList.remove('show');
        fb.style.display = 'none';
        loading.classList.add('show');

        const data = await queryViaProxy(waybill);
        loading.classList.remove('show');

        if (data && data.ok) {
            showResult(data, waybill);
        } else {
            // 代理不可用或无记录：仍给出船司官网深链，让客户可自助查询
            const link = (data && data.deep_link) || localDeepLink(waybill);
            showNotFound(waybill, link);
        }
    }

    function showResult(data, waybill) {
        const result = document.getElementById('trackingResult');
        const resultTitle = document.getElementById('resultTitle');
        const resultWaybill = document.getElementById('resultWaybill');
        const resultStatus = document.getElementById('resultStatus');
        const resultInfo = document.getElementById('resultInfo');
        const resultTimeline = document.getElementById('resultTimeline');

        const carrier = carrierLabel(data.carrier_code) || data.carrier_name || '';
        resultTitle.textContent = carrier || (isZh() ? '货物运输状态' : 'Shipment Status');
        resultWaybill.textContent = data.identifier || waybill;

        // 状态标签
        const statusKey = (data.status || 'unknown').toLowerCase();
        const statusText = isZh()
            ? (data.status_label || statusKey)
            : (STATUS_EN[statusKey] || data.status_label || statusKey);
        const cls = STATUS_CLASS[statusKey] || 'status-pending';
        resultStatus.innerHTML = '<span class="result-status ' + cls + '">' + escapeHtml(statusText) + '</span>';

        // 信息网格
        const info = [];
        info.push(['运单号 / Reference', data.identifier || waybill]);
        if (data.carrier_name || data.carrier_code) info.push(['船公司 / Carrier', carrier || data.carrier_name]);
        if (data.origin) info.push(['起运地 / Origin', data.origin]);
        if (data.destination) info.push(['目的地 / Destination', data.destination]);
        if (data.eta) {
            const etaTxt = formatEta(data.eta);
            info.push(['预计到达 / ETA', etaTxt + (data.eta_is_estimated ? ' (预计)' : '')]);
        }
        if (data.vessel_name) info.push(['船名航次 / Vessel', data.vessel_name]);
        if (data.identifier_type) info.push(['单号类型 / Type', data.identifier_type]);
        info.push(['数据更新 / Updated', data.updated_at || '-']);

        resultInfo.innerHTML = info.map(function (row) {
            return '<div class="info-item"><div class="label">' + escapeHtml(row[0]) +
                   '</div><div class="value">' + escapeHtml(row[1]) + '</div></div>';
        }).join('');

        // 轨迹时间线
        const events = data.events || [];
        if (events.length) {
            resultTimeline.innerHTML = events.map(function (ev) {
                const isLast = false;
                const dotClass = ev.actual ? 'completed' : 'upcoming';
                const time = formatEta(ev.event_time);
                const desc = (ev.description || ev.event_code || '') +
                    (ev.transport_mode ? ' · ' + ev.transport_mode : '');
                return '<div class="timeline-item ' + dotClass + '"><div class="timeline-dot"></div>' +
                       '<div class="timeline-content"><h5>' + escapeHtml(desc) + '</h5>' +
                       '<p>' + escapeHtml(ev.location || '') + '</p>' +
                       '<div class="time">' + escapeHtml(time) + '</div></div></div>';
            }).join('');
        } else {
            // 免费查询未返回逐条轨迹时，引导至船司官网查看完整轨迹
            const dl = data.deep_link || localDeepLink(waybill);
            resultTimeline.innerHTML =
                '<div class="timeline-item upcoming"><div class="timeline-dot"></div>' +
                '<div class="timeline-content"><h5>' + (isZh() ? '查看完整运输轨迹' : 'View full tracking history') + '</h5>' +
                '<p>' + (isZh() ? '在船公司官网可查看逐条节点记录' : 'Open the carrier site for milestone-by-milestone status') + '</p>' +
                '<div class="time"><a href="' + escapeHtml(dl) + '" target="_blank" rel="noopener" style="color:var(--accent);">' +
                escapeHtml(dl) + '</a></div></div></div>';
        }

        result.classList.add('show');
        result.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function escapeHtml(s) {
        return String(s === undefined || s === null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function formatEta(v) {
        if (!v) return '-';
        var d = new Date(v);
        if (isNaN(d.getTime())) return String(v);
        var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
               ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }
    </script>
'''

new_lines = lines[:start] + [NEW_JS] + lines[end + 1:]
with io.open(PATH, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("已写入。文件行数: %d -> %d" % (len(lines), len(new_lines)))
