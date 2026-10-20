# Cargo Tracker · 货物跟踪台

一个**零依赖、可长期使用**的本地货物跟踪工具：录入箱单 / 提单 / 订舱号，一键拉取船公司真实状态，带本地数据库做轨迹留存。数据全程存本机，不上传任何第三方。

> 适用场景：货代 / 国际物流日常跟踪客户货物，尤其是 EV 海运集装箱运输的订舱与在途监控。

---

## 功能特性

- **快速查询**：输入箱号 / 提单号 / 订舱号，立即返回船司、航线、ETA、状态。
- **货物登记与长期跟踪**：把货物登记进本地库，随查随更新，轨迹时间线落库留存。
- **船公司自动识别**：箱号前 4 位（如 `MSKU` → Maersk、`MSCU` → MSC）自动识别船司。
- **多类型自动重试**：免费端点对提单 / 订舱类型的实时查询有每日额度，查询失败或撞额度时自动换类型（container_id → bill_of_lading → booking_id）重试，命中后把正确类型写回记录。
- **轨迹时间线**：装船 / 卸船 / 提货等里程碑分色展示（需 API key 解锁详细事件）。
- **官网深链兜底**：一键跳船司官网跟踪页；未收录的船司回退到搜索链接。
- **零依赖**：仅用 Python 标准库（`http.server` + `sqlite3`），无需 `pip install`，任何装了 Python 的机器都能跑。

---

## 数据来源与选型（为什么这么做）

在 GitHub 上调研了货物跟踪生态后确定的方案：

| 方案 | 结论 |
| --- | --- |
| 纯前端 mock（如 `global-container-tracker`） | 无真实数据，弃用 |
| 爬虫（Maersk scraper / Playwright 爬虫） | 天天要维护、易失效，不适合长期用，弃用 |
| **TrackingMCP 公开免密端点** | ✅ 覆盖 229 家船公司、DCSA 标准、无需 key 即可按号查真实状态；填免费 key 解锁组合跟踪 / 滞箱预警 / 详细轨迹 |

本工具通过 `POST https://api.trackingmcp.com/v1/track/public` 获取真实数据。

---

## 快速开始

```bash
# 需要 Python 3.8+（仅用标准库）
cd cargo-tracker
python3 app.py            # 默认端口 8765；或 ./start.sh
# 浏览器打开 http://127.0.0.1:8765
```

停服：`Ctrl + C`。

---

## 配置（可选 · 提升能力）

免费层只返回**预览数据**（识别 + 航线 + ETA，无逐条轨迹事件），且有每日实时查询额度。

解锁完整能力（详细里程碑轨迹、组合跟踪、滞箱预警、更高额度）：

1. 到 <https://navo24.com/signup> 申请一个 **免费 API key**。
2. 在工具「设置」页粘贴保存（key 存于本地 `cargo.db`，不上传）。

---

## 目录结构

```
cargo-tracker/
├── app.py              # 后端：HTTP 服务 + SQLite + TrackingMCP 客户端（含代理支持）
├── static/
│   └── index.html      # 前端单页仪表盘（零构建、可离线）
├── start.sh            # 一键启动脚本
├── cargo.db            # 运行时生成的本地数据库（自动创建，已被 .gitignore 忽略）
└── README.md
```

---

## API 说明

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/shipments` | 货物列表 |
| POST | `/api/shipments` | 登记货物（支持 `carrier_code` 显式指定船司） |
| GET  | `/api/shipments/<id>` | 货物详情（含轨迹事件） |
| POST | `/api/shipments/<id>/track` | 跟踪并更新该货物 |
| POST | `/api/track` | 快速查询（不入库） |
| GET  | `/api/carriers` | 支持的船司与深链模板 |
| GET/POST | `/api/config` | 读取 / 保存配置（API key 等） |

---

## 部署 / 托管说明

- **代码托管到 GitHub**：本仓库即为标准 git 仓库，可直接 `git push` 到你的 GitHub 账号做备份 / 分享 / 版本管理，任意机器 `git clone` 后 `python3 app.py` 即用。
- **GitHub Pages 直接托管（不可行）**：本工具需要 Python 后端 + SQLite，Pages 仅托管静态文件；且 TrackingMCP API 的 CORS 仅放行 `trackingmcp.com` 自身域名，浏览器从 Pages 域直连会被拦。若要在官网（如 `excellent-voyage.cn`）提供在线跟踪，需要额外配一个**小代理**（可跑在自有服务器 / NAS 或 Cloudflare、Vercel 等 serverless 上）由后端转发 API 请求。

---

## 常见问题

**Q：查询返回 `DEMO_ONLY / visitor_used`？**
A：免费端点当日实时查询额度已用尽。换类型重试通常会回退到预览数据；要稳定使用请配置免费 API key。

**Q：为什么我的号查不到轨迹事件？**
A：免费层仅返回预览（识别 + 航线 + ETA）。逐条里程碑事件需 API key 解锁。

**Q：数据存在哪？安全吗？**
A：全部存在本机 `cargo.db`（SQLite），不上传任何第三方。API key 也只存本地库。

---

## License

MIT
