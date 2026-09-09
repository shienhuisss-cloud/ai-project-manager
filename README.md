# AI 项目需求管家

一个中文网页工具，帮助接网站 / 小程序项目的工作室和独立开发者，把零散的客户沟通整理成**有原文证据的需求清单**，经人工确认形成**不可覆盖的版本**，再把后续沟通与旧版本比较，生成**可逐条审核的变更建议**。

## 它解决什么问题

接单时，客户需求往往散落在多段聊天记录里——一会儿说"做一个官网"，一会儿说"先不要会员"，过几天又反悔。靠人肉整理容易出现两个问题：**记漏需求**，以及**事后说不清"当初到底约定了什么"**。

这个工具把流程固化下来：

1. 粘贴沟通原文，保留原始材料；
2. 用大模型提取需求候选，每一条都**引用原文出处**；
3. 人工编辑、确认后生成**版本快照**（确认后不可修改）；
4. 输入新沟通，与已确认版本比较，生成**变更建议**（新增/修改/删除/澄清/未变）；
5. 逐条采纳或忽略，形成下一版本；
6. 随时导出 Markdown 需求文档。

核心原则：**AI 的输出永远是草稿，只有经过人工确认才成为正式版本**。这避免了模型幻觉直接污染需求文档。

## 技术选型

| 部分 | 选择 | 理由 |
| --- | --- | --- |
| 后端 | Python + FastAPI | 轻量、类型友好，适合快速迭代 |
| 前端 | 原生 HTML / CSS / JS | 单页工具不需要重框架，零构建步骤 |
| 数据库 | SQLite | 单人/小团队本地使用，零部署成本 |
| 模型 | OpenAI 兼容接口 | 可接 DeepSeek / Kimi / GLM / 通义等任意服务商 |
| 测试 | pytest | 覆盖版本规则、权限隔离、校验逻辑 |

## 功能一览

- 用户注册 / 登录，PBKDF2 密码哈希 + 服务端会话令牌
- 项目级访问控制：项目 owner 与成员（member）权限隔离
- 需求提取与变更比较（真实模型，结构化输出 + 后端校验）
- 证据可追溯：每条需求/变更都带材料 ID 和原文引用，后端校验引文真实存在
- 版本不可覆盖：确认后生成不可变快照，保留父版本链
- 请求幂等：重复提交不会产生重复版本
- 输入上限、请求限额
- Markdown 导出
- 数据备份 / 恢复脚本

## 快速开始

### 环境要求

- Python 3.10+

### 安装与运行

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开浏览器访问 http://127.0.0.1:8000/ ，首次使用先注册一个账号。

### 配置模型密钥（可选，但推荐）

真实分析需要配置一个模型服务。复制 `.env.example` 为 `.env`，填入：

```ini
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://api.deepseek.com/v1   # 或其它服务商
LLM_MODEL=deepseek-chat
```

不配置也能运行：内置示例材料会走演示模式，其它输入会明确提示"尚未配置模型"，不会伪造结果。

支持的常见服务商：

| 服务商 | LLM_BASE_URL | LLM_MODEL |
| --- | --- | --- |
| DeepSeek | https://api.deepseek.com/v1 | deepseek-chat |
| Moonshot(Kimi) | https://api.moonshot.cn/v1 | moonshot-v1-8k |
| 智谱 GLM | https://open.bigmodel.cn/api/paas/v4 | glm-4-flash |
| 通义千问 | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest
```

## 项目结构

```
backend/
  main.py          # FastAPI 应用与路由
  auth.py          # 密码哈希与令牌
  llm.py           # 模型调用客户端（OpenAI 兼容）
  prompts.py       # 提取 / 比较两份提示词
  validators.py    # 结构化校验 + 证据归属检查
  backup.py        # 数据备份 / 恢复
frontend/
  index.html / app.js / style.css   # 单页前端
tests/             # pytest 测试
```

## 已知限制与待改进

- **数据库**：SQLite 单文件，适合单人/小团队本地使用。多用户高并发需迁移到 PostgreSQL。
- **部署**：目前是本地运行。要面向真实客户，还需部署到有持久磁盘的托管环境（详见 `DEPLOY.md`）。
- **模型效果**：提取与比较依赖所选模型的理解能力，结构化校验只能保证格式和证据合法，不能保证语义完全正确——所以保留了人工确认环节。
- **前端**：原生 JS，无状态管理框架；功能增多后可能需要重构。
