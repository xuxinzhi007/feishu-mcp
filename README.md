# feishu-mcp

飞书机器人的 stdio MCP 服务，基于官方 [mcp SDK](https://github.com/modelcontextprotocol/python-sdk) 的 FastMCP 实现。把飞书开放平台能力（发消息、云文档读写、知识库节点解析）封装成 MCP 工具，供 Cursor、Claude Desktop、Qoder 或任意支持 MCP 的 agent 调用。

- 官方 SDK，协议帧格式、握手、错误处理均由 SDK 处理
- 依赖由 `uv` 管理（自动托管 Python 3.10+）
- 凭据通过环境变量或本地 `config.json` 提供，`config.json` 不提交

## 提供的工具

| 工具 | 说明 |
| --- | --- |
| `feishu_send_message` | 以机器人身份发消息（私聊/群聊，默认纯文本） |
| `feishu_resolve_doc` | 把 wiki 链接 / wiki token / docx 文档 ID 解析成文档 ID |
| `feishu_read_document` | 读取云文档标题与全部块内容 |
| `feishu_append_document` | 向文档末尾追加块（text / heading1-3 / bullet / ordered / quote / divider） |
| `feishu_delete_block` | 删除文档顶层的块 |

## 快速开始

```bash
# 1. 准备凭据（二选一）
cp config.example.json config.json   # 然后填入 app_id / app_secret
# 或者用环境变量：
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx

# 2. 本地自测（首次会自动下载 Python 并装依赖）
uv run --directory . python server.py
```

## 接入 MCP 客户端

Qoder（`~/.qoder/settings.json`）或 Cursor（`~/.cursor/mcp.json`）里加：

```json
{
  "mcpServers": {
    "feishu": {
      "command": "uv",
      "args": ["run", "--directory", "/绝对路径/feishu-mcp", "python", "server.py"],
      "env": {
        "FEISHU_APP_ID": "cli_xxx",
        "FEISHU_APP_SECRET": "xxx"
      }
    }
  }
}
```

`env` 里不放凭据时，服务会回退到 `config.json`。

## 配置优先级

1. 环境变量 `FEISHU_APP_ID` + `FEISHU_APP_SECRET`
2. 环境变量 `FEISHU_CONFIG` 指向的 JSON 文件
3. `server.py` 同目录 `config.json`
4. 当前工作目录 `config.json`

## 权限要求

应用需在飞书开放平台开通以下权限（scope）：

- `im:message:send_as_bot` —— 发消息
- `docx:document:write_only` —— 编辑文档（追加/删除块）
- `docx:document:readonly` —— 读取文档
- `wiki:node:read` —— 解析知识库节点

要操作的文档需把机器人添加为协作者（写作者）才能写入。

## 目录结构

```
feishu-mcp/
├── server.py           # MCP 服务（官方 SDK + FastMCP）
├── feishu_client.py    # 飞书 API 客户端（纯 stdlib，可单独 import 复用）
├── pyproject.toml      # 依赖声明（mcp>=1.0,<2）
├── config.example.json # 凭据模板
└── config.json         # 真实凭据（gitignore）
```
