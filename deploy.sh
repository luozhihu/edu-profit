#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.production.yml"
ENV_FILE="$PROJECT_DIR/.env.production"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "需要 root 权限或 sudo 才能安装 Docker。"
  fi
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi

  command -v apt-get >/dev/null 2>&1 ||
    fail "未检测到 Docker。自动安装目前仅支持 Ubuntu/Debian，请先安装 Docker Compose。"

  log "安装 Docker 和 Docker Compose"
  run_root apt-get update
  if ! run_root apt-get install -y docker.io docker-compose-v2; then
    run_root apt-get install -y docker.io docker-compose-plugin
  fi
  if command -v systemctl >/dev/null 2>&1; then
    run_root systemctl enable --now docker
  fi
}

docker_compose() {
  if docker info >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    fail "当前用户无权访问 Docker，请使用 root 执行脚本或将用户加入 docker 组。"
  fi
}

write_env() {
  if [ -f "$ENV_FILE" ]; then
    log "保留现有生产配置：$ENV_FILE"
    return
  fi

  local admin_password="${ADMIN_PASSWORD:-}"
  local admin_username="${ADMIN_USERNAME:-admin}"
  local app_port="${APP_PORT:-8000}"
  local max_upload_bytes="${MAX_UPLOAD_BYTES:-10485760}"

  [[ "$admin_username" =~ ^[A-Za-z0-9._-]+$ ]] ||
    fail "管理员账号只能包含字母、数字和 . _ -"
  [[ "$app_port" =~ ^[0-9]+$ ]] && [ "$app_port" -ge 1 ] && [ "$app_port" -le 65535 ] ||
    fail "APP_PORT 必须是 1 到 65535 之间的端口号。"
  [[ "$max_upload_bytes" =~ ^[0-9]+$ ]] && [ "$max_upload_bytes" -gt 0 ] ||
    fail "MAX_UPLOAD_BYTES 必须是正整数。"

  if [ -z "$admin_password" ]; then
    if [ -t 0 ]; then
      read -r -s -p "设置管理员密码（至少 12 位）: " admin_password
      printf '\n'
    else
      fail "首次非交互部署必须设置 ADMIN_PASSWORD 环境变量。"
    fi
  fi
  [ "${#admin_password}" -ge 12 ] || fail "管理员密码至少需要 12 位。"
  [[ "$admin_password" =~ ^[A-Za-z0-9._!@%+=:-]+$ ]] ||
    fail "管理员密码只能包含字母、数字和 . _ ! @ % + = : -"

  umask 077
  cat >"$ENV_FILE" <<EOF
APP_SECRET=$(random_hex 32)
BOOTSTRAP_ADMIN_USERNAME=$admin_username
BOOTSTRAP_ADMIN_PASSWORD=$admin_password
APP_PORT=$app_port
MAX_UPLOAD_BYTES=$max_upload_bytes
EOF
  log "已生成生产配置：$ENV_FILE"
}

wait_for_health() {
  local container_id status
  container_id="$(docker_compose ps -q app)"
  [ -n "$container_id" ] || fail "应用容器未启动。"

  for _ in $(seq 1 36); do
    status="$(
      if docker info >/dev/null 2>&1; then
        docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
      else
        sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
      fi
    )"
    [ "$status" = "healthy" ] && return
    [ "$status" = "unhealthy" ] && break
    sleep 5
  done

  docker_compose logs --tail=120 app
  fail "应用健康检查失败。"
}

main() {
  [ -f "$COMPOSE_FILE" ] || fail "找不到 $COMPOSE_FILE"
  ensure_docker
  write_env

  log "构建并启动服务"
  docker_compose up -d --build --remove-orphans

  log "等待应用健康检查"
  wait_for_health

  local port
  port="$(awk -F= '$1=="APP_PORT"{print $2}' "$ENV_FILE")"
  log "部署完成"
  printf '访问地址: http://%s:%s\n' "$(hostname -I 2>/dev/null | awk '{print $1}' || printf 'SERVER_IP')" "$port"
  printf '管理员账号: %s\n' "$(awk -F= '$1=="BOOTSTRAP_ADMIN_USERNAME"{print $2}' "$ENV_FILE")"
  printf '生产配置和首次管理员密码保存在: .env.production\n'
  printf '查看日志: docker compose --env-file .env.production -f docker-compose.production.yml logs -f app\n'
  printf '再次执行 ./deploy.sh 可安全更新应用，SQLite 数据库和附件不会被删除。\n'
}

main "$@"
