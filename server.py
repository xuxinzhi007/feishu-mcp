#!/usr/bin/env python3
"""飞书 MCP 服务（官方 mcp SDK + FastMCP，stdio）。

核心能力：**原地编辑已有块**，而不是只能“文末追加 / 整段删除”。
  - feishu_read_document  读块树（每个块都有稳定 block_id + path）
  - feishu_update_block   原地改一个块的内容/样式（block_id 与位置不变）
  - feishu_patch_block    段内查找替换（保留其余文字样式）
  - feishu_insert_blocks  在任意下标插入，支持嵌套 children
  - feishu_delete_block   按 block_id 删（任意层级）
  - feishu_replace_block  换块类型（同位置插入+删旧，block_id 会变）
  - feishu_edit_document  一次提交多步编辑，支持 dry_run

凭据优先级：环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET >
FEISHU_CONFIG 指向的 JSON 文件 > 脚本同目录 config.json > 当前目录 config.json。
"""

import json
import os
from pathlib import Path
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from feishu_client import FeishuClient


def load_config():
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return app_id, app_secret

    path = os.environ.get("FEISHU_CONFIG", "").strip()
    candidates = [p for p in (path,) if p]
    candidates += [
        str(Path(__file__).with_name("config.json")),
        str(Path.cwd() / "config.json"),
    ]
    for p in candidates:
        try:
            cfg = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(cfg, dict) and cfg.get("app_id") and cfg.get("app_secret"):
            return cfg["app_id"].strip(), cfg["app_secret"].strip()
    raise SystemExit(
        "缺少飞书应用凭据。请设置环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET，"
        "或提供包含 app_id / app_secret 的 config.json（可参考 config.example.json）。"
    )


APP_ID, APP_SECRET = load_config()
client = FeishuClient(APP_ID, APP_SECRET)

mcp = FastMCP("feishu-mcp")


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------


@mcp.tool()
def feishu_send_message(
    receive_id: str,
    content: str,
    receive_id_type: Literal["email", "open_id", "user_id", "chat_id"] = "email",
    msg_type: Literal["text", "post", "text_card", "image", "interactive"] = "text",
) -> dict:
    """以机器人身份发送飞书消息（私聊或群聊）。

    Args:
        receive_id: 接收方 ID，含义由 receive_id_type 决定（邮箱 / open_id / user_id / chat_id）。
        content: 消息内容。msg_type=text 时为纯文本；其他类型时为 JSON 字符串。
        receive_id_type: 接收方 ID 类型，默认 email。
        msg_type: 消息类型，默认 text。
    """
    return client.send_message(receive_id, content, receive_id_type, msg_type)


# ---------------------------------------------------------------------------
# 文档定位 / 读取
# ---------------------------------------------------------------------------


@mcp.tool()
def feishu_resolve_doc(ref: str) -> dict:
    """把 wiki 链接 / wiki 节点 token / docx 文档 ID 解析为文档 ID，返回类型与标题。"""
    return client.resolve_doc(ref)


@mcp.tool()
def feishu_read_document(ref: str, include_style: bool = False) -> dict:
    """读取文档标题与完整块树。**改文档前先调用它拿 block_id。**

    返回的 blocks 是树形结构，每个节点包含：
        block_id  稳定 ID（编辑时用它定位，不要用文字去猜）
        type      text / heading1-9 / bullet / ordered / code / quote / todo /
                  divider / callout / grid / table / table_cell / image ...
        index     在父块 children 里的下标（0 起，用于定点插入）
        path      位置路径，如 "3" 或 "3.1"
        text      纯文本
        children  子块（列表项缩进、高亮块内容、表格单元格内容都在这里）
        data      非文本块的结构化元数据：图片给 token/width/height/caption，
                  附件给 token/name，画板给 token（whiteboard_id），表格给 property 等

    同时返回 title_block_id —— 它就是文档标题块，可传给 feishu_update_block 改标题。
    include_style=True 时额外返回 style（对齐/折叠/代码语言/todo 勾选）与 spans（逐片内联样式）。

    图片只返回元数据（token、宽高、caption），不返回图片二进制：下载素材需要
    docs:document.media:download 权限，当前应用未开通。

    Args:
        ref: 文档引用（wiki 链接 / wiki token / docx 文档 ID 都行）。
        include_style: 是否需要样式细节。要“只改颜色/加粗/看代码语言”时置 True。
    """
    return client.read_document(ref, include_style=include_style)


