#!/usr/bin/env python3
"""feishu-mcp 实机自测：在真实文档上验证「原地改、定点插、按 id 删」都没问题。

用法：
    uv run --directory . python selftest.py --doc "https://xxx.feishu.cn/wiki/xxxx"
    # 或
    FEISHU_TEST_DOC="https://..." uv run --directory . python selftest.py

自测会在文档末尾建一块临时内容，跑完自动删除；已有块在测试前后做快照比对，
一旦被动过就判失败。退出码 0 = 全过。
"""

import argparse
import json
import os
import sys

from feishu_client import FeishuClient, FeishuError, block_text

PASS, FAIL = "PASS", "FAIL"


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, cond, detail=""):
        self.rows.append((PASS if cond else FAIL, name, detail))
        return bool(cond)

    def eq(self, name, got, want):
        return self.check(name, got == want, "got=%r want=%r" % (got, want))

    def section(self, title):
        self.rows.append(("", "-- %s --" % title, ""))

    def dump(self):
        for state, name, detail in self.rows:
            if not state:
                print(name)
            elif state == PASS:
                print("  %s  %s" % (state, name))
            else:
                print("  %s  %s  %s" % (state, name, detail))
        failed = sum(1 for s, _, _ in self.rows if s == FAIL)
        passed = sum(1 for s, _, _ in self.rows if s == PASS)
        print("\n合计：%d passed, %d failed" % (passed, failed))
        return failed


def top_blocks(client, doc_id):
    return client._children_of(doc_id, doc_id)


def index_of(blocks, block_id):
    for i, b in enumerate(blocks):
        if b["block_id"] == block_id:
            return i
    return -1


