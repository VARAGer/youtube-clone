# Deployment, operation and restore

## Clean Ubuntu

From the extracted repository run `bash scripts/deploy.sh`. On Ubuntu the script
installs Docker from Docker's signed apt repository if absent. Root or sudo
access and outbound access to apt, Docker Hub and Python package sources are
required. Existing Docker installs must provide Compose v2 and a running engine.
MinIO is compiled from its pinned upstream source release rather than relying
on discontinued public container image registries. The initial build also
requires access to the Go module proxy/checksum database and may take several
minutes.

The script generates a mode-600 `.env`, starts PostgreSQL, MinIO, FastAPI and the
worker, waits for readiness, then generates and seeds real demo clips. It is
safe to run again; demo identities and titles are reused.

Default URL: http://localhost:8080. For a VM, use its IP in the browser. By default the server validates the
browser Origin against the request host, so direct localhost/IP access works.
Behind a reverse proxy set PUBLIC_ORIGIN to the external URL.
Use a real HTTPS origin with `COOKIE_SECURE=true` behind a reverse proxy when
exposing the service publicly. To change settings after first startup, edit
`.env` and run `docker compose up -d --force-recreate app worker`.

## Common commands

```sh
docker compose ps
docker compose logs --tail=100 app worker
docker compose logs -f worker
docker compose restart worker
docker compose --profile tools run --rm seed
docker compose down           # preserves named volumes
```

`docker compose down -v` irreversibly removes the database and media. Do not run
it against data you want to retain. Readiness at `/health/ready` checks required
dependencies; liveness at `/health/live` reports that the web process runs.
The OpenAPI UI is `/docs` and machine-readable schema is `/openapi.json`.

## Backups and restore

Run `bash scripts/backup.sh /absolute/backup/directory` using a Docker-enabled
account. The script stops API and worker for a consistent database/object
snapshot, dumps PostgreSQL and archives the stopped MinIO volume. Copy the
backup off-host and retain `.env` separately in a secrets manager.

To restore into a fresh deployment with the same `.env`:

1. Start infrastructure: `docker compose up -d db minio`.
2. Stop MinIO: `docker compose stop minio`.
3. Restore its volume using a temporary container with `--volumes-from` the
   MinIO container and extract `objects.tar.gz` into `/data`.
4. Restore the DB: `cat database.dump | docker compose exec -T db pg_restore -U youtube -d youtube --clean --if-exists`.
5. Start services: `docker compose up -d`; check readiness and play a known video.

A backup is not proven until restored and inspected. Full restore testing must
be recorded in `docs/verification.md` before claiming disaster recovery works.
