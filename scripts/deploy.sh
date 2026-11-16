#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v docker >/dev/null; then
  if [[ $(uname -s) != Linux ]] || [[ ! -f /etc/os-release ]]; then
    echo 'Install Docker Engine with Compose v2, then rerun this script.' >&2; exit 1
  fi
  . /etc/os-release
  if [[ ${ID:-} != ubuntu ]]; then
    echo 'Automatic installation supports Ubuntu. Install Docker + Compose v2 first.' >&2; exit 1
  fi
  SUDO=()
  if [[ $EUID != 0 ]]; then SUDO=(sudo); fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  "${SUDO[@]}" curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" | "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  "${SUDO[@]}" systemctl enable --now docker
fi
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then DOCKER=(sudo docker); fi
"${DOCKER[@]}" compose version >/dev/null
if [[ ! -f .env ]]; then
  umask 077
  DB_SECRET=$(openssl rand -hex 24)
  STORAGE_SECRET=$(openssl rand -hex 24)
  DEMO_SECRET=$(openssl rand -hex 12)
  cat > .env <<ENV
POSTGRES_PASSWORD=$DB_SECRET
MINIO_ROOT_USER=youtube-storage
MINIO_ROOT_PASSWORD=$STORAGE_SECRET
S3_BUCKET=videos
PUBLIC_ORIGIN=${PUBLIC_ORIGIN:-}
COOKIE_SECURE=${COOKIE_SECURE:-false}
MAX_UPLOAD_BYTES=536870912
PORT=${PORT:-8080}
BIND_ADDRESS=${BIND_ADDRESS:-0.0.0.0}
DEMO_PASSWORD=$DEMO_SECRET
ENV
fi
"${DOCKER[@]}" compose up -d --build --wait --wait-timeout 300
"${DOCKER[@]}" compose --profile tools run --rm seed
printf '\nApplication: http://localhost:%s\nAPI docs: http://localhost:%s/docs\n' "${PORT:-8080}" "${PORT:-8080}"
printf 'Demo accounts and the generated demo password are documented by the seed output and in .env.\n'