def run(client, ref, report):
    doc_id = client.resolve_doc(ref)["document_id"]
    before = client.read_document(ref, include_style=True)
    before_top = [(b["block_id"], b["text"], b["type"]) for b in before["blocks"]]
    def track(res):
        return res

    try:
        # ---------------------------------------------------------------- 读
        report.section("读取")
        report.check("文档可读，拿到标题", bool(before.get("title")), repr(before.get("title")))
        report.check("块树带 block_id/type/index/path",
                     all(k in before["blocks"][0] for k in ("block_id", "type", "index", "path"))
                     if before["blocks"] else True)
        report.check("可拿到标题块 ID（可直接改标题）",
                     bool(before.get("title_block_id")), str(before.get("title_block_id")))

        # ------------------------------------------------------------ 末尾插入
        report.section("插入")
        marker = track(client.insert_blocks(
            ref, [{"type": "heading2", "text": "__SELFTEST__"}]))["created"][0]["block_id"]
        blocks = top_blocks(client, doc_id)
        marker_idx = index_of(blocks, marker)
        report.check("末尾插入成功", marker_idx >= 0)

        group = track(client.insert_blocks(ref, [
            {"type": "text", "text": "原始段落 A"},
            {"type": "text", "spans": [{"text": "加粗片段", "bold": True},
                                       {"text": " 普通片段"}]},
            {"type": "todo", "text": "待办项", "done": False},
            {"type": "code", "text": "print(1)", "language": "python"},
            {"type": "bullet", "text": "父项", "children": [
                {"type": "bullet", "text": "子一"},
                {"type": "bullet", "text": "子二"}]},
        ], index=marker_idx + 1))
        p_a, p_spans, p_todo, p_code, p_bullet = [c["block_id"] for c in group["created"]]
        blocks = top_blocks(client, doc_id)
        order = [index_of(blocks, x) for x in (marker, p_a, p_spans, p_todo, p_code, p_bullet)]
        report.check("批量插入保持顺序", order == sorted(order) and -1 not in order, str(order))

        tree = client.read_document(ref)
        bullet_node = next(b for b in tree["blocks"] if b["block_id"] == p_bullet)
        kids = [k["text"] for k in bullet_node.get("children") or []]
        report.eq("嵌套子块一次建成", kids, ["子一", "子二"])

        # --------------------------------------------------------- 原地修改
        report.section("原地修改已有段落（核心）")
        before_update = client.read_document(ref)
        node_before = next(b for b in before_update["blocks"] if b["block_id"] == p_a)
        res = client.update_block(ref, p_a, text="改后的段落 A")
        report.eq("update 返回同一个 block_id", res["updated"]["block_id"], p_a)

        after_update = client.read_document(ref)
        node_after = next(b for b in after_update["blocks"] if b["block_id"] == p_a)
        report.eq("文本已更新", node_after["text"], "改后的段落 A")
        report.eq("位置 index 不变", node_after["index"], node_before["index"])
        report.eq("block_id 不变", node_after["block_id"], p_a)
        neighbors_b = [b["block_id"] for b in after_update["blocks"]]
        report.check("相邻块未被打乱", neighbors_b == [b["block_id"] for b in before_update["blocks"]])

        # 只改块级样式，不动文字
        client.update_block(ref, p_todo, block_style={"done": True})
        styled = client.read_document(ref, include_style=True)
        todo_node = next(b for b in styled["blocks"] if b["block_id"] == p_todo)
        report.eq("todo 勾选状态改成功", (todo_node.get("style") or {}).get("done"), True)
        report.eq("只改样式时文字不动", todo_node["text"], "待办项")

        client.update_block(ref, p_code, block_style={"language": "go", "wrap": True})
        styled = client.read_document(ref, include_style=True)
        code_node = next(b for b in styled["blocks"] if b["block_id"] == p_code)
        report.eq("代码块语言改成功", (code_node.get("style") or {}).get("language"), 22)

        # 改带子块的列表项：子块不能被丢
        client.update_block(ref, p_bullet, text="父项（改）")
        tree = client.read_document(ref)
        bn = next(b for b in tree["blocks"] if b["block_id"] == p_bullet)
        report.eq("改列表项文字后子块仍在",
                  [k["text"] for k in bn.get("children") or []], ["子一", "子二"])
        report.eq("列表项文字已改", bn["text"], "父项（改）")

        # 改文档标题（page 块）后还原
        orig_title = before["title"]
        try:
            client.update_block(ref, before["title_block_id"], text="__SELFTEST_TITLE__")
            got_title = client.read_document(ref)["title"]
        finally:
            client.update_block(ref, before["title_block_id"], text=orig_title)
        report.eq("文档标题可原地改", got_title, "__SELFTEST_TITLE__")
        report.eq("文档标题已还原", client.read_document(ref)["title"], orig_title)

        # ----------------------------------------------------------- 段内替换
        report.section("段内查找替换（保样式）")
        res = client.patch_block(ref, p_spans, "加粗片段", "已替换片段")
        report.eq("替换命中一次", res.get("replaced"), 1)
        styled = client.read_document(ref, include_style=True)
        spans_node = next(b for b in styled["blocks"] if b["block_id"] == p_spans)
        report.eq("替换后整段文本正确", spans_node["text"], "已替换片段 普通片段")
        report.eq("另一片段样式保留", (spans_node["spans"][1].get("bold") is None), True)
        report.eq("被替换片段样式保留", spans_node["spans"][0].get("bold"), True)

        # 跨 text_run 的替换（匹配区间跨越两个不同样式的片段）
        r = track(client.insert_blocks(ref, [{
            "type": "text",
            "spans": [{"text": "ABC", "bold": True}, {"text": "DEF"}],
        }]))
        p_cross = r["created"][0]["block_id"]
        client.patch_block(ref, p_cross, "CDE", "X")
        styled = client.read_document(ref, include_style=True)
        cross = next(b for b in styled["blocks"] if b["block_id"] == p_cross)
        report.eq("跨片段替换结果正确", cross["text"], "ABXF")
        report.eq("跨片段替换后样式按 run 保留",
                  [(s["text"], bool(s.get("bold"))) for s in cross["spans"]],
                  [("ABX", True), ("F", False)])

        # 分割线
        r = track(client.insert_blocks(ref, [{"type": "divider"}]))
        div = r["created"][0]["block_id"]
        node = next(b for b in client.read_document(ref)["blocks"] if b["block_id"] == div)
        report.eq("分割线插入成功", node["type"], "divider")

        # 表格：一次建好 2x2，单元格文字可原地改
        r = track(client.insert_blocks(ref, [{
            "type": "table",
            "rows": [["表头A", "表头B"], ["值1", "值2"]],
            "header_row": True,
        }]))
        table_id = r["created"][0]["block_id"]
        tree = client.read_document(ref)
        tnode = next(b for b in tree["blocks"] if b["block_id"] == table_id)
        report.eq("表格建出 4 个单元格", len(tnode.get("children") or []), 4)
        cell_text_block = tnode["children"][0]["children"][0]["block_id"]
        client.update_block(ref, cell_text_block, text="表头A（改）")
        tree = client.read_document(ref)
        tnode = next(b for b in tree["blocks"] if b["block_id"] == table_id)
        report.eq("单元格文字可原地改",
                  tnode["children"][0]["children"][0]["text"], "表头A（改）")

        # 高亮块
        r = track(client.insert_blocks(ref, [{
            "type": "callout", "text": "高亮内容", "background_color": "lightyellow"}]))
        callout_id = r["created"][0]["block_id"]
        tree = client.read_document(ref)
        cnode = next(b for b in tree["blocks"] if b["block_id"] == callout_id)
        report.eq("高亮块带子文本", cnode["children"][0]["text"], "高亮内容")

        # ------------------------------------------------------------ 定点插入
        report.section("定点插入")
        blocks = top_blocks(client, doc_id)
        idx_p_a = index_of(blocks, p_a)
        track(client.insert_blocks(ref, [{"type": "text", "text": "插在 A 前面"}], index=idx_p_a))
        blocks = top_blocks(client, doc_id)
        new_idx = index_of(blocks, p_a)
        report.eq("A 下移一位", new_idx, idx_p_a + 1)
        report.eq("新块落在 A 前一位", block_text(blocks[idx_p_a]), "插在 A 前面")

        # --------------------------------------------------------- 换块类型
        report.section("换块类型（同位置）")
        blocks = top_blocks(client, doc_id)
        idx = index_of(blocks, p_a)
        res = client.replace_block(ref, p_a, [{"type": "heading3", "text": "A 变成标题"}])
        new_p_a = res["created"][0]["block_id"]
        blocks = top_blocks(client, doc_id)
        report.eq("新块占用原下标", index_of(blocks, new_p_a), idx)
        report.eq("旧块已删", index_of(blocks, p_a), -1)
        node = next(b for b in client.read_document(ref)["blocks"] if b["block_id"] == new_p_a)
        report.eq("类型已变", node["type"], "heading3")
        report.eq("顶层块数不变（替换不增不减）", len(blocks), len(before_top) + 11)

        # ------------------------------------------------------------ 删除
        report.section("删除")
        tree = client.read_document(ref)
        bullet_node = next(b for b in tree["blocks"] if b["block_id"] == p_bullet)
        child_1 = bullet_node["children"][0]["block_id"]
        client.delete_block(ref, child_1)
        tree = client.read_document(ref)
        bullet_node = next(b for b in tree["blocks"] if b["block_id"] == p_bullet)
        report.eq("删掉一个子块后剩一个", len(bullet_node.get("children") or []), 1)

        # delete_range
        blocks = top_blocks(client, doc_id)
        r = track(client.insert_blocks(ref, [
            {"type": "text", "text": "range-1"},
            {"type": "text", "text": "range-2"},
            {"type": "text", "text": "range-3"},
        ]))
        start = index_of(top_blocks(client, doc_id), r["created"][0]["block_id"])
        client.delete_range(ref, start, start + 3)
        texts = [block_text(b) for b in top_blocks(client, doc_id)]
        report.check("delete_range 删掉 3 块", not any(t.startswith("range-") for t in texts))

        # ------------------------------------------------------- 组合 + dry_run
        report.section("组合编辑")
        marker_idx = index_of(top_blocks(client, doc_id), marker)
        res = client.edit_document(ref, [
            {"op": "update", "block_id": p_spans, "text": "组合编辑写入"},
            {"op": "insert", "index": marker_idx + 1, "blocks": [{"type": "text", "text": "组合插入"}]},
        ])
        report.eq("组合编辑执行 2 步", res["applied"], 2)
        report.check("组合插入的新块已创建",
                     any(block_text(b) == "组合插入" for b in top_blocks(client, doc_id)))

        snapshot = [(b["block_id"], block_text(b)) for b in top_blocks(client, doc_id)]
        client.edit_document(ref, [{"op": "update", "block_id": p_spans, "text": "dry-run 不应写"}],
                             dry_run=True)
        after_dry = [(b["block_id"], block_text(b)) for b in top_blocks(client, doc_id)]
        report.eq("dry_run 不改文档", snapshot, after_dry)

        # ------------------------------------------------------------ 错误路径
        report.section("错误提示")
        for name, fn, keyword in [
            ("越界 index 给出可用范围",
             lambda: client.insert_blocks(ref, [{"type": "text", "text": "x"}], index=9999), "越界"),
            ("未知块类型提示可用类型",
             lambda: client.insert_blocks(ref, [{"type": "video"}]), "不支持的块类型"),
            ("不存在的 block_id 提示重新读取",
             lambda: client.update_block(ref, "doxcn_not_exist_xyz", text="x"), "不存在"),
            ("未知操作名给出可选值",
             lambda: client.edit_document(ref, [{"op": "nope"}]), "未知操作"),
        ]:
            try:
                fn()
                report.check(name, False, "没有抛错")
            except FeishuError as exc:
                report.check(name, keyword in exc.msg, exc.msg)

    finally:
        # ------------------------------------------------------------- 清理
        report.section("清理")
        keep = {bid for bid, _, _ in before_top}
        remaining = top_blocks(client, doc_id)
        removed = 0
        for b in list(remaining):
            if b["block_id"] not in keep:
                try:
                    client.delete_block(ref, b["block_id"])
                    removed += 1
                except FeishuError as exc:
                    report.check("清理块 %s" % b["block_id"], False, exc.msg)
        report.check("临时块已全部删除", removed >= 1, "removed=%d" % removed)

        after = client.read_document(ref, include_style=True)
        after_top = [(b["block_id"], b["text"], b["type"]) for b in after["blocks"]]
        report.eq("原有块未被动过（ID/文本/类型一致）", after_top, before_top)

    return report.dump()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=os.environ.get("FEISHU_TEST_DOC", ""),
                    help="测试文档：wiki 链接 / wiki token / docx id")
    ap.add_argument("--config", default=os.environ.get("FEISHU_CONFIG", ""))
    args = ap.parse_args()
    if not args.doc:
        print("缺少 --doc / FEISHU_TEST_DOC", file=sys.stderr)
        return 2

    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        client = FeishuClient(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"])
    else:
        from pathlib import Path
        path = args.config or str(Path(__file__).with_name("config.json"))
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        client = FeishuClient(cfg["app_id"], cfg["app_secret"])

    report = Report()
    try:
        failed = run(client, args.doc, report)
    except FeishuError as exc:
        report.check("未捕获的飞书错误", False, str(exc))
        failed = report.dump()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
