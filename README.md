# AgentPlane

AgentPlane 是一个面向企业场景的智能体控制与运行平台骨架。第一版采用模块化
FastAPI API、独立 Agent Worker、PostgreSQL、Redis Streams、LangGraph 和 Vue 3。

完整规划见 [`docs/architecture-and-mvp-plan.md`](docs/architecture-and-mvp-plan.md)。

## 本地开发

1. 从 `.env.example` 复制 `.env` 并配置模型接口。
2. 启动基础设施：

   ```powershell
   docker compose --env-file .env -f deploy/compose.yaml up -d
   ```

3. 安装后端依赖并执行迁移：

   ```powershell
   Set-Location backend
   uv sync --all-groups
   uv run alembic upgrade head
   ```

4. 分别启动 API 和 Worker：

   ```powershell
   uv run agentplane-api
   uv run agentplane-worker
   ```

5. 安装并启动前端：

   ```powershell
   Set-Location ..\web
   pnpm install
   pnpm dev
   ```

默认地址：

- Web：http://127.0.0.1:5173
- API：http://127.0.0.1:8000
- OpenAPI：http://127.0.0.1:8000/docs
- PostgreSQL：127.0.0.1:55432
- Redis：127.0.0.1:56379

基础设施使用非标准宿主机端口，避免覆盖本机已有 PostgreSQL 或 Redis。可在 `.env`
中通过 `POSTGRES_PORT` 和 `REDIS_PORT` 调整 Compose 映射，并同步修改连接 URL。

未配置 `MODEL_API_KEY` 与 `MODEL_NAME` 时，管理端和健康检查仍可运行，但创建 Run
会返回 `503 MODEL_NOT_CONFIGURED`。

## 检查

```powershell
python scripts/check.py
```

启动 Compose 并执行迁移后，可运行真实 PostgreSQL/Redis 集成闭环：

```powershell
Set-Location backend
$env:RUN_INTEGRATION = "1"
uv run pytest -m integration
```

浏览器最小闭环使用本机 Chrome：

```powershell
Set-Location ..\web
pnpm test:e2e
```

`scripts/check.py` 会检查 OpenAPI 及 TypeScript 生成文件是否漂移；集成测试和浏览器
测试因依赖本机服务与浏览器，使用上面的命令显式执行。

当前仓库只初始化本地 Git，不包含远端、提交或推送。
