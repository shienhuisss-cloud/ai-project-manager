# 部署说明（M4）

本文说明如何把「AI 项目需求管家」部署到面向多客户试用的环境。

## 1. 技术栈与持久化要求

- 后端：Python + FastAPI + SQLite（当前）。
- 前端：纯静态 HTML/CSS/JS，由 FastAPI 直接托管。
- 数据：SQLite 单文件 `app.db`。

**关键约束**：SQLite 依赖「有持久磁盘」的环境。若托管平台每次部署会重置文件系统（无持久磁盘），数据会丢失，必须换用托管数据库（PostgreSQL / MySQL）并改造 `backend/main.py` 的存储层。选择环境前请先确认它有持久卷（persistent volume）。

## 2. 单机部署（VPS / 自己的服务器）

1. 安装 Python 3.10+ 和依赖。
2. 复制代码，创建 `.env`（见 `.env.example`）填模型密钥。
3. 用生产服务器运行（不要用开发模式裸跑）：

```bash
pip install fastapi "uvicorn[standard]" pydantic
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> 注意：当前实现用单个 SQLite 文件 + 内存连接，**必须 `--workers 1`**。多进程会各开一个写连接导致冲突。要横向扩展需先换成 PostgreSQL。

4. 用反向代理（Nginx / Caddy）提供 HTTPS，并转发到 8000 端口。

## 3. 托管平台（PaaS）——需要你提供的账户

以下平台支持持久磁盘 + 免费额度，适合小范围试用。**部署需要你本人提供对应平台的账户和登录授权**，我无法代为创建：

| 平台 | 持久存储 | 说明 |
| --- | --- | --- |
| Railway | 有持久卷 | 需挂载 Volume 到 `app.db` 所在目录 |
| Fly.io | 有 Volume | `fly volumes create` |
| Render | 有 Disk | 免费层 Disk 在闲置后可能被回收，需注意 |
| 自建 VPS | 有 | 见第 2 节 |

部署要点（以 Railway 为例）：
1. 关联仓库，设置启动命令 `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1`。
2. 挂载持久卷到 `/app`（或 `DB_PATH` 指向的目录）。
3. 在平台的环境变量里填 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（不要提交到仓库）。
4. 设置 `DB_PATH=/app/app.db`（指向持久卷）。

## 4. 安全配置

- 生产环境建议通过反向代理设置请求体大小上限，与后端 `MAX_SOURCE_LENGTH` 一致。
- `SESSION_TTL_HOURS`、`RATE_LIMIT_MAX` 等通过环境变量调整。
- 模型密钥只放环境变量，绝不进源码或前端。

## 5. 数据备份与恢复

```bash
# 备份（可在服务运行时执行）
python backend/backup.py backup

# 恢复（建议先停止服务）
python backend/backup.py restore backups/app_20260908_120000.db
```

建议部署后配置定时备份（cron / 平台计划任务）。

## 6. 尚未完成、需要你提供或确认的事项

- [ ] **托管平台账户**：我当前环境没有可用的云平台账户或部署权限，无法替你完成真实上线。请提供目标平台，或按第 3 节自行部署后告诉我。
- [ ] 多进程/高并发：当前锁单 worker；量大后需迁移 PostgreSQL。
- [ ] 数据库加密/备份加密：视合规要求决定。
