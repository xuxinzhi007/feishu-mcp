"""飞书开放平台 API 客户端（纯标准库，Python 3.9+）。

只封装已经实机验证过的接口：租户令牌、发消息、知识库节点解析、云文档读写。
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://open.feishu.cn/open-apis"

# block_type -> 承载文本的字段名
TEXT_FIELD_BY_TYPE = {2: "text"}
for _i in range(1, 10):
    TEXT_FIELD_BY_TYPE[2 + _i] = "heading%d" % _i
TEXT_FIELD_BY_TYPE.update({12: "bullet", 13: "ordered", 14: "code", 15: "quote", 17: "todo", 23: "callout"})

# 追加块时支持的块类型 -> (block_type, 字段名)
WRITE_BLOCKS = {
    "text": (2, "text"),
    "heading1": (3, "heading1"),
    "heading2": (4, "heading2"),
    "heading3": (5, "heading3"),
    "bullet": (12, "bullet"),
    "ordered": (13, "ordered"),
    "quote": (15, "quote"),
    "divider": (22, "divider"),
}


class FeishuError(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__("飞书错误 %s：%s" % (code, msg))


def block_text(block):
    """从块结构里提取纯文本（分隔线、表格等无文本的块返回空串）。"""
    field = TEXT_FIELD_BY_TYPE.get(block.get("block_type"))
    if not field:
        return ""
    node = block.get(field) or {}
    parts = []
    for el in node.get("elements", []):
        run = el.get("text_run")
        if run:
            parts.append(run.get("content", ""))
    return "".join(parts)


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0.0

    # ---- 基础设施 ----

    def _get_token(self):
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        data = self._request("POST", "/auth/v3/tenant_access_token/internal", {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }, auth=False)
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError(-1, "认证失败，未返回 tenant_access_token")
        self._token = token
        self._token_expires = time.time() + int(data.get("expire", 7200))
        return token

    def _request(self, method, path, body=None, query=None, auth=True):
        url = BASE_URL + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = "Bearer " + self._get_token()
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.load(resp)
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except (ValueError, UnicodeError):
                raise FeishuError(exc.code, "HTTP %s，无响应体" % exc.code) from None
        except (OSError, ValueError) as exc:
            raise FeishuError(-1, "网络异常：%s" % exc) from None
        if not isinstance(result, dict) or result.get("code") != 0:
            code = result.get("code") if isinstance(result, dict) else -1
            msg = result.get("msg") if isinstance(result, dict) else str(result)
            raise FeishuError(code, msg)
        return result.get("data") if isinstance(result, dict) and "data" in result else result

    # ---- 消息 ----

    def send_message(self, receive_id, content, receive_id_type="email", msg_type="text"):
        if msg_type == "text":
            content = json.dumps({"text": content}, ensure_ascii=False)
        data = self._request("POST", "/im/v1/messages", body={
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }, query={"receive_id_type": receive_id_type})
        return {"message_id": data.get("message_id")}

    # ---- 文档定位 ----

    def resolve_doc(self, ref):
        """把 wiki 链接/wiki token/docx 文档 ID 解析成文档 ID。

        返回 {"document_id", "type", "title"}。
        """
        ref = (ref or "").strip()
        docx_match = re.search(r"/docx/([A-Za-z0-9]+)", ref)
        if docx_match:
            return {"document_id": docx_match.group(1), "type": "docx", "title": None}

        wiki_match = re.search(r"/wiki/([A-Za-z0-9]+)", ref)
        token = wiki_match.group(1) if wiki_match else ref
        if wiki_match or re.fullmatch(r"[A-Za-z0-9]+", token):
            try:
                data = self._request("GET", "/wiki/v2/spaces/get_node", query={"token": token})
                node = (data or {}).get("node")
                if node:
                    return {
                        "document_id": node.get("obj_token"),
                        "type": node.get("obj_type"),
                        "title": node.get("title"),
                    }
            except FeishuError:
                pass
        return {"document_id": token, "type": "docx", "title": None}

    def _document_id(self, ref):
        doc = self.resolve_doc(ref)
        doc_id = doc.get("document_id")
        if not doc_id:
            raise FeishuError(-1, "无法从引用中解析出文档 ID：%s" % ref)
        return doc

    # ---- 文档读写 ----

    def read_document(self, ref):
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        meta = self._request("GET", "/docx/v1/documents/%s" % doc_id)
        title = ((meta or {}).get("document") or {}).get("title") or doc.get("title")
        items = self._request("GET", "/docx/v1/documents/%s/blocks" % doc_id,
                              query={"page_size": 500}).get("items", [])
        blocks = []
        for b in items:
            if b.get("block_type") == 1:  # page 块本身无文本
                continue
            blocks.append({
                "block_id": b.get("block_id"),
                "block_type": b.get("block_type"),
                "text": block_text(b),
            })
        return {
            "document_id": doc_id,
            "title": title,
            "blocks": blocks,
            "text": "\n".join(x["text"] for x in blocks),
        }

    def append_document(self, ref, blocks):
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        children = []
        for spec in blocks:
            kind = spec.get("type", "text")
            if kind not in WRITE_BLOCKS:
                raise FeishuError(-1, "不支持的块类型：%s" % kind)
            block_type, field = WRITE_BLOCKS[kind]
            if kind == "divider":
                children.append({"block_type": block_type, "divider": {}})
            else:
                text = spec.get("text", "")
                children.append({
                    "block_type": block_type,
                    field: {"elements": [{"text_run": {"content": text}}]},
                })
        data = self._request("POST", "/docx/v1/documents/%s/blocks/%s/children" % (doc_id, doc_id),
                              body={"children": children})
        created = []
        for b in data.get("children", []):
            created.append({"block_id": b.get("block_id"), "block_type": b.get("block_type"), "text": block_text(b)})
        return {"document_id": doc_id, "created": created}

    def delete_block(self, ref, block_id):
        """删除文档顶层的块（仅支持根节点直接子块）。"""
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        items = self._request("GET", "/docx/v1/documents/%s/blocks/%s/children" % (doc_id, doc_id),
                              query={"page_size": 500}).get("items", [])
        index = None
        for i, b in enumerate(items):
            if b.get("block_id") == block_id:
                index = i
                break
        if index is None:
            raise FeishuError(-1, "未在文档顶层找到块 %s（删除仅支持顶层块）" % block_id)
        self._request("DELETE", "/docx/v1/documents/%s/blocks/%s/children/batch_delete" % (doc_id, doc_id),
                      body={"start_index": index, "end_index": index + 1})
        return {"deleted": block_id}
