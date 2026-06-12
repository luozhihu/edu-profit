#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.production.yml"
ENV_FILE="$PROJECT_DIR/.env.production"
SERVICE_NAME="edu-profit"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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

docker_uses_podman() {
  docker --version 2>/dev/null | grep -qi podman && return 0
  docker info 2>&1 | grep -qi 'Emulate Docker CLI using podman' && return 0
  return 1
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    if docker_uses_podman; then
      fail "当前 docker 命令仍指向 Podman。请先移除 podman-docker 并重新执行 ./deploy.sh。"
    fi
    return
  fi

  log "安装 Docker 和 Docker Compose"
  if command -v apt-get >/dev/null 2>&1; then
    run_root apt-get update
    if ! run_root apt-get install -y docker.io docker-compose-v2; then
      run_root apt-get install -y docker.io docker-compose-plugin
    fi
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    local package_manager
    package_manager="$(command -v dnf || command -v yum)"
    local -a rpm_options=(
      --setopt=retries=10
      --setopt=timeout=120
      --setopt=minrate=1
    )
    run_root "$package_manager" "${rpm_options[@]}" install -y ca-certificates curl

    if rpm -q podman-docker >/dev/null 2>&1; then
      log "移除 podman-docker，避免 docker 命令指向 Podman 兼容层"
      run_root "$package_manager" "${rpm_options[@]}" remove -y podman-docker
    fi

    if ! run_root "$package_manager" "${rpm_options[@]}" install -y dnf-plugins-core; then
      run_root "$package_manager" "${rpm_options[@]}" install -y yum-utils
    fi
    if ! run_root "$package_manager" config-manager --add-repo \
      https://download.docker.com/linux/centos/docker-ce.repo; then
      run_root yum-config-manager --add-repo \
        https://download.docker.com/linux/centos/docker-ce.repo
    fi
    run_root "$package_manager" "${rpm_options[@]}" --setopt=install_weak_deps=False install -y \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    fail "未检测到受支持的包管理器。请先安装 Docker Engine 和 Docker Compose 插件。"
  fi

  if docker_uses_podman; then
    fail "当前系统仍在使用 Podman 的 docker 兼容层。请先移除 podman-docker 后重新执行 ./deploy.sh。"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files | grep -q '^docker\.service'; then
      run_root systemctl enable --now docker
    elif systemctl list-unit-files | grep -q '^podman\.service'; then
      run_root systemctl enable --now podman
    else
      log "未找到 docker.service，跳过服务启动。"
    fi
  elif command -v service >/dev/null 2>&1; then
    run_root service docker start || true
  fi

  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 ||
    fail "Docker 安装完成，但 Docker Compose 插件不可用。"
}

docker_compose() {
  if docker_uses_podman; then
    fail "当前 docker 命令仍指向 Podman。请先移除 podman-docker 后重新执行 ./deploy.sh。"
  elif docker info >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    fail "当前用户无权访问 Docker，请使用 root 执行脚本或将用户加入 docker 组。"
  fi
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  log "安装 uv"
  run_root mkdir -p /root/.cache
  local installer
  installer="$(mktemp)"
  curl -fsSL https://astral.sh/uv/install.sh -o "$installer"
  run_root env UV_INSTALL_DIR=/usr/local/bin bash "$installer"
  rm -f "$installer"
}

ensure_python() {
  if uv python find 3.11 >/dev/null 2>&1; then
    return
  fi
  log "安装 Python 3.11"
  uv python install 3.11
}

deploy_host() {
  log "切换到主机部署模式"
  install_uv
  ensure_python

  mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/uploads"
  # shellcheck disable=SC1090
  set -a
  . "$ENV_FILE"
  set +a
  uv sync --frozen --no-dev --no-install-project
  APP_ENV=production \
  DATABASE_URL="sqlite:///$PROJECT_DIR/data/edu_profit.db" \
  UPLOAD_DIR="$PROJECT_DIR/uploads" \
  BOOTSTRAP_ADMIN_USERNAME="${BOOTSTRAP_ADMIN_USERNAME:-admin}" \
  BOOTSTRAP_ADMIN_PASSWORD="${BOOTSTRAP_ADMIN_PASSWORD:-}" \
  ./.venv/bin/python -m app.bootstrap

  cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=edu-profit
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment=APP_ENV=production
ExecStart=$PROJECT_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8000} --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    run_root systemctl daemon-reload
    run_root systemctl enable --now "$SERVICE_NAME"
  else
    fail "主机部署需要 systemd。当前系统未检测到 systemctl。"
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
      if docker_uses_podman; then
        echo podman
      elif docker info >/dev/null 2>&1; then
        docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
      else
        sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
      fi
    )"
    [ "$status" = "podman" ] && fail "当前 docker 命令仍指向 Podman。请先移除 podman-docker。"
    [ "$status" = "healthy" ] && return
    [ "$status" = "unhealthy" ] && break
    sleep 5
  done

  docker_compose logs --tail=120 app
  fail "应用健康检查失败。"
}

wait_for_local_health() {
  local port="${APP_PORT:-8000}"
  for _ in $(seq 1 36); do
    if curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      return
    fi
    sleep 5
  done
  journalctl -u "$SERVICE_NAME" --no-pager -n 120 2>/dev/null || true
  fail "本机健康检查失败。"
}

main() {
  [ -f "$COMPOSE_FILE" ] || fail "找不到 $COMPOSE_FILE"
  write_env

  if [ "${DEPLOY_MODE:-host}" = "docker" ]; then
    ensure_docker
    log "构建并启动服务"
    if ! docker_compose up -d --build --remove-orphans; then
      log "Docker 方案失败，切换到主机部署模式"
      deploy_host
    fi
  else
    deploy_host
  fi

  log "等待应用健康检查"
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    wait_for_local_health
  else
    wait_for_health
  fi

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
