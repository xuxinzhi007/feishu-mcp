# feishu-mcp

飞书机器人的 stdio MCP 服务，基于官方 [mcp SDK](https://github.com/modelcontextprotocol/python-sdk) 的 FastMCP 实现。把飞书开放平台能力（发消息、云文档读写、知识库节点解析）封装成 MCP 工具，供 Cursor、Claude Desktop、Qoder 或任意支持 MCP 的 agent 调用。

**重点：支持原地编辑已有段落。** 不是只能“文末追加 / 整段删除”——改一段话用 `feishu_update_block`，文字、`block_id`、在文档里的位置都不变，前后结构不受影响。

- 官方 SDK，协议帧格式、握手、错误处理均由 SDK 处理
- 依赖由 `uv` 管理（自动托管 Python 3.10+）
- 凭据通过环境变量或本地 `config.json` 提供，`config.json` 不提交
- 自带实机自测：`selftest.py`（45 条断言，跑完自动清理，原有块做快照比对）

## 提供的工具

| 工具 | 说明 |
| --- | --- |
| `feishu_send_message` | 以机器人身份发消息（私聊/群聊，默认纯文本） |
| `feishu_resolve_doc` | 把 wiki 链接 / wiki token / docx 文档 ID 解析成文档 ID |
| `feishu_read_document` | 读文档标题 + 完整块树（block_id、类型、位置、层级、可选样式） |
| `feishu_update_block` | **原地改已有块**的内容 / 内联样式 / 块级样式，位置和 ID 不变 |
| `feishu_patch_block` | 段内查找替换，保留该段其余文字的内联样式 |
| `feishu_insert_blocks` | 在任意下标插入，支持 `children` 嵌套（列表缩进、高亮块等） |
| `feishu_append_document` | 末尾追加（等价 `insert_blocks(index=-1)`，保留兼容） |
| `feishu_delete_block` | 按 block_id 删除（任意层级，含子块） |
| `feishu_replace_block` | 换块类型：同位置插入新块 + 删旧块（`block_id` 会变） |
| `feishu_edit_document` | 一次提交多步编辑，支持 `dry_run` 预检 |

## 怎么改已有段落

```text
1. feishu_read_document(ref)                     拿到 blocks[].block_id
2. feishu_update_block(ref, block_id, text="新内容")   原地覆盖这一段
```

只改一句话里的几个字、且想保住加粗/链接等格式：

```text
feishu_patch_block(ref, block_id, find="旧词", replace="新词")
```

要插到中间而不是末尾：

```text
feishu_insert_blocks(ref, blocks, index=3)       插到第 3 个块之前
```

给某个列表项加子项：

```text
feishu_insert_blocks(ref, blocks, parent_block_id=<列表项的 block_id>)
```

整篇结构调整（先预演）：

```text
feishu_edit_document(ref, operations, dry_run=True)
feishu_edit_document(ref, operations)
```

### 块定义格式（`feishu_insert_blocks` / `feishu_replace_block` / `append`）

```json
{"type": "heading2", "text": "背景"}
{"type": "text", "text": "普通段落"}
{"type": "text", "spans": [{"text": "加粗", "bold": true}, {"text": "普通"}]}
{"type": "bullet", "text": "父项", "children": [
  {"type": "bullet", "text": "子项一"},
  {"type": "bullet", "text": "子项二"}]}
{"type": "todo", "text": "待办", "done": false}
{"type": "code", "text": "print(1)", "language": "python"}
{"type": "ordered", "text": "第一条"}
{"type": "quote", "text": "引用"}
{"type": "divider"}
{"type": "table", "rows": [["表头A", "表头B"], ["值1", "值2"]], "header_row": true}
{"type": "callout", "text": "高亮内容", "background_color": "lightyellow"}
```

`type` 支持：`text` / `heading1`~`heading9` / `bullet` / `ordered` / `quote` / `todo` / `code` / `divider` / `table` / `callout`。

表格也可以直接写 `children`（`table_cell` 列表）。高亮块、分栏列必须至少有一个子块，`callout` 传 `text` 时会自动补一个文本子块。

## 能力边界（接口没有的，不是没实现）

| 需求 | 结论 |
| --- | --- |
| 改已有段落的文字/样式 | ✅ `feishu_update_block`，ID 与位置不变 |
| 段内局部替换、保格式 | ✅ `feishu_patch_block` |
| 插入到任意位置 | ✅ `feishu_insert_blocks(index=...)` |
| 嵌套列表 / 高亮块 / 分栏 | ✅ `children` + 创建嵌套块接口 |
| 改 todo 勾选、代码语言、折叠、对齐 | ✅ `block_style` |
| 改文档标题 | ✅ 对 `title_block_id` 调 `feishu_update_block` |
| **改 block_type**（正文↔标题） | ❌ 飞书无此接口。用 `feishu_replace_block`：同位置插新删旧，顺序不乱但 `block_id` 会变 |
| 删除中间某个块 | ✅ `feishu_delete_block`（按父块+下标，不是按 ID 硬删） |
| 表格创建 / 单元格文字 | ✅ `{"type":"table","rows":[...]}`；单元格文字用嵌套 text 块的 `block_id` 原地改 |
| 高亮块 / 分栏 | ✅ `{"type":"callout","children":[...]}`，分栏列需自带子块 |
| 图片 / 电子表格 / 多维表格创建 | ❌ 未封装（需要素材上传或子表初始化） |

## 快速开始

```bash
# 1. 准备凭据（二选一）
cp config.example.json config.json   # 然后填入 app_id / app_secret
# 或者用环境变量：
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx

# 2. 本地自测（首次会自动下载 Python 并装依赖）
uv run --directory . python server.py

# 3. 实机自测（会在文档末尾建临时块，跑完自动删）
uv run --directory . python selftest.py --doc "https://xxx.feishu.cn/wiki/xxxx"
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
- `docx:document:write_only` —— 编辑文档（创建/更新/删除块）
- `docx:document:readonly` —— 读取文档
- `wiki:node:read` —— 解析知识库节点

要操作的文档需把机器人添加为协作者（写作者）才能写入。

## 限频与健壮性

飞书对单应用 3 QPS、单文档并发 3 QPS 限频（错误码 99991400 / HTTP 429）。客户端已内置：

- 写请求最小间隔，避免自己打满 3 QPS
- 命中限频时指数退避重试（最多 5 次）
- 读请求网络异常自动重试
- **写请求网络中断不自动重试**（结果未知，重试可能建出重复块），直接报错并提示先读文档确认

## 目录结构

```
feishu-mcp/
├── server.py           # MCP 服务（官方 SDK + FastMCP）
├── feishu_client.py    # 飞书 API 客户端（纯 stdlib，可单独 import 复用）
├── selftest.py         # 实机自测（45 条断言）
├── mcp_smoke.py        # MCP 协议层冒烟测试（起 stdio server 调工具）
├── pyproject.toml      # 依赖声明（mcp>=1.0,<2）
├── config.example.json # 凭据模板
└── config.json         # 真实凭据（gitignore）
```
