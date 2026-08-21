# Agent Robot

`agent_robot` is the standalone robot customer-service edition. It does not
share the legacy project's PostgreSQL database, Redis logical database, Docker
volumes, or service ports.

| Component | Endpoint / isolation |
| --- | --- |
| FastAPI | `http://localhost:8001/api/v1` |
| Vue | `http://localhost:5174` |
| PostgreSQL | `localhost:15433`, database `agent_robot` |
| Redis | dedicated logical database `1` |
| Docker volumes | `agent_robot-*` |

## Start

Windows 用户可以直接双击项目根目录的 `start-agent-robot.cmd` 一键启动；它会在 Docker 未运行时自动尝试打开 Docker Desktop，启动 Docker 服务、等待后端健康检查，再打开 Vue 前端终端窗口。首次启动会构建后端镜像，后续可使用 `start-agent-robot.cmd -SkipBuild` 跳过构建。

Open PowerShell in this directory. Provide the three values only in the current
terminal session, then start the services:

```powershell
$env:DASHSCOPE_API_KEY = 'your-local-key'
$env:APP_ADMIN_USERNAME = 'xiaow'
$env:APP_ADMIN_PASSWORD = 'Valid123!'
docker compose -p agent_robot up -d --build
```

Check that the backend, PostgreSQL, and Redis are healthy:

```powershell
docker compose -p agent_robot ps
Invoke-WebRequest http://localhost:8001/api/v1/health/ready
```

Start the frontend in a separate terminal:

```powershell
Set-Location D:\python\PythonProject\agent_robot\frontend
npm install --cache D:\codex_store\npm-cache
$env:VITE_API_BASE_URL = 'http://localhost:8001/api/v1'
npm run dev -- --port 5174
```

## External Report Authorization

External report records are not addressed directly by platform users. An
administrator must explicitly map a platform user UUID to the permitted
external business user ID using:

```text
PUT /api/v1/admin/external-identity-mappings
```

Example body:

```json
{
  "platform_user_id": "platform-user-uuid",
  "external_user_id": "1001"
}
```

The report workflow rejects requests with no active mapping before it accesses
external report data. The local fixture has 180 records: 120 base rows and 60
extension rows.

## Stop

```powershell
docker compose -p agent_robot down
```

This stops the standalone services while retaining their independent Docker
volumes. The legacy `Agent` project remains unaffected.
