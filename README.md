# ZJAI-Agent_MCPversion

基于 Flask 的轻量 AI Agent，支持 MCP 工具调用、多模型 API、多用户登录与 Markdown 技能。
全部数据以 JSON 落盘，无需数据库。

## 特性

- **MCP 工具调用**：支持 Streamable HTTP 与 SSE 两种协议，多服务并存
- **工具命名空间**：多 MCP 服务下的同名工具自动加 `服务ID__工具名` 前缀，互不冲突
- **多模型**：任意 OpenAI 兼容接口（Ollama、vLLM、DeepSeek、OpenAI 等）
- **流式输出**：SSE 逐字返回，支持工具调用的过程展示
- **多用户**：账号体系 + 管理员控制台
- **双皮肤**：`terminal`（终端黑绿，默认）与 `aurora`（极简深色）
- **安装向导**：首次访问引导完成配置，无需手改 JSON

## 环境要求

- Python **3.8+**（已在 3.8.20 与 3.13.5 验证）
- 依赖见 `requirements.txt`

## 快速开始

### Windows

双击 `run.bat`，它会自动安装依赖并启动。

### Linux / macOS

```bash
python3 -m pip install -r requirements.txt
python3 run.py
```

启动后访问 <http://127.0.0.1:5000>，会进入安装向导。

### 使用已有 Python 之外的虚拟环境（推荐）

```bash
python3 -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## 首次初始化

向导共 4 步：

1. 欢迎页
2. 设置管理员账号（密码至少 6 位）
3. 配置模型 API（至少一个）
4. 站点名称、界面皮肤、默认欢迎语，可选填 MCP 服务

完成后写入 `data/install.lock`，再次访问不会重复初始化。

**重新初始化**：停止服务，删除 `data/` 目录即可（会清空所有会话与账号）。

## 配置说明

运行时配置在 `config/`，数据在 `data/`，两者都不纳入版本控制。

| 文件 | 内容 |
|---|---|
| `config/models.json` | 模型 API 列表 |
| `config/mcp.json` | MCP 服务列表 |
| `config/app.json` | 站点名称、皮肤、欢迎语 |
| `data/users.json` | 用户账号 |
| `data/sessions.json` | 会话列表 |
| `data/messages.json` | 消息记录 |

`config/*.example.json` 是格式参考。

### 切换皮肤

三种方式任选：

1. 设置中心 → 个性化设置 → 界面皮肤
2. 安装向导第 4 步
3. 直接改 `config/app.json`：

```json
{
  "name": "ZJAI-Agent",
  "skin": "aurora",
  "theme": "aurora",
  "welcome_message": "你好",
  "initialized": true
}
```

`skin` 与 `theme` 互为别名，取值为 `terminal` 或 `aurora`。

### 预置配置（跳过向导填写）

可以先把 `config/models.json` 和 `config/mcp.json` 写好再启动，向导里对应的步骤
（第 3 步模型 API、第 4 步 MCP 地址）**留空即可跳过**，已有配置不会被覆盖。

适合批量部署：提前铺好配置文件，安装时只创建管理员账号。

```bash
mkdir -p config
cp config/models.example.json config/models.json
cp config/mcp.example.json config/mcp.json
# 按需编辑两个文件，填入真实地址与密钥
python run.py
```

只需满足至少一条有效模型（`base_url` 与 `model` 均非空）。配置文件里的模型会
直接出现在「设置中心 → 模型 API」中。

> 配置文件在每次请求时实时读取，改完无需重启。

### 配置 MCP 服务

在「设置中心 → MCP 服务」中填写，或直接编辑 `config/mcp.json`：

```json
{
  "servers": [
    {
      "id": "filesystem",
      "name": "文件系统",
      "protocol": "streamable_http",
      "url": "http://127.0.0.1:9000/mcp",
      "enabled": true,
      "oauth_enabled": false,
      "verify_ssl": true,
      "headers": {}
    }
  ]
}
```

- `protocol`：`streamable_http`（默认）或 `sse`
- `headers`：需要鉴权时填 `{"Authorization": "Bearer <token>"}`
- `id` 会作为工具名前缀。若 id 含 `__`，会在展示时替换为 `_` 避免歧义

保存后缓存自动失效，无需重启。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ZJAI_SECRET_KEY` | `change-this-secret-key` | Flask session 密钥，**生产环境必须修改** |
| `ZJAI_MAX_BODY_MB` | `32` | 请求体上限（MB） |

## 目录结构

```
app/
  __init__.py          # 路由与页面
  config.py            # 路径与密钥
  services/
    auth_service.py    # 账号
    session_service.py # 会话与消息
    model_service.py   # 模型调用、工具解析、流式
    mcp_service.py     # MCP 协议、session 与工具缓存
    json_store.py      # JSON 读写（带文件锁）
    skill_service.py   # 技能加载
templates/             # Jinja 模板
static/                # 样式与前端脚本
skills/                # Markdown 技能，放 *.md 即自动加载
config/                # 运行时配置
data/                  # 运行时数据
tests/                 # pytest 测试
```

## 技能（Skill）

在 `skills/` 放任意 `.md` 文件即被自动加载，文件名去掉扩展名作为技能名。

## 运行测试

```bash
python -m pytest tests/ -q
```

测试使用临时目录，不会污染仓库的 `data/` 与 `config/`。

## 生产部署

内置服务器仅供开发使用。生产环境建议前置 WSGI 服务器：

```bash
pip install waitress
waitress-serve --host=0.0.0.0 --port=5000 run:app
```

并务必设置 `ZJAI_SECRET_KEY`：

```bash
export ZJAI_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

## 已知限制

- 消息以单个 JSON 文件存储，每条新消息都会整体重写 `messages.json`。会话很多时会变慢，适合个人或小团队使用
- JSON 文件锁为进程内锁，多进程部署时请勿让多个实例共用同一 `data/` 目录
