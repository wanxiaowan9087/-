#!/usr/bin/env sh
set -eu

env_file=${1:-deploy/production.env}
compose_file=deploy/docker-compose.production.yml

if [ ! -f "$env_file" ]; then
    echo "Missing $env_file. Copy deploy/production.env.example first." >&2
    exit 1
fi

for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DASHSCOPE_API_KEY APP_ADMIN_USERNAME APP_ADMIN_PASSWORD APP_CURSOR_SIGNING_SECRET APP_CORS_ORIGINS; do
    if ! grep -q "^${key}=." "$env_file"; then
        echo "Missing $key in $env_file." >&2
        exit 1
    fi
done

if grep -Eq '=replace-with-|=robot\.example\.com' "$env_file"; then
    echo "Replace every placeholder in $env_file before deployment." >&2
    exit 1
fi

docker version --format '{{.Server.Version}}' >/dev/null
docker compose version >/dev/null
docker compose --env-file "$env_file" -f "$compose_file" config -q
echo "Preflight passed. Production Compose is syntactically valid."
