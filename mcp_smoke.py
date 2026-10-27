#!/usr/bin/env python3
"""MCP 协议层冒烟测试：真的用 stdio 起 server.py，走 MCP 握手 + 调工具。

覆盖：握手、工具清单、读取、创建、原地更新、段内替换、删除、错误返回。
写操作只碰一个临时标题块，跑完删掉；退出码 0 = 全过。

用法：
    uv run --directory . python mcp_smoke.py --doc "https://xxx.feishu.cn/wiki/xxxx"
"""

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(name)


def payload(result):
    return json.loads(result.content[0].text)


async def main(doc_ref):
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(HERE, "server.py")],
        cwd=HERE,
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            check("MCP 握手成功", init.serverInfo.name == "feishu-mcp", init.serverInfo)

            tools = (await session.list_tools()).tools
            names = {t.name for t in tools}
            check("工具清单完整（10 个）", len(names) == 10, sorted(names))
            check("包含 feishu_update_block", "feishu_update_block" in names)

            res = await session.call_tool("feishu_read_document", {"ref": doc_ref})
            check("read_document 可调用", not res.isError, res.content[0].text[:200])
            before = payload(res)

            created = None
            try:
                res = await session.call_tool("feishu_insert_blocks", {
                    "ref": doc_ref,
                    "blocks": [{"type": "heading2", "text": "__MCP_SMOKE__"}]})
                check("insert_blocks 可调用", not res.isError, res.content[0].text[:200])
                created = payload(res)["created"][0]["block_id"]

                res = await session.call_tool("feishu_update_block", {
                    "ref": doc_ref, "block_id": created, "text": "__MCP_SMOKE_2__"})
                check("update_block 原地改成功", not res.isError and
                      payload(res)["updated"]["block_id"] == created,
                      res.content[0].text[:200])

                res = await session.call_tool("feishu_patch_block", {
                    "ref": doc_ref, "block_id": created,
                    "find": "SMOKE_2", "replace": "SMOKE_3"})
                check("patch_block 段内替换成功",
                      not res.isError and payload(res)["replaced"] == 1,
                      res.content[0].text[:200])

                res = await session.call_tool("feishu_read_document", {"ref": doc_ref})
                node = next(b for b in payload(res)["blocks"] if b["block_id"] == created)
                check("改动对得上", node["text"] == "__MCP_SMOKE_3__", node)

                res = await session.call_tool("feishu_update_block", {
                    "ref": doc_ref, "block_id": "doxcn_not_exist", "text": "x"})
                check("非法 block_id 返回结构化错误",
                      res.isError and "block_id" in res.content[0].text,
                      res.content[0].text[:200])
            finally:
                if created:
                    await session.call_tool("feishu_delete_block", {
                        "ref": doc_ref, "block_id": created})

            res = await session.call_tool("feishu_read_document", {"ref": doc_ref})
            after = payload(res)
            check("临时块已清理干净",
                  [b["block_id"] for b in after["blocks"]] == [b["block_id"] for b in before["blocks"]])

    print("\n合计：%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=os.environ.get("FEISHU_TEST_DOC", ""))
    args = ap.parse_args()
    if not args.doc:
        print("缺少 --doc / FEISHU_TEST_DOC", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(args.doc)))
