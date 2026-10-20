#!/bin/bash
# =============================================================
# 嘉旅官网货物跟踪 · NAS 部署脚本 (fnOS / Debian 12)
# =============================================================
# 在 NAS 上部署:
#   1) 边缘代理 (edge_proxy.py)  —— 处理 CORS、缓存、限流
#   2) cloudflared 隧道          —— 把代理暴露到公网
#
# 用法:  bash deploy_nas.sh
# =============================================================
set -euo pipefail

APP_DIR="/opt/ev-track"
PORT="${TRACK_PORT:-8788}"
TUNNEL_LOG="/var/log/ev-track-tunnel.log"

echo "==> [1/6] 创建目录"
mkdir -p "$APP_DIR"

echo "==> [2/6] 检查 cloudflared"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "    未安装 cloudflared，尝试下载..."
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64) CFURL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" ;;
    aarch64) CFURL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" ;;
    *) echo "    ✗ 不支持的架构: $ARCH"; exit 1 ;;
  esac
  curl -sL "$CFURL" -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared
fi
echo "    cloudflared: $(cloudflared --version 2>/dev/null | head -1)"

echo "==> [3/6] 部署代理代码"
# edge_proxy.py 由本脚本同目录提供
cp -f "$(dirname "$0")/edge_proxy.py" "$APP_DIR/edge_proxy.py"
echo "    已安装到 $APP_DIR/edge_proxy.py"

echo "==> [4/6] 配置 systemd 服务"
cat > /etc/systemd/system/ev-track.service <<UNIT
[Unit]
Description=Excellent Voyage Cargo Tracking Edge Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/edge_proxy.py --port $PORT
Restart=always
RestartSec=10
Environment=TRACK_CACHE_TTL=21600
Environment=TRACK_RATE_LIMIT=10
StandardOutput=append:/var/log/ev-track.log
StandardError=append:/var/log/ev-track.log

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/ev-track-tunnel.service <<UNIT
[Unit]
Description=Excellent Voyage Cargo Tracking Cloudflare Tunnel
After=network-online.target ev-track.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:$PORT --no-autoupdate --protocol http2
Restart=always
RestartSec=15
StandardOutput=append:$TUNNEL_LOG
StandardError=append:$TUNNEL_LOG

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ev-track.service ev-track-tunnel.service
systemctl restart ev-track.service
sleep 3

echo "==> [5/6] 启动隧道"
systemctl restart ev-track-tunnel.service
sleep 20

echo "==> [6/6] 输出公网地址"
URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | tail -1)
if [ -z "$URL" ]; then
  echo "    ✗ 隧道未就绪，请查看日志: journalctl -u ev-track-tunnel -n 50"
  exit 1
fi

echo ""
echo "======================================================"
echo " 部署完成"
echo "   代理本地地址 : http://127.0.0.1:$PORT"
echo "   公网地址     : $URL"
echo "   健康检查     : $URL/api/health"
echo ""
echo " ⚠️ 快速隧道地址在重启后会变化。"
echo "    官网 tracking.html 里的 TRACK_ENDPOINTS 需同步更新，"
echo "    或改用 Cloudflare 命名隧道获得固定域名。"
echo "======================================================"
echo "$URL" > /opt/ev-track/public_url.txt
echo "地址已保存: /opt/ev-track/public_url.txt"