# ---------------------------------------------------------------------------
# 原地编辑（核心：改已有段落用这两个，不要删了重写）
# ---------------------------------------------------------------------------


@mcp.tool()
def feishu_update_block(
    ref: str,
    block_id: str,
    text: Optional[str] = None,
    spans: Optional[list] = None,
    style: Optional[dict] = None,
    block_style: Optional[dict] = None,
) -> dict:
    """**原地修改一个已存在的块**：block_id 不变、在文档中的位置和顺序不变、子块不变。

    这是“改已有段落”的标准做法，不要用 delete + append 重建整篇。

    Args:
        ref: 文档引用。
        block_id: 要改的块（来自 feishu_read_document）。
        text: 新的纯文本。整段内容会被替换。
        spans: 分片富文本，用于同段内不同样式。给了这个就忽略 text。
            例：[{"text":"重要：" ,"bold":true},{"text":"普通说明"}]
            每片可选 bold / italic / underline / strikethrough / inline_code / link。
        style: 整段统一的内联样式，如 {"bold": true}、{"link": "https://..."}。
        block_style: 块级样式（不是字体）：
            align: left / center / right
            done: bool          —— todo 勾选状态
            folded: bool        —— 标题/列表折叠
            language: str|int   —— 仅 code 块，如 "python"
            wrap: bool          —— 仅 code 块，自动换行
            indentation_level: "none" / "one"
            background_color: 如 "LightGrayBackground"
            sequence: "auto" / "1" —— 仅 ordered 列表编号

    注意：block_type 不能通过本工具改（飞书没有该接口）。把普通段落改成标题请用
    feishu_replace_block。

    示例：
        改一段正文      feishu_update_block(ref, "doxcn...", text="新的说明")
        只改 todo 勾选  feishu_update_block(ref, "doxcn...", block_style={"done": true})
        代码块换语言    feishu_update_block(ref, "doxcn...", block_style={"language": "go"})
        改文档标题      feishu_update_block(ref, <title_block_id>, text="新标题")
    """
    return client.update_block(ref, block_id, text=text, spans=spans,
                               style=style, block_style=block_style)


@mcp.tool()
def feishu_patch_block(
    ref: str,
    block_id: str,
    find: str,
    replace: str,
    all: bool = False,
) -> dict:
    """在单个块内做“查找替换”，**保留其余文字的内联样式和整段结构**。

    用于只改一句话里的几个字：比 feishu_update_block(整段替换) 更不容易丢格式。
    跨 text_run 的匹配也能处理（例如把加粗片段里的词换掉）。

    Args:
        ref: 文档引用。
        block_id: 目标块。
        find: 要查找的原文本（精确匹配）。
        replace: 替换成的新文本；传空串等于删除这几个字。
        all: 为 True 时替换该块内全部命中，默认只替换第一处。

    返回 replaced 为实际替换次数；为 0 表示没找到，文档未改动。
    """
    return client.patch_block(ref, block_id, find, replace, all=all)


# ---------------------------------------------------------------------------
# 结构编辑
# ---------------------------------------------------------------------------


