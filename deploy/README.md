# Alibaba Cloud Production Deployment

This deployment targets one Alibaba Cloud Lightweight Application Server and a small
public workload of up to about ten concurrent users. The Qwen model is called through
DashScope; no local model or GPU is required.

## Architecture

- `frontend`: Nginx serves the built Vue application and proxies `/api/` and `/uploads/`.
- `backend`: one FastAPI/Uvicorn worker, reachable only from the Docker network.
- `postgres`: PostgreSQL 17 with pgvector. It owns accounts, conversations, memories,
  knowledge chunks, and embeddings.
- `redis`: disposable cache and short-lived coordination state.

Only Nginx exposes a public port. PostgreSQL, Redis, and FastAPI must never be opened
in the Alibaba Cloud firewall.

## Before The Server Is Touched

1. Bind an SSH key to the server. Prefer a non-root administrator account with `sudo`.
2. In the Alibaba Cloud security group and the instance firewall, allow TCP `80` and
   `443`. Restrict TCP `22` to the administrator's public IP. Do not allow `5432`,
   `6379`, or `8000`.
3. Create an A record for the intended domain, pointing to the server public IP. A
   Mainland China server needs a completed ICP filing before a public website can be
   served normally.
4. Obtain an HTTPS certificate for the exact domain through Alibaba Cloud Certificate
   Management Service. Download the Nginx certificate and private-key files.
5. Keep the DashScope API key and production secrets private. They must be entered on
   the server, not committed or sent through chat.

## Server Setup

Run these commands on CentOS Stream 9 after Docker Engine and the Docker Compose plugin
are installed. Configure an Alibaba Cloud Docker registry mirror first if Docker Hub is
unreachable from the server.

```bash
git clone <your-repository-url> agent_robot
cd agent_robot
cp deploy/production.env.example deploy/production.env
chmod 600 deploy/production.env
```

Edit `deploy/production.env` with the real domain, DashScope key, database password,
administrator password, and cursor-signing secret. Generate secrets locally on the
server with:

```bash
openssl rand -hex 32
```

Run the non-destructive configuration check before startup:

```bash
sh deploy/scripts/preflight.sh deploy/production.env
```

## HTTPS Certificate

Create these files on the server with permissions limited to the deploy user:

```text
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

Start the TLS configuration only after both files exist:

```bash
docker compose --env-file deploy/production.env \
  -f deploy/docker-compose.production.yml \
  -f deploy/docker-compose.production.https.yml up -d --build
```

The HTTPS configuration redirects HTTP to HTTPS and keeps the Vue frontend and API on
the same domain. This avoids browser CORS complexity and lets Nginx preserve FastAPI
streaming responses without buffering them.

Do not expose the login page publicly through the HTTP-only configuration. The base
Compose file is only for initial local Nginx validation before the certificate is ready.

## Verify

```bash
docker compose --env-file deploy/production.env \
  -f deploy/docker-compose.production.yml \
  -f deploy/docker-compose.production.https.yml ps

curl --fail https://your-domain.example/api/v1/health/ready
```

The readiness response must show `postgresql`, `redis`, `vector_store`, and `model` as
`available`. Confirm registration, login, a streamed chat response, session restoration,
and an administrator knowledge upload in a browser.

## Backups And Server Replacement

Create a daily backup after the service starts:

```bash
sh deploy/scripts/backup.sh deploy/production.env
```

The script saves a PostgreSQL custom-format backup, uploaded knowledge and avatars, the
legacy JSON vector backup, and checksums under `deploy/backups/`. Copy those backups to
an Alibaba Cloud OSS bucket or another machine. Retain at least seven successful daily
backups.

For a replacement server, restore the PostgreSQL dump into a pgvector-enabled PostgreSQL
container and restore `uploads.tar.gz` plus `runtime.tar.gz` into their named volumes.
The PostgreSQL backup is authoritative for pgvector data. Do not restore only
`vector-store.json`, because later administrator uploads live in PostgreSQL.
