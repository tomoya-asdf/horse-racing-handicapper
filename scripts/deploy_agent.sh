#!/usr/bin/env bash
#
# ホスト側デプロイエージェント (Linux/Debian 用)。
# WebUI からのアップデート依頼を受けて git pull + docker compose build + up -d を実行する。
#
# Windows 用の scripts/deploy_agent.ps1 と同じ役割。webui コンテナに docker.sock を
# 渡さないため、デプロイはホスト上のこのスクリプトが担当する。WebUI とは共有ボリューム
# ./data 上の JSON でやりとりする。
#
#   - data/deploy_request.json  : WebUI が書くデプロイ依頼。本スクリプトが処理後に削除する。
#   - data/restart_request.json : WebUI が書く再起動依頼。本スクリプトが処理後に削除する。
#   - data/deploy_status.json   : 本スクリプトが書く状態(現在バージョン/更新有無/進捗)。
#
# コンテナを丸ごと作り直しても、このスクリプトはホスト上で動き続けるため安全に全スタックを
# 更新できる。git と docker(compose プラグイン)がホストで使えることが前提。
#
# 使い方:
#   chmod +x scripts/deploy_agent.sh
#   ./scripts/deploy_agent.sh            # 既定30秒間隔
#   ./scripts/deploy_agent.sh 60         # 60秒間隔
#
# systemd で常駐させる例は末尾の注記を参照。

set -u

INTERVAL="${1:-30}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
COMPOSE="$REPO/docker-compose.yml"
DATA="$REPO/data"
STATUS_FILE="$DATA/deploy_status.json"
REQUEST_FILE="$DATA/deploy_request.json"
RESTART_REQUEST_FILE="$DATA/restart_request.json"

mkdir -p "$DATA"

# docker compose v2 (プラグイン) / v1 (docker-compose) を検出
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "ERROR: docker compose が見つかりません" >&2
  exit 1
fi

# 直近のデプロイ結果(状態ファイルに引き継ぐ)
last_deploy_at=""
last_deploy_result=""
last_message=""

now() { date --iso-8601=seconds; }
current_ref() { git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true; }
local_sha() { git -C "$REPO" rev-parse --short HEAD 2>/dev/null || true; }
remote_sha() { git -C "$REPO" rev-parse --short '@{u}' 2>/dev/null || true; }

# JSON 文字列値のエスケープ(BOM なし UTF-8 で書くので Python json.loads がそのまま読める)
json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

# 空なら null、そうでなければ "エスケープ済み文字列"
json_str() {
  if [ -z "${1:-}" ]; then printf 'null'; else printf '"%s"' "$(json_escape "$1")"; fi
}

# 状態を原子的に書き出す(WebUI が読み取り中の破損を避けるため temp + mv)
write_status() {
  local state=$1 update=$2 cur_sha=$3 cur_ref=$4 rem_sha=$5
  local ts; ts=$(now)
  cat > "$STATUS_FILE.tmp" <<EOF
{
  "current_sha": $(json_str "$cur_sha"),
  "current_ref": $(json_str "$cur_ref"),
  "remote_sha": $(json_str "$rem_sha"),
  "update_available": $update,
  "last_checked_at": $(json_str "$ts"),
  "state": $(json_str "$state"),
  "last_deploy_at": $(json_str "$last_deploy_at"),
  "last_deploy_result": $(json_str "$last_deploy_result"),
  "message": $(json_str "$last_message"),
  "agent_seen_at": $(json_str "$ts")
}
EOF
  mv -f "$STATUS_FILE.tmp" "$STATUS_FILE"
}

fail_deploy() {
  local step=$1 log=$2 ref=$3
  last_deploy_result="failed"
  last_message="[$step] 失敗:"$'\n'"$(printf '%s' "$log" | tail -n 40)"
  write_status "failed" false "$(local_sha)" "$ref" "$(remote_sha)"
}

run_deploy() {
  local ref=$1
  last_deploy_at=$(now)
  write_status "running" false "$(local_sha)" "$ref" "$(remote_sha)"

  local log="" out code
  out=$(git -C "$REPO" pull --ff-only 2>&1); code=$?
  log+="### git pull"$'\n'"$out"$'\n'
  if [ $code -ne 0 ]; then fail_deploy "git pull" "$log" "$ref"; return; fi

  out=$("${DC[@]}" -f "$COMPOSE" build 2>&1); code=$?
  log+="### docker compose build"$'\n'"$out"$'\n'
  if [ $code -ne 0 ]; then fail_deploy "docker compose build" "$log" "$ref"; return; fi

  out=$("${DC[@]}" -f "$COMPOSE" up -d 2>&1); code=$?
  log+="### docker compose up -d"$'\n'"$out"$'\n'
  if [ $code -ne 0 ]; then fail_deploy "docker compose up -d" "$log" "$ref"; return; fi

  last_deploy_result="success"
  last_message="デプロイ成功:"$'\n'"$(printf '%s' "$log" | tail -n 20)"
  write_status "success" false "$(local_sha)" "$ref" "$(remote_sha)"
}

run_restart() {
  local ref=$1
  last_deploy_at=$(now)
  write_status "running" false "$(local_sha)" "$ref" "$(remote_sha)"

  local out code
  out=$("${DC[@]}" -f "$COMPOSE" restart collector predictor webui 2>&1); code=$?
  if [ $code -ne 0 ]; then
    last_deploy_result="failed"
    last_message="[restart] 失敗:"$'\n'"$(printf '%s' "$out" | tail -n 40)"
    write_status "failed" false "$(local_sha)" "$ref" "$(remote_sha)"
    return
  fi
  last_deploy_result="success"
  last_message="再起動成功:"$'\n'"$(printf '%s' "$out" | tail -n 20)"
  write_status "success" false "$(local_sha)" "$ref" "$(remote_sha)"
}

echo "deploy agent started. repo=$REPO interval=${INTERVAL}s compose=[${DC[*]}]"

while true; do
  {
    ref=$(current_ref)
    git -C "$REPO" fetch --quiet 2>/dev/null || true
    local_s=$(local_sha)
    remote_s=$(remote_sha)
    update=false
    if [ -n "$remote_s" ] && [ -n "$local_s" ] && [ "$remote_s" != "$local_s" ]; then
      update=true
    fi

    if [ -f "$REQUEST_FILE" ]; then
      rm -f "$REQUEST_FILE"
      echo "$(now) deploy requested -> running"
      run_deploy "$ref"
    elif [ -f "$RESTART_REQUEST_FILE" ]; then
      rm -f "$RESTART_REQUEST_FILE"
      echo "$(now) restart requested -> running"
      run_restart "$ref"
    else
      state="${last_deploy_result:-idle}"
      write_status "$state" "$update" "$local_s" "$ref" "$remote_s"
    fi
  } || echo "agent loop error (continuing)"
  sleep "$INTERVAL"
done

# --- systemd で常駐させる例 ---------------------------------------------------
# /etc/systemd/system/horse-deploy-agent.service を作成:
#
#   [Unit]
#   Description=Horse racing deploy agent
#   After=docker.service
#   Requires=docker.service
#
#   [Service]
#   Type=simple
#   User=<リポジトリ所有ユーザー>
#   WorkingDirectory=/opt/horse-racing-handicapper
#   ExecStart=/opt/horse-racing-handicapper/scripts/deploy_agent.sh
#   Restart=always
#   RestartSec=10
#
#   [Install]
#   WantedBy=multi-user.target
#
# 反映:
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now horse-deploy-agent
#   journalctl -u horse-deploy-agent -f   # ログ確認
