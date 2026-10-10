#!/usr/bin/env python3
"""飞书 MCP 服务（官方 mcp SDK + FastMCP，stdio）。

凭据优先级：环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET >
FEISHU_CONFIG 指向的 JSON 文件 > 脚本同目录 config.json > 当前目录 config.json。
"""

import json
import os
from pathlib import Path
from typing import Literal

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


@mcp.tool()
def feishu_resolve_doc(ref: str) -> dict:
    """把 wiki 链接 / wiki 节点 token / docx 文档 ID 解析为文档 ID，返回文档类型与标题。"""
    return client.resolve_doc(ref)


@mcp.tool()
def feishu_read_document(ref: str) -> dict:
    """读取飞书云文档的标题与全部块内容（纯文本）。"""
    return client.read_document(ref)


@mcp.tool()
def feishu_append_document(ref: str, blocks: list) -> dict:
    """向飞书云文档末尾追加块。

    Args:
        ref: 文档引用（wiki 链接 / wiki token / docx 文档 ID）。
        blocks: 要追加的块列表，每个块形如 {"type": "text", "text": "内容"}；
            type 支持 text / heading1 / heading2 / heading3 / bullet / ordered / quote / divider。
    """
    return client.append_document(ref, blocks)


@mcp.tool()
def feishu_delete_block(ref: str, block_id: str) -> dict:
    """删除飞书云文档顶层的某个块（仅支持根节点直接子块）。

    Args:
        ref: 文档引用（wiki 链接 / wiki token / docx 文档 ID）。
        block_id: 要删除的块 ID，可从 feishu_read_document 结果中获取。
    """
    return client.delete_block(ref, block_id)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
