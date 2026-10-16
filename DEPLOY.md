# 部署与升级

当前默认用于单机或小范围试用，尚无公开在线地址。以下文件已准备好部署步骤，运行命令不等于已经发布。

## Docker 部署

复制 `.env.example` 为 `.env`，由部署管理员填写模型服务配置。默认正式业务模式，智能整理与变更比较通过后台调用所配置的模型服务。使用带 Compose 的 Docker：

```bash
docker compose up --build -d
```

服务绑定本机 8000 端口，数据库与备份保存在 `app-data` 持久卷。容器使用非 root 用户；需要开放公网时在前面配置 Caddy/Nginx HTTPS，反向代理到 127.0.0.1:8000。不要删除持久卷。

单独运行镜像：

```bash
docker build -t ai-project-manager .
docker run -d --name ai-project-manager --env-file .env -p 127.0.0.1:8000:8000 -v ai-requirements-data:/data ai-project-manager
```

Docker 配置尚需在实际 Docker 主机验证。本地回归使用 Python + Uvicorn，不等于容器运行验证。

## Python 部署

推荐 Python 3.12，建立虚拟环境并安装 `requirements-lock.txt`，由系统服务管理：

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

使用独立请求连接和事务，但 SQLite 仍会串行写入。当前建议单实例、单 worker；多实例和高并发应先迁移到服务型数据库。不要把有数据的磁盘目录整体挂载到代码目录；将 `DB_PATH` 指向专门的数据卷，例如 `/data/app.db`。

## 配置

- 所有配置支持根目录 `.env` 和环境变量，环境变量优先。
- `DB_PATH`：持久磁盘路径；`BACKUP_DIR`：备份目录。
- `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`：模型服务的 API 密钥、兼容接口地址与模型标识，均为智能分析的必要配置。
- `LLM_RESPONSE_FORMAT`：默认 prompt；支持 JSON Schema 的服务可显式设置 json_schema。
- `LLM_INPUT_PRICE_PER_MILLION / LLM_OUTPUT_PRICE_PER_MILLION / LLM_CURRENCY`：自行按实际定价填写；不内置易过期的价格。
- `RATE_LIMIT_MAX`：每用户每分钟分析请求；包含成员与失败调用。
- `AUTH_RATE_LIMIT_MAX`：每来源 IP 每分钟注册/登录次数。
- `MAX_BODY_BYTES`：请求体字节上限，默认 2 MB。反向代理也应设相应上限。
- `CORS_ORIGINS`：默认空、同域使用；跨域时填写明确来源，不使用通配符。
- `ENABLE_DEMO_MODE`：默认 false；只在独立培训环境显式设为 true。培训入口为 `/?training=1`，不展示在正式登录页上。不要在正式业务环境启用固定示例替代分析。

### 首次接入智能服务

配置文件放在项目根目录，与 `.env.example` 同级，文件名必须是 `.env`，不能是 Windows 记事本自动追加扩展名后的 `.env.txt`。可在资源管理器中开启“文件扩展名”核对。

1. 将 `.env.example` 复制为 `.env`；已有配置时直接编辑，不要覆盖。
2. 填写 `LLM_API_KEY`（API 密钥，不是网站登录密码）、`LLM_BASE_URL` 和 `LLM_MODEL`。
3. 保存文件后重启 Uvicorn 进程；Compose 部署执行 `docker compose up -d --force-recreate`，让容器重新加载环境变量。
4. 注册自己的账号，新建项目，用脱敏材料测试智能整理并人工核查原文引用。

### 接入验收与运维

`/api/health` 用于检查应用启动状态。模型接入验收应另外使用一条脱敏材料执行智能整理，检查需求草稿及逐字引文，再测试新沟通与正式版本的变更比较。

排查调用问题时依次核对配置文件名、三个必填参数、进程是否重新加载配置、服务商密钥权限与额度、网络连接，以及输出校验结果。材料归档与分析分开保存，便于重试和人工核查。不要把密钥、服务商完整错误响应或客户原文放进公开日志与 issue。

`.env`、本地配置变体、数据库和备份不会随源码提交；不要把密钥写入 `.env.example`。公网部署通过平台的环境变量或密钥管理功能注入配置，升级代码时保留现有配置与数据。

普通业务页面不展示模型名称、密钥配置、额度或 token 计量；服务接入由管理员在部署配置中维护。健康接口仅返回应用可用性与版本，不返回模型配置。

代理设置应只信任真实反向代理传来的来源 IP，避免伪造转发头绕过认证限流。TLS、持久磁盘和实际模型额度由部署环境提供。

## 账号与业务数据

系统不提供默认管理员、默认密码或预置业务账号。首次访问登录页点击“创建工作空间账号”，设置用户名与至少 6 位密码；成员协作时，其他成员先注册，再由项目所有者添加。

账号、会话和项目记录存储在部署实例的数据库中，不会上传到 GitHub，也不会因拉取源码自动出现在其他机器。更换实例需迁移数据库；目前没有找回密码、企业 SSO 或成员移除功能。

## 备份、迁移与回退

第一次用新代码启动前，先用旧版本的备份脚本备份旧库，再停止旧服务。新代码启动时自动检查 `PRAGMA user_version`，升级在事务中完成。旧版本数据及其快照保留，旧的无权限域幂等记录归档为 `idempotency_legacy`。存在孤立关联记录时迁移会报错并回滚，先修复数据后再启动。

```bash
python backend/backup.py backup
# 恢复前停止服务：
python backend/backup.py restore backups/你的备份.db
```

脚本读取同一套 `DB_PATH` 配置。默认备份目录为数据库目录中的 `backups/`。备份使用 SQLite online backup，包含 WAL 中已提交的数据；恢复会先校验备份完整性，并给当前库生成带时间戳的安全备份。

容器中备份：

```bash
docker compose exec app python backend/backup.py backup
```

不要把还原操作作为健康检查。回退旧代码时应同时恢复升级前备份，避免旧代码误用新的表结构。备份仍需另存到不同设备或服务，按真实业务需求制定保留与恢复演练计划。
