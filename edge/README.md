# edge/ · 官网货物跟踪边缘代理

让 **GitHub Pages 上的官网**能查询真实货物轨迹。

## 为什么需要这一层

TrackingMCP 的 REST API 只允许 `Origin: https://trackingmcp.com` 跨域访问
（已实测：`access-control-allow-origin` 硬编码为该域），因此官网页面里的
浏览器 `fetch` 会被 CORS 直接拦死。GitHub Pages 只能托管静态文件，无法自建后端。

本目录即这层"后端"：它跑在 NAS 上，通过 Cloudflare Tunnel 暴露到公网，
对官网页面提供同源可访问的 JSON 接口。

## 组成

| 文件 | 作用 |
|------|------|
| `edge_proxy.py` | 代理主体：零依赖（Python 标准库）、CORS 白名单、SQLite 缓存、限流、多类型重试、船司深链兜底 |
| `deploy_nas.sh` | NAS 一键部署（装 cloudflared、写 systemd、启动） |
| `sync_endpoint.sh` | 隧道地址变化时自动把新地址同步到官网配置（GitHub API） |
| `nas_ssh.py` | paramiko 封装的 NAS 远程操作工具（密码走环境变量，不落盘） |
| `patch_tracking.py` | 把官网 tracking.html 的模拟数据段替换为真实查询逻辑 |
| `verify_tracking.js` | 在 Node 中模拟浏览器环境，端到端验证页面查询逻辑 |

## 关键设计

**1. CORS 严格白名单**
只放行 `https://www.excellent-voyage.cn`（及其 http/www 变体、localhost 联调）。
陌生 Origin 直接 403，避免代理被第三方滥用。

**2. SQLite 缓存（TTL 6 小时）**
TrackingMCP 免费额度极紧（公开端点每个访客每天仅 1 次实时查询，超额返回
`DEMO_ONLY / visitor_used`）。相同单号在 TTL 内直接读缓存，不消耗上游额度。

**3. 多类型自动重试**
依次尝试 `container_id` → `bill_of_lading` → `booking_id`，命中即返回。
避免"单号是提单却按箱号查"导致查不到（这是实际踩过的坑）。

**4. 限流**
单 IP 每 60 秒最多 10 次，防止单号被恶意刷量。

**5. 船司深链兜底**
即使代理超时或上游查不到，也会返回船司官网跟踪页链接
（Maersk / MSC / CMA CGM / COSCO / Hapag-Lloyd / ZIM / ONE / OOCL 等），
让客户始终有下一步，不会看到"未找到"就断了。

## 接口

```
GET /api/track?ref=<单号>[&type=container_id|bill_of_lading|booking_id]
GET /api/health
```

返回字段：`ok / carrier_name / status / status_label / eta / origin /
destination / vessel_name / events[] / deep_link / cached / updated_at`

## 部署到 NAS

```bash
export NAS_PASSWORD='<NAS 密码>'
cd edge
python3 nas_ssh.py upload edge_proxy.py /tmp/edge_proxy.py 644
python3 nas_ssh.py run "cp /tmp/edge_proxy.py /opt/ev-track/ && ..." sudo
```

或用 `deploy_nas.sh` 走完整流程。

NAS 上现役服务：
- `ev-track.service` — 代理（systemd，开机自启）
- `ev-track-tunnel.service` — Cloudflare 隧道（systemd，开机自启）
- `/etc/cron.d/ev-track-sync` — 每 10 分钟检查隧道地址，变了自动同步官网

## 隧道地址会变

Cloudflare **快速隧道**（`trycloudflare.com`）每次重启地址都变，这是它的设计。

本方案的对策：官网不硬编码地址，而是每次查询时读
`docs/data/track-endpoints.json`（同源，无 CORS 问题）。
隧道地址变化时，NAS 上的 cron 自动通过 GitHub API 更新这个 JSON 并提交，
官网下次查询就自动用上新地址，**无需改代码、无需重新部署站点**。

想要真正固定的域名（如 `track.excellent-voyage.cn`），需改用 Cloudflare
**命名隧道**，前提是把 `excellent-voyage.cn` 的 NS 从阿里云迁到 Cloudflare
（`www` 继续 CNAME 到 `github.io` 即可，站点本身不受影响）。

## 免费额度的现实边界

TrackingMCP 免费套餐 = **5 个活跃集装箱 + 100 次调用/月**。
公开端点对真实单号每天仅 1 次免费实时查询。因此：
- **箱号**（container_id）通常能拿到完整轨迹（实测 MSC 箱号 40 条节点）
- **提单/订舱号** 走 preview 路径，返回船司 + 航线 + ETA，完整轨迹需 key

要解锁完整能力，去 trackingmcp.com/auth/signup 注册免费 key，填到
`edge_proxy.py` 的 `TRACKINGMCP_API_KEY` 环境变量即可。

## 验证

```bash
node verify_tracking.js <tracking.html路径> <代理地址>
```
