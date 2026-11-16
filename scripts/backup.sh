#!/usr/bin/env bash
# Logical DB dump plus a consistent stopped-object-store snapshot.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DEST=${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$DEST"
chmod 700 "$DEST"
docker compose stop app worker
trap 'docker compose start app worker' EXIT
docker compose exec -T db pg_dump -U youtube -d youtube -Fc > "$DEST/database.dump"
docker compose stop minio
trap 'docker compose start minio app worker' EXIT
docker run --rm --volumes-from "$(docker compose ps -aq minio)" -v "$(cd "$DEST" && pwd):/backup" alpine:3.22 tar -czf /backup/objects.tar.gz -C /data .
docker compose start minio app worker
trap - EXIT
printf 'Backup saved to %s. Preserve .env separately in a secrets manager.\n' "$DEST"
