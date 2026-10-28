#!/usr/bin/env python3
"""飞书应用权限自检：逐项探测能力，输出「已具备 / 缺失」。

设计原则：所有探测都带假 ID 或空参数，**不会创建、修改、删除任何真实资源**；
素材上传探测用假 parent_node 且不带文件，鉴权通过后会停在参数校验。

用法：
    uv run --directory . python scope_check.py
    uv run --directory . python scope_check.py --json
"""

import argparse
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

from feishu_client import BASE_URL, FeishuClient, FeishuError

HERE = os.path.dirname(os.path.abspath(__file__))

# 每条：(能力, 需要的权限之一, method, path, body, query)
# method == "MULTIPART" 时走 multipart 探测
CHECKS = [
    ("读取文档块", "docx:document:readonly", "GET",
     "/docx/v1/documents/fakeToken/blocks", None, None),
    ("编辑文档块", "docx:document:write_only", "PATCH",
     "/docx/v1/documents/fakeToken/blocks/batch_update", {"requests": []}, None),
    ("创建文档", "docx:document:create / docx:document", "POST",
     "/docx/v1/documents", {"title": "probe", "folder_token": "fldcnFake"}, None),
    ("云文档列表", "drive:drive:readonly / drive:drive / space:document:retrieve", "GET",
     "/drive/v1/files", None, None),
    ("复制云文档", "docs:document:copy / drive:drive", "POST",
     "/drive/v1/files/fakeToken/copy",
     {"name": "probe", "type": "docx", "folder_token": "fldcnFake"}, None),
    ("删除云文档", "space:document:delete / drive:drive", "DELETE",
     "/drive/v1/files/fakeToken", None, {"type": "file"}),
    ("上传素材（图片/附件）", "docs:document.media:upload", "MULTIPART",
     "/drive/v1/medias/upload_all", None, None),
    ("下载素材（图片/附件）", "docs:document.media:download / drive:drive:readonly", "GET",
     "/drive/v1/medias/fakeToken/download", None, None),
    ("知识库空间列表", "wiki:wiki / wiki:wiki:readonly", "GET", "/wiki/v2/spaces", None, None),
    ("知识库建/移节点", "wiki:node:create / wiki:node:move / wiki:wiki", "POST",
     "/wiki/v2/spaces/fake/nodes/fake/move",
     {"target_parent_token": "fake", "target_space_id": "fake"}, None),
    ("读电子表格", "sheets:spreadsheet / sheets:spreadsheet:readonly", "GET",
     "/sheets/v3/spreadsheets/fakeToken", None, None),
    ("创建电子表格", "sheets:spreadsheet:create / sheets:spreadsheet / drive:drive", "POST",
     "/sheets/v3/spreadsheets", {}, None),
    ("读多维表格", "bitable:app / bitable:app:readonly", "GET",
     "/bitable/v1/apps/fakeToken/tables", None, None),
    ("创建多维表格", "base:app:create / bitable:app", "POST", "/bitable/v1/apps", {}, None),
    ("画板节点读取", "board:whiteboard:node:read", "GET",
     "/board/v1/whiteboards/AbCdEfGhIjKlMnOpQrStUvWx/nodes", None, None),
    ("画板节点写入（画图）", "board:whiteboard:node:create", "POST",
     "/board/v1/whiteboards/AbCdEfGhIjKlMnOpQrStUvWx/nodes",
     {"nodes": [{"id": "p1", "type": "composite_shape", "x": 0, "y": 0,
                 "width": 100, "height": 60,
                 "composite_shape": {"type": "rect"}}]}, None),
    ("OCR 图片识字", "optical_char_recognition:image", "POST",
     "/optical_char_recognition/v1/image/basic_recognize", {}, None),
    ("读取任务", "task:task:read / task:task:write", "GET", "/task/v2/tasks", None, None),
    ("编辑任务", "task:task:write / task:task:delete", "DELETE",
     "/task/v2/tasks/fakeGuid", None, None),
    ("读取日历", "calendar:calendar:readonly / calendar:calendar", "GET",
     "/calendar/v4/calendars", None, None),
    ("群组信息", "im:chat:readonly / im:chat", "GET", "/im/v1/chats", None, None),
    ("通讯录读取", "contact:*（已具备，scope 名未确认）", "GET",
     "/contact/v3/users", None, {"page_size": 1}),
    ("IM 图片/文件上传", "im:resource", "MULTIPART_EMPTY", "/im/v1/images", None, None),
]


def multipart(token, path, with_file):
    boundary = "----scopeprobe" + uuid.uuid4().hex
    parts = []
    if path.endswith("/upload_all"):
        fields = {"file_name": "probe.png", "parent_type": "docx_image",
                  "parent_node": "fakeDocToken", "size": "0"}
        for k, v in fields.items():
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, k, v)).encode())
        if with_file:
            parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; '
                          'filename="probe.png"\r\nContent-Type: image/png\r\n\r\n' % boundary).encode()
                         + b"\x89PNG\r\n\x1a\n" + b"\r\n")
    else:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="image_type"\r\n\r\n'
                      'message\r\n' % boundary).encode())
        if with_file:
            parts.append(('--%s\r\nContent-Disposition: form-data; name="image"; '
                          'filename="probe.png"\r\nContent-Type: image/png\r\n\r\n' % boundary).encode()
                         + b"\x89PNG\r\n\x1a\n" + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(BASE_URL + path, data=b"".join(parts), method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (ValueError, UnicodeError):
            return {"code": exc.code, "msg": "HTTP %s" % exc.code}


def check(client, name, need, method, path, body, query):
    try:
        if method.startswith("MULTIPART"):
            result = multipart(client._get_token(), path, with_file=False)
            code, msg = result.get("code"), result.get("msg", "")
            if code == 0:
                return "granted", need, ""
            if code == 99991672:
                return "missing", need, msg
            return "granted", need, "code=%s" % code
        client._request(method, path, body=body, query=query)
        return "granted", need, ""
    except FeishuError as exc:
        if exc.code == 99991672:
            required = exc.msg
            if "required: [" in required:
                required = required.split("required: [", 1)[1].split("]", 1)[0]
            return "missing", required, ""
        if exc.code in (99991671, 99991663):
            return "missing", need, "token/身份不对：%s" % exc.msg
        return "granted", need, "code=%s" % exc.code
    except Exception as exc:  # noqa: BLE001
        return "unknown", need, str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", default=os.environ.get("FEISHU_CONFIG", ""))
    args = ap.parse_args()

    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        client = FeishuClient(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"])
    else:
        cfg = json.loads(open(args.config or os.path.join(HERE, "config.json")).read())
        client = FeishuClient(cfg["app_id"], cfg["app_secret"])

    rows = []
    for name, need, method, path, body, query in CHECKS:
        status, required, detail = check(client, name, need, method, path, body, query)
        rows.append({"capability": name, "status": status,
                     "required": required, "detail": detail})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if all(r["status"] == "granted" for r in rows) else 1

    icon = {"granted": "✅", "missing": "⛔", "unknown": "❔"}
    label = {"granted": "已具备", "missing": "缺失", "unknown": "未知"}
    width = max(len(r["capability"]) for r in rows) + 2
    for r in rows:
        line = "%s %s %s" % (icon[r["status"]], r["capability"].ljust(width), label[r["status"]])
        if r["status"] == "missing":
            line += "  需要：" + r["required"]
        elif r["detail"]:
            line += "  (" + r["detail"] + ")"
        print(line)

    missing = [r for r in rows if r["status"] == "missing"]
    print("\n缺失 %d 项 / 共 %d 项" % (len(missing), len(rows)))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
