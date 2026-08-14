#!/usr/bin/env sh
set -eu
umask 077

env_file=${1:-deploy/production.env}
backup_root=${2:-deploy/backups}
compose_file=deploy/docker-compose.production.yml
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$backup_root/$timestamp"

if [ ! -f "$env_file" ]; then
    echo "Missing $env_file." >&2
    exit 1
fi

set -a
. "$env_file"
set +a

mkdir -p "$target"

# The database backup includes user data, conversations, memories, and pgvector rows.
docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$target/postgres.dump"
docker compose --env-file "$env_file" -f "$compose_file" exec -T backend \
    tar -C /app/data/uploads -czf - . > "$target/uploads.tar.gz"
docker compose --env-file "$env_file" -f "$compose_file" exec -T backend \
    tar -C /app/runtime -czf - . > "$target/runtime.tar.gz"
sha256sum "$target"/* > "$target/SHA256SUMS"

echo "Backup complete: $target"
