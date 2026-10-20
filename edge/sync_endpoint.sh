#!/bin/bash
# =============================================================
# 隧道地址变化 → 自动同步到官网配置 (GitHub API)
# =============================================================
# 快速隧道地址在每次重启后会变化。本脚本由 ev-track-tunnel 的
# ExecStartPost 或独立 cron 调用，检测到地址变化时，
# 自动更新官网仓库的 docs/data/track-endpoints.json 并提交。
#
# 用法: bash sync_endpoint.sh
# 依赖: curl, python3, GITHUB_TOKEN 环境变量
# =============================================================
set -uo pipefail

APP_DIR="/opt/ev-track"
LOG="/opt/ev-track/data/sync.log"
REPO="7jj62z2ffk-eng/excellentvoyage"
FILE="docs/data/track-endpoints.json"
BRANCH="main"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$APP_DIR/data/tunnel.log" 2>/dev/null | tail -1)
if [ -z "$URL" ]; then
  log "隧道地址未就绪，跳过"
  exit 0
fi

CURRENT=""
if [ -f "$APP_DIR/data/current_endpoint.txt" ]; then
  CURRENT=$(cat "$APP_DIR/data/current_endpoint.txt")
fi

if [ "$URL" = "$CURRENT" ]; then
  log "地址未变化: $URL"
  exit 0
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  log "未设置 GITHUB_TOKEN，跳过自动同步（地址=$URL）"
  echo "$URL" > "$APP_DIR/data/current_endpoint.txt"
  exit 0
fi

log "地址变化: ${CURRENT:-无} -> $URL"

# 用 GitHub API 取当前文件的 sha 与内容
SHA=$(curl -s -m 30 -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/$REPO/contents/$FILE?ref=$BRANCH" | \
  python3 -c "import sys,json;print(json.load(sys.stdin).get('sha',''))" 2>/dev/null)

if [ -n "$SHA" ]; then
  # 已有文件 -> 走 PUT 更新
  RESP=$(python3 - "$SHA" "$URL" <<'PY'
import base64, json, sys, urllib.request, urllib.error
sha, url = sys.argv[1], sys.argv[2]
REPO="7jj62z2ffk-eng/excellentvoyage"; FILE="docs/data/track-endpoints.json"; BRANCH="main"
import os
token=os.environ.get("GITHUB_TOKEN","")
data = {"endpoints":[url],
        "note":"官网货物跟踪代理地址（NAS + Cloudflare 隧道）。隧道地址变更时更新此文件并 push，官网会自动读取，无需改代码。"}
body = json.dumps({"message":"chore: 更新货物跟踪代理地址 (%s)"%url,
                   "content":base64.b64encode((json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode()).decode(),
                   "branch":BRANCH,"sha":sha}).encode()
r = urllib.request.Request("https://api.github.com/repos/%s/contents/%s"%(REPO,FILE),data=body,method="PUT")
r.add_header("Authorization","Bearer "+token); r.add_header("Accept","application/vnd.github+json")
r.add_header("Content-Type","application/json")
try:
    with urllib.request.urlopen(r,timeout=40) as resp:
        print(resp.status)
except urllib.error.HTTPError as e:
    print("ERR",e.code)
PY
)
  log "更新结果: $RESP"
else
  # 文件不存在 -> 走 POST 创建
  RESP=$(python3 - "$URL" <<'PY'
import base64, json, sys, urllib.request, urllib.error, os
url=sys.argv[1]
REPO="7jj62z2ffk-eng/excellentvoyage"; FILE="docs/data/track-endpoints.json"; BRANCH="main"
token=os.environ.get("GITHUB_TOKEN","")
data = {"endpoints":[url],
        "note":"官网货物跟踪代理地址（NAS + Cloudflare 隧道）。"}
body = json.dumps({"message":"chore: 新增货物跟踪代理地址配置",
                   "content":base64.b64encode((json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode()).decode(),
                   "branch":BRANCH}).encode()
r = urllib.request.Request("https://api.github.com/repos/%s/contents/%s"%(REPO,FILE),data=body,method="PUT")
r.add_header("Authorization","Bearer "+token); r.add_header("Accept","application/vnd.github+json")
r.add_header("Content-Type","application/json")
try:
    with urllib.request.urlopen(r,timeout=40) as resp: print(resp.status)
except urllib.error.HTTPError as e: print("ERR",e.code)
PY
)
  log "创建结果: $RESP"
fi

echo "$URL" > "$APP_DIR/data/current_endpoint.txt"
log "已记录当前地址: $URL"