@mcp.tool()
def feishu_insert_blocks(
    ref: str,
    blocks: list,
    index: int = -1,
    parent_block_id: Optional[str] = None,
) -> dict:
    """在指定位置插入一批块（默认文档末尾）。

    与 append 的区别：可以插到任何下标，不会打乱前后结构。

    Args:
        ref: 文档引用。
        index: 插入到父块 children 的下标位置（0 起）；-1 表示末尾。
            例如现有 [A,B,C]，index=1 插在 A 与 B 之间。
        parent_block_id: 父块 ID。省略 = 文档顶层。要给某个列表项加子项、往高亮块里
            塞内容时传该父块的 block_id。
        blocks: 块列表。每块：
            {"type": "...", "text": "内容"}
            type: text / heading1..heading9 / bullet / ordered / quote / todo /
                  code / divider / table / callout
            可选：
              spans          分片富文本（同 feishu_update_block）
              style          整段内联样式
              language       仅 code 块，如 "python"
              done           仅 todo 块
              children       子块列表（递归同结构），会走“创建嵌套块”接口一次建好
              block_style    块级样式（align / folded / indentation_level /
                             background_color / wrap）

        示例：标题 + 正文 + 带两个子项的无序列表
        [
          {"type": "heading2", "text": "背景"},
          {"type": "text", "text": "普通段落。"},
          {"type": "bullet", "text": "父项", "children": [
              {"type": "bullet", "text": "子项一"},
              {"type": "bullet", "text": "子项二"}]}
        ]

        示例：2x2 表格
        [{"type": "table", "rows": [["表头A", "表头B"], ["值1", "值2"]], "header_row": true}]

        示例：高亮块
        [{"type": "callout", "text": "注意", "background_color": "lightyellow"}]

        示例：在文档最前面插一段
        feishu_insert_blocks(ref, [{"type": "text", "text": "开头"}], index=0)
    """
    return client.insert_blocks(ref, blocks, index=index, parent_block_id=parent_block_id)


@mcp.tool()
def feishu_append_document(ref: str, blocks: list) -> dict:
    """向文档**末尾**追加块（等价 feishu_insert_blocks(index=-1)）。

    只在真的要“往后接内容”时用。要改已有段落请用 feishu_update_block / feishu_patch_block；
    要插到中间请用 feishu_insert_blocks 指定 index。块格式同 feishu_insert_blocks。
    """
    return client.append_document(ref, blocks)


@mcp.tool()
def feishu_delete_block(ref: str, block_id: str) -> dict:
    """按 block_id 删除一个块（任意层级，含其所有子块）。

    删父块会连带删除子块；只想删列表里的某一项就用那一项的 block_id。
    """
    return client.delete_block(ref, block_id)


@mcp.tool()
def feishu_replace_block(ref: str, block_id: str, blocks: list) -> dict:
    """替换块的类型/内容，**位置不变**（block_id 会变）。

    飞书接口没有“改 block_type”，所以“普通段落 → 二级标题”只能：
    在原下标插入新块，再删除旧块。前后块的位置和顺序不受影响。

    Args:
        ref: 文档引用。
        block_id: 被替换的块。
        blocks: 新的块列表（格式同 feishu_insert_blocks），可以为多块；传空列表等于删除。
    """
    return client.replace_block(ref, block_id, blocks)


@mcp.tool()
def feishu_edit_document(ref: str, operations: list, dry_run: bool = False) -> dict:
    """一次提交多步编辑，按顺序执行（减少来回调用，适合整篇结构调整）。

    每个 operation 是 dict，op 取值：
        {"op": "update",  "block_id": "...", "text": "...", "block_style": {...}}
        {"op": "patch",   "block_id": "...", "find": "...", "replace": "..."}
        {"op": "insert",  "index": 1, "blocks": [...], "parent_block_id": "..."}
        {"op": "append",  "blocks": [...]}
        {"op": "replace", "block_id": "...", "blocks": [...]}
        {"op": "delete",  "block_id": "..."}
        {"op": "delete_range", "start_index": 0, "end_index": 2,
         "parent_block_id": "..."}   # 左闭右开；parent 省略=顶层

    dry_run=True 时只校验 block_id 是否存在、参数是否合法，不写入文档。建议大改动先 dry_run。

    失败会停在出错那一步，返回信息里带“已成功 N 步”，已完成的部分不会回滚
    ——所以把风险高的操作放前面，或者先用 dry_run 验一遍。
    """
    return client.edit_document(ref, operations, dry_run=dry_run)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
