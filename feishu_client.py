"""飞书开放平台 API 客户端（纯标准库，Python 3.9+）。

设计目标：**能原地改已有段落，而不是只能追加/整段删除**。

飞书 docx v1 实际提供的能力（均已实机验证）：
  - 读块树      GET    /docx/v1/documents/{doc}/blocks           （全量块，含父子关系）
  - 原地改内容  PATCH  /docx/v1/documents/{doc}/blocks/batch_update
                update_text_elements 换掉该块的文本元素，block_id 与位置都不变
                update_text_style    改块级样式（对齐/折叠/todo 勾选/代码语言/换行/缩进/编号）
  - 定点插入    POST   /docx/v1/documents/{doc}/blocks/{parent}/children
                body.index 指定插入位置（-1 = 末尾）
  - 嵌套创建    POST   /docx/v1/documents/{doc}/blocks/{parent}/descendant
                一次创建带子块的树（高亮块/分栏/表格单元格必须用这个）
  - 按位置删除  DELETE /docx/v1/documents/{doc}/blocks/{parent}/children/batch_delete
                以「父块 + start_index/end_index」定位，不是按 block_id

能力边界（不是实现遗漏，是接口本身没有）：
  - 没有「改 block_type」的接口。text ↔ heading 互转只能在同一位置「插入新块 + 删旧块」，
    位置和顺序能保住，block_id 会变（见 replace_block）。
  - 标题/正文的折叠、todo 勾选、代码语言属于块级样式，走 update_text_style。
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://open.feishu.cn/open-apis"

# ---------------------------------------------------------------------------
# 块类型表（对齐官方 BlockType 枚举）
# ---------------------------------------------------------------------------

BLOCK_TYPES = {
    1: "page",
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    18: "bitable",
    19: "callout",
    20: "chat_card",
    21: "diagram",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    26: "iframe",
    27: "image",
    28: "isv",
    29: "mindnote",
    30: "sheet",
    31: "table",
    32: "table_cell",
    33: "view",
    34: "quote_container",
    35: "task",
    36: "okr",
    37: "okr_objective",
    38: "okr_key_result",
    39: "okr_progress",
    40: "add_ons",
    41: "jira_issue",
    42: "wiki_catalog",
    43: "board",
    44: "agenda",
    45: "agenda_item",
    46: "agenda_item_title",
    47: "agenda_item_content",
    48: "link_preview",
    49: "source_synced",
    50: "reference_synced",
}

# 承载富文本的块类型 -> 内容字段名（字段名恰好等于类型名）
TEXT_FIELD_BY_TYPE = {1: "page", 2: "text", 22: "divider"}
for _i in range(1, 10):
    TEXT_FIELD_BY_TYPE[2 + _i] = "heading%d" % _i
TEXT_FIELD_BY_TYPE.update({12: "bullet", 13: "ordered", 14: "code", 15: "quote", 17: "todo"})

# 能通过 update_text_elements 改写的块类型（分割线没有文字）
EDITABLE_TEXT_TYPES = set(TEXT_FIELD_BY_TYPE) - {22}

# 可新建的块类型 -> (block_type, 内容字段名)
WRITE_BLOCKS = {
    "text": (2, "text"),
    "heading1": (3, "heading1"),
    "heading2": (4, "heading2"),
    "heading3": (5, "heading3"),
    "heading4": (6, "heading4"),
    "heading5": (7, "heading5"),
    "heading6": (8, "heading6"),
    "heading7": (9, "heading7"),
    "heading8": (10, "heading8"),
    "heading9": (11, "heading9"),
    "bullet": (12, "bullet"),
    "ordered": (13, "ordered"),
    "code": (14, "code"),
    "quote": (15, "quote"),
    "todo": (17, "todo"),
    "callout": (19, "callout"),
    "divider": (22, "divider"),
    "table": (31, "table"),
    "table_cell": (32, "table_cell"),
}

TYPE_ALIASES = {
    "h1": "heading1", "h2": "heading2", "h3": "heading3", "h4": "heading4",
    "h5": "heading5", "h6": "heading6", "h7": "heading7", "h8": "heading8", "h9": "heading9",
    "title": "heading1", "subtitle": "heading2",
    "paragraph": "text", "p": "text", "plain": "text",
    "list": "bullet", "ul": "bullet", "unordered": "bullet",
    "ol": "ordered", "numbered": "ordered",
    "checkbox": "todo", "check": "todo", "task": "todo",
    "hr": "divider", "separator": "divider", "line": "divider",
    "blockquote": "quote", "pre": "code", "codewrap": "code",
}

# 代码块 language 名称 -> 飞书 CodeLanguage 枚举值（官方 1~75）
CODE_LANGUAGES = {
    "plaintext": 1, "text": 1, "plain": 1,
    "bash": 7,
    "csharp": 8, "c#": 8,
    "c++": 9, "cpp": 9,
    "c": 10,
    "css": 12,
    "dart": 15,
    "dockerfile": 18, "docker": 18,
    "go": 22, "golang": 22,
    "html": 24,
    "json": 28,
    "java": 29,
    "javascript": 30, "js": 30,
    "kotlin": 32,
    "latex": 33, "tex": 33,
    "lua": 36,
    "markdown": 39, "md": 39,
    "php": 43,
    "python": 49, "py": 49,
    "r": 50,
    "ruby": 52, "rb": 52,
    "rust": 53, "rs": 53,
    "sql": 56,
    "scala": 57,
    "shell": 60, "sh": 60, "zsh": 60,
    "swift": 61,
    "typescript": 63, "ts": 63,
    "xml": 66,
    "yaml": 67, "yml": 67,
    "cmake": 68,
    "diff": 69,
    "graphql": 71,
    "toml": 75,
}

# text_run 支持的内联样式字段
INLINE_STYLE_KEYS = ("bold", "italic", "underline", "strikethrough", "inline_code")

# 块级样式 -> update_text_style.fields 位
BLOCK_STYLE_FIELDS = {
    "align": 1,
    "done": 2,
    "folded": 3,
    "language": 4,
    "wrap": 5,
    "background_color": 6,
    "indentation_level": 7,
}

ALIGN_VALUES = {"left": 1, "center": 2, "right": 3}
INDENT_VALUES = {"none": "NoIndent", "noindent": "NoIndent", "one": "OneLevelIndent",
                 "onelevelindent": "OneLevelIndent"}
BACKGROUND_COLORS = {
    "lightgray": "LightGrayBackground", "lightred": "LightRedBackground",
    "lightorange": "LightOrangeBackground", "lightyellow": "LightYellowBackground",
    "lightgreen": "LightGreenBackground", "lightblue": "LightBlueBackground",
    "lightpurple": "LightPurpleBackground", "palegray": "PaleGrayBackground",
    "darkgray": "DarkGrayBackground", "darkred": "DarkRedBackground",
    "darkorange": "DarkOrangeBackground", "darkyellow": "DarkYellowBackground",
    "darkgreen": "DarkGreenBackground", "darkblue": "DarkBlueBackground",
    "darkpurple": "DarkPurpleBackground",
}

# 高亮块背景色（CalloutBackgroundColor 枚举）
CALLOUT_BACKGROUNDS = {
    "lightred": 1, "lightorange": 2, "lightyellow": 3, "lightgreen": 4,
    "lightblue": 5, "lightpurple": 6, "lightgray": 7,
    "darkred": 8, "darkorange": 9, "darkyellow": 10, "darkgreen": 11,
    "darkblue": 12, "darkpurple": 13, "darkgray": 14,
}

# 限频：单应用 3 QPS，单文档并发 3 QPS
_RETRY_CODES = {99991400, 1254290, 1061045}
_MAX_RETRIES = 5
_WRITE_MIN_INTERVAL = 0.36


class FeishuError(Exception):
    def __init__(self, code, msg, status=None, path=None):
        self.code = code
        self.msg = msg
        self.status = status
        self.path = path
        super().__init__("飞书错误 %s：%s" % (code, msg))


# ---------------------------------------------------------------------------
# 文本/元素工具
# ---------------------------------------------------------------------------


def _elements_text(block, field=None):
    """把一个块里的 text_element 列表拼成纯文本。"""
    t = block.get("block_type")
    field = field or TEXT_FIELD_BY_TYPE.get(t)
    if not field:
        return ""
    node = block.get(field) or {}
    parts = []
    for el in node.get("elements") or []:
        if "text_run" in el:
            parts.append(el["text_run"].get("content", ""))
        elif "mention_user" in el:
            parts.append("@用户")
        elif "mention_doc" in el:
            md = el["mention_doc"]
            parts.append(md.get("title") or md.get("url") or "@文档")
        elif "equation" in el:
            parts.append(el["equation"].get("content", ""))
        elif "reminder" in el:
            parts.append("[日期提醒]")
        elif "file" in el:
            parts.append("[附件]")
        elif "inline_block" in el:
            parts.append("[内嵌块]")
    return "".join(parts)


def block_text(block):
    """从块结构里提取纯文本（无文本的块返回空串）。"""
    return _elements_text(block)


def _text_run(content, style=None):
    """构造 text_run，支持可选内联样式。"""
    run = {"content": content}
    if style:
        st = {}
        for k in INLINE_STYLE_KEYS:
            if style.get(k):
                st[k] = True
        link = style.get("link")
        if link:
            st["link"] = {"url": urllib.parse.quote(link, safe="")}
        for k in ("text_color", "background_color"):
            if style.get(k):
                st[k] = style[k]
        if st:
            run["text_element_style"] = st
    return {"text_run": run}


def _pick_inline_style(src):
    """从任意 dict 里挑出内联样式字段。"""
    if not src:
        return None
    out = {k: src[k] for k in INLINE_STYLE_KEYS if src.get(k)}
    if src.get("link"):
        out["link"] = src["link"]
    return out or None


def _code_language(lang):
    """把代码块语言解析成枚举值；未知语言回退纯文本(1)。"""
    if isinstance(lang, int):
        return lang
    return CODE_LANGUAGES.get(str(lang).strip().lower(), 1)


def _block_style(language=None, align=None, done=None, folded=None, wrap=None,
                 indentation_level=None, background_color=None, sequence=None):
    """把用户友好的块级样式转成飞书 text_style + fields 位。"""
    style, fields = {}, []
    if align is not None:
        style["align"] = ALIGN_VALUES.get(str(align).lower(), align) if not isinstance(align, int) else align
        fields.append(BLOCK_STYLE_FIELDS["align"])
    if done is not None:
        style["done"] = bool(done)
        fields.append(BLOCK_STYLE_FIELDS["done"])
    if folded is not None:
        style["folded"] = bool(folded)
        fields.append(BLOCK_STYLE_FIELDS["folded"])
    if language is not None:
        style["language"] = _code_language(language)
        fields.append(BLOCK_STYLE_FIELDS["language"])
    if wrap is not None:
        style["wrap"] = bool(wrap)
        fields.append(BLOCK_STYLE_FIELDS["wrap"])
    if background_color is not None:
        key = str(background_color).lower()
        style["background_color"] = BACKGROUND_COLORS.get(key, background_color)
        fields.append(BLOCK_STYLE_FIELDS["background_color"])
    if indentation_level is not None:
        key = str(indentation_level).lower()
        style["indentation_level"] = INDENT_VALUES.get(key, indentation_level)
        fields.append(BLOCK_STYLE_FIELDS["indentation_level"])
    if sequence is not None:
        style["sequence"] = str(sequence)
        # sequence 没有独立的 fields 位，必须和至少一个其它样式字段一起下发
        if not fields:
            raise FeishuError(-1, "sequence 不能单独修改，请同时指定 align 等其它块级样式")
    return style, fields


def normalize_block_type(kind):
    kind = (kind or "text").strip().lower()
    kind = TYPE_ALIASES.get(kind, kind)
    if kind not in WRITE_BLOCKS:
        raise FeishuError(-1, "不支持的块类型：%s（可用：%s）" % (kind, ", ".join(sorted(WRITE_BLOCKS))))
    return kind


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0.0
        self._last_write = 0.0

    # ---- 基础设施 ----

    def _get_token(self):
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        data = self._request("POST", "/auth/v3/tenant_access_token/internal", {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }, auth=False, write=False)
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuError(-1, "认证失败，未返回 tenant_access_token")
        self._token = token
        self._token_expires = time.time() + int(data.get("expire", 7200))
        return token

    def _request(self, method, path, body=None, query=None, auth=True, write=None):
        """发请求；对限频/瞬时错误做指数退避重试。"""
        if write is None:
            write = method in ("POST", "PATCH", "PUT", "DELETE")
        url = BASE_URL + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = "Bearer " + self._get_token()
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")

        last_exc = None
        for attempt in range(_MAX_RETRIES):
            if write:
                wait = _WRITE_MIN_INTERVAL - (time.time() - self._last_write)
                if wait > 0:
                    time.sleep(wait)
                self._last_write = time.time()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.load(resp)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise FeishuError(exc.code, "HTTP %s，无 JSON 响应体" % exc.code,
                                      status=exc.code, path=path) from None
                if (exc.code == 429 or result.get("code") in _RETRY_CODES) and attempt < _MAX_RETRIES - 1:
                    last_exc = FeishuError(result.get("code"), result.get("msg"),
                                           status=exc.code, path=path)
                    time.sleep(min(8.0, 0.6 * (2 ** attempt)))
                    continue
                raise FeishuError(result.get("code"), result.get("msg"),
                                  status=exc.code, path=path) from None
            except (OSError, ValueError) as exc:
                # 读请求可安全重试；写请求超时/断连时结果未知，重试可能建出重复块，
                # 所以直接报错，让调用方先读文档确认。
                if not write and attempt < _MAX_RETRIES - 1:
                    time.sleep(min(8.0, 0.6 * (2 ** attempt)))
                    continue
                hint = "（写操作网络中断，结果未知；请先 feishu_read_document 确认是否已生效）" if write else ""
                raise FeishuError(-1, "网络异常：%s%s" % (exc, hint), path=path) from None

            if not isinstance(result, dict) or result.get("code") != 0:
                code = result.get("code") if isinstance(result, dict) else -1
                msg = result.get("msg") if isinstance(result, dict) else str(result)
                if code in _RETRY_CODES and attempt < _MAX_RETRIES - 1:
                    last_exc = FeishuError(code, msg, path=path)
                    time.sleep(min(8.0, 0.6 * (2 ** attempt)))
                    continue
                raise FeishuError(code, msg, path=path)
            return result.get("data") if "data" in result else result
        raise last_exc or FeishuError(-1, "请求失败：%s" % path)

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
        """把 wiki 链接/wiki token/docx 文档 ID 解析成 {"document_id","type","title"}。"""
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
        if not doc.get("document_id"):
            raise FeishuError(-1, "无法从引用中解析出文档 ID：%s" % ref)
        return doc

    def _doc_meta(self, doc_id):
        data = self._request("GET", "/docx/v1/documents/%s" % doc_id)
        return (data or {}).get("document") or {}

    # ---- 块读取 ----

    def _all_blocks(self, doc_id):
        items, page_token = [], None
        while True:
            data = self._request("GET", "/docx/v1/documents/%s/blocks" % doc_id,
                                 query={"page_size": 500, "page_token": page_token})
            items.extend(data.get("items") or [])
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                break
        return items

    def _tree(self, items, include_style=False):
        by_id = {b["block_id"]: b for b in items}
        root = next((b for b in items if b.get("block_type") == 1), None)
        if root is None:
            raise FeishuError(-1, "文档里找不到 page 根块，可能没有读权限")

        def node(b, index, depth, path):
            n = {
                "block_id": b["block_id"],
                "type": BLOCK_TYPES.get(b.get("block_type"), "unknown_%s" % b.get("block_type")),
                "index": index,
                "path": path,
                "text": block_text(b),
            }
            field = TEXT_FIELD_BY_TYPE.get(b.get("block_type"))
            if include_style and field and b.get("block_type") in EDITABLE_TEXT_TYPES:
                node_data = b.get(field) or {}
                n["style"] = node_data.get("style") or {}
                spans = []
                for el in node_data.get("elements") or []:
                    tr = el.get("text_run")
                    if not tr:
                        continue
                    st = tr.get("text_element_style") or {}
                    span = {"text": tr.get("content", "")}
                    for k in INLINE_STYLE_KEYS:
                        if st.get(k):
                            span[k] = True
                    if st.get("link"):
                        span["link"] = urllib.parse.unquote(st["link"].get("url", ""))
                    spans.append(span)
                if spans:
                    n["spans"] = spans
            kids = [by_id[c] for c in (b.get("children") or []) if c in by_id]
            if kids:
                n["children"] = [node(k, i, depth + 1, "%s.%d" % (path, i)) for i, k in enumerate(kids)]
            return n

        top = [by_id[c] for c in (root.get("children") or []) if c in by_id]
        return [node(b, i, 0, str(i)) for i, b in enumerate(top)], root

    def read_document(self, ref, include_style=False):
        """读取文档标题 + 完整块树（含 block_id / 类型 / 位置 / 层级）。

        include_style=True 时额外返回块级样式与内联 spans，用于精确编辑。
        """
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        meta = self._doc_meta(doc_id)
        items = self._all_blocks(doc_id)
        tree, root = self._tree(items, include_style=include_style)
        title = meta.get("title") or block_text(root) or doc.get("title")
        return {
            "document_id": doc_id,
            "title": title,
            "title_block_id": root["block_id"],
            "revision_id": meta.get("revision_id"),
            "count": len(items) - 1,
            "blocks": tree,
        }

    def get_block(self, ref, block_id):
        """取单个块的原始结构。"""
        doc = self._document_id(ref)
        try:
            data = self._request(
                "GET", "/docx/v1/documents/%s/blocks/%s" % (doc["document_id"], block_id))
        except FeishuError as exc:
            if exc.code in (1770001, 1770002, 1770003):
                raise FeishuError(
                    exc.code,
                    "块 %s 不存在或已删除，请重新调用 feishu_read_document 取最新 block_id"
                    % block_id,
                ) from None
            raise
        block = (data or {}).get("block")
        if not block:
            raise FeishuError(-1, "找不到块 %s" % block_id)
        return block

    def _locate(self, doc_id, block_id, blocks=None):
        """返回 (父块 ID, 在父块 children 中的下标, 父块原始结构)。"""
        blocks = blocks or self._all_blocks(doc_id)
        by_id = {b["block_id"]: b for b in blocks}
        target = by_id.get(block_id)
        if not target:
            raise FeishuError(-1, "文档里没有块 %s；请先调用 feishu_read_document 取最新 block_id" % block_id)
        parent_id = target.get("parent_id")
        parent = by_id.get(parent_id)
        if parent is None:
            root = next((b for b in blocks if b.get("block_type") == 1), None)
            parent = root
            parent_id = root["block_id"]
        children = parent.get("children") or []
        if block_id not in children:
            raise FeishuError(-1, "块 %s 不在父块 %s 的子块列表中（可能已被删除）" % (block_id, parent_id))
        return parent_id, children.index(block_id), parent

    def _children_of(self, doc_id, parent_id):
        items, page_token = [], None
        while True:
            data = self._request(
                "GET",
                "/docx/v1/documents/%s/blocks/%s/children" % (doc_id, parent_id),
                query={"page_size": 500, "page_token": page_token},
            )
            items.extend(data.get("items") or [])
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                break
        return items

    # ---- 块构造 ----

    def _elements_from_spec(self, spec):
        spans = spec.get("spans")
        if spans:
            elements = []
            for sp in spans:
                if not isinstance(sp, dict):
                    continue
                content = sp.get("text", sp.get("content", ""))
                elements.append(_text_run(content, _pick_inline_style(sp)))
            return elements or [_text_run("")]
        inline = _pick_inline_style(spec.get("style")) or _pick_inline_style(spec)
        return [_text_run(spec.get("text", ""), inline)]

    def _normalize_spec(self, spec):
        """规整块定义：表格糖、高亮块默认子块、递归处理 children。"""
        if not isinstance(spec, dict):
            raise FeishuError(-1, "块定义必须是 dict：%r" % (spec,))
        kind = normalize_block_type(spec.get("type", "text"))
        out = dict(spec)
        out["type"] = kind

        if kind == "table":
            rows = spec.get("rows")
            if rows:
                cells = []
                for row in rows:
                    for cell in row:
                        child = cell if isinstance(cell, dict) else {
                            "type": "text", "text": "" if cell is None else str(cell)}
                        cells.append({"type": "table_cell", "children": [child]})
                out["children"] = spec.get("children") or cells
                out["row_size"] = len(rows)
                out["column_size"] = max(len(r) for r in rows) if rows else 0
        elif kind in ("callout", "table_cell") and not spec.get("children"):
            out["children"] = [{"type": "text", "text": spec.get("text", "")}]

        if out.get("children"):
            out["children"] = [self._normalize_spec(c) for c in out["children"]]
        return out

    def _block_payload(self, spec):
        """把 {"type","text",...} 规范成飞书 block 结构。"""
        kind = normalize_block_type(spec.get("type", "text"))
        block_type, field = WRITE_BLOCKS[kind]
        if kind == "divider":
            return {"block_type": block_type, "divider": {}}
        if kind == "table_cell":
            return {"block_type": block_type, "table_cell": {}}
        if kind == "callout":
            data = {}
            bg = spec.get("background_color")
            if bg is not None:
                key = str(bg).lower()
                data["background_color"] = CALLOUT_BACKGROUNDS.get(key, bg)
            if spec.get("emoji_id"):
                data["emoji_id"] = spec["emoji_id"]
            return {"block_type": block_type, "callout": data}
        if kind == "table":
            rows = spec.get("row_size")
            cols = spec.get("column_size")
            if not rows or not cols:
                raise FeishuError(
                    -1, "创建表格要给 rows 二维数组，或同时给 row_size 和 column_size")
            prop = {"row_size": int(rows), "column_size": int(cols)}
            for key in ("header_row", "header_column", "column_width"):
                if spec.get(key) is not None:
                    prop[key] = spec[key]
            return {"block_type": block_type, "table": {"property": prop}}

        node = {"elements": self._elements_from_spec(spec)}
        style_src = spec.get("block_style") or spec
        style, fields = _block_style(
            language=(spec.get("language") if kind == "code" else None),
            align=style_src.get("align"),
            done=(style_src.get("done") if kind == "todo" else None),
            folded=style_src.get("folded"),
            wrap=style_src.get("wrap"),
            indentation_level=style_src.get("indentation_level"),
            background_color=style_src.get("background_color"),
            sequence=style_src.get("sequence"),
        )
        if style:
            node["style"] = style
        return {"block_type": block_type, field: node}

    # ---- 写操作 ----

    def _create_children(self, doc_id, parent_id, blocks, index=-1):
        """在 parent 下创建同级块；index=-1 表示追加到末尾。"""
        if not blocks:
            return []
        body = {"children": blocks}
        if index is not None and index >= 0:
            body["index"] = index
        data = self._request(
            "POST", "/docx/v1/documents/%s/blocks/%s/children" % (doc_id, parent_id), body=body)
        return data.get("children") or []

    def _create_descendants(self, doc_id, parent_id, specs, index=-1):
        """一次创建带层级结构的块树（高亮块/分栏/表格必须走这里）。"""
        temp_ids = []
        descendants = []
        counter = [0]

        def walk(spec, prefix):
            counter[0] += 1
            tmp = "tmp_%d" % counter[0]
            payload = self._block_payload(spec)
            payload["block_id"] = tmp
            children_specs = spec.get("children") or []
            if children_specs:
                payload["children"] = [walk(ch, tmp) for ch in children_specs]
            descendants.append(payload)
            return tmp

        for spec in specs:
            temp_ids.append(walk(spec, "root"))
        body = {"children_id": temp_ids, "descendants": descendants}
        if index is not None and index >= 0:
            body["index"] = index
        data = self._request(
            "POST", "/docx/v1/documents/%s/blocks/%s/descendant" % (doc_id, parent_id), body=body)
        relations = {r["temporary_block_id"]: r["block_id"]
                     for r in data.get("block_id_relations") or []}
        created = data.get("children") or []
        # 附上临时 ID 映射，方便调用方对应
        for c in created:
            for tmp, real in relations.items():
                if c.get("block_id") == real and tmp in temp_ids:
                    c["temporary_block_id"] = tmp
        return created, relations

    def _render_created(self, blocks):
        return [{
            "block_id": b.get("block_id"),
            "type": BLOCK_TYPES.get(b.get("block_type"), "unknown_%s" % b.get("block_type")),
            "text": block_text(b),
        } for b in blocks]

    def insert_blocks(self, ref, blocks, index=-1, parent_block_id=None):
        """在指定位置插入块（默认文档末尾）。

        blocks 支持嵌套：{"type":"bullet","text":"父","children":[{...}]}。
        嵌套会走「创建嵌套块」接口，一次请求建好整棵树。
        """
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        parent_id = parent_block_id or doc_id
        if parent_block_id:
            self.get_block(ref, parent_block_id)  # 校验父块存在

        if index is not None and index >= 0:
            existing = self._children_of(doc_id, parent_id)
            if index > len(existing):
                raise FeishuError(
                    -1,
                    "index 越界：父块 %s 现有 %d 个子块，index 只能是 0..%d（-1 表示末尾）"
                    % (parent_id, len(existing), len(existing)),
                )

        specs = [self._normalize_spec(b) for b in blocks]

        if any(b.get("children") for b in specs):
            created, relations = self._create_descendants(doc_id, parent_id, specs, index=index)
            return {
                "document_id": doc_id,
                "parent_block_id": parent_id,
                "created": self._render_created(created),
                "id_map": relations,
            }

        payloads = [self._block_payload(s) for s in specs]
        created = self._create_children(doc_id, parent_id, payloads, index=index)
        return {
            "document_id": doc_id,
            "parent_block_id": parent_id,
            "created": self._render_created(created),
        }

    def append_document(self, ref, blocks):
        """向文档末尾追加块（保留旧名字，等价 insert_blocks(index=-1)）。"""
        return self.insert_blocks(ref, blocks, index=-1)

    def update_block(self, ref, block_id, text=None, spans=None, style=None,
                     block_style=None):
        """原地修改已有块：block_id 和文档位置都不变。

        text   纯文本（替换该块全部内容）
        spans  分片富文本 [{"text":"...","bold":True}]
        style  内联样式（作用于整段，如 {"bold":True}）
        block_style 块级样式 {"align","done","folded","language","wrap",
                              "indentation_level","background_color","sequence"}
        """
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        block = self.get_block(ref, block_id)
        bt = block.get("block_type")
        field = TEXT_FIELD_BY_TYPE.get(bt)
        if bt not in EDITABLE_TEXT_TYPES:
            raise FeishuError(
                -1,
                "块 %s 是 %s 类型，不含可改的富文本。改它的子块（table_cell/callout 里的 text 块），"
                "或删掉重建" % (block_id, BLOCK_TYPES.get(bt, bt)),
            )

        req = {"block_id": block_id}
        if spans is not None or text is not None:
            spec = {"spans": spans} if spans is not None else {"text": text, "style": style}
            req["update_text_elements"] = {"elements": self._elements_from_spec(spec)}

        if block_style:
            style_payload, fields = _block_style(**{
                k: block_style.get(k) for k in (
                    "align", "done", "folded", "language", "wrap",
                    "indentation_level", "background_color", "sequence")
            })
            if fields:
                req["update_text_style"] = {"style": style_payload, "fields": fields}

        if len(req) == 1:
            raise FeishuError(-1, "没有要更新的内容：至少给 text / spans / block_style 之一")

        data = self._request("PATCH", "/docx/v1/documents/%s/blocks/batch_update" % doc_id,
                             body={"requests": [req]})
        updated = (data or {}).get("blocks") or []
        out = self._render_created(updated[:1]) or []
        return {
            "document_id": doc_id,
            "updated": out[0] if out else {"block_id": block_id},
        }

    def patch_block(self, ref, block_id, find, replace, all=False):
        """在块内做「查找替换」，保留该块其余文字的内联样式。

        这是最安全的改法：只动匹配到的那几个字，段落 id、位置、加粗等都不变。
        """
        if not find:
            raise FeishuError(-1, "find 不能为空")
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        block = self.get_block(ref, block_id)
        field = TEXT_FIELD_BY_TYPE.get(block.get("block_type"))
        if block.get("block_type") not in EDITABLE_TEXT_TYPES:
            raise FeishuError(-1, "块 %s 不含富文本，无法做文本替换" % block_id)
        node = dict(block.get(field) or {})
        elements = [dict(e) for e in (node.get("elements") or [])]

        count = _replace_in_elements(elements, find, replace, all_matches=all)
        if count == 0:
            return {"document_id": doc_id, "updated": {"block_id": block_id}, "replaced": 0,
                    "note": "块内没有找到目标文本，未做修改"}

        req = {"block_id": block_id, "update_text_elements": {"elements": elements}}
        data = self._request("PATCH", "/docx/v1/documents/%s/blocks/batch_update" % doc_id,
                             body={"requests": [req]})
        updated = (data or {}).get("blocks") or []
        out = self._render_created(updated[:1]) or []
        return {
            "document_id": doc_id,
            "updated": out[0] if out else {"block_id": block_id},
            "replaced": count,
        }

    def delete_block(self, ref, block_id):
        """删除块（任意层级，按“父块 + 下标”删）。"""
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        parent_id, index, _ = self._locate(doc_id, block_id)
        self._request(
            "DELETE",
            "/docx/v1/documents/%s/blocks/%s/children/batch_delete" % (doc_id, parent_id),
            body={"start_index": index, "end_index": index + 1},
        )
        return {"document_id": doc_id, "deleted": block_id, "parent_block_id": parent_id}

    def delete_range(self, ref, start_index, end_index, parent_block_id=None):
        """删除父块 children 的 [start, end) 区间（左闭右开）。"""
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        parent_id = parent_block_id or doc_id
        if end_index <= start_index:
            raise FeishuError(-1, "end_index 必须大于 start_index")
        self._request(
            "DELETE",
            "/docx/v1/documents/%s/blocks/%s/children/batch_delete" % (doc_id, parent_id),
            body={"start_index": start_index, "end_index": end_index},
        )
        return {"document_id": doc_id, "parent_block_id": parent_id,
                "deleted_range": [start_index, end_index]}

    def replace_block(self, ref, block_id, blocks):
        """把块替换成另一种类型/内容，位置不变（block_id 会变）。

        飞书没有「改 block_type」接口，所以 text → heading 只能这么做：
        在同一个下标插入新块，再删掉旧块。顺序和位置保住了，不会打乱全文。
        """
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        parent_id, index, _ = self._locate(doc_id, block_id)
        if not blocks:
            return self.delete_block(ref, block_id)

        inserted = self.insert_blocks(ref, blocks, index=index, parent_block_id=(
            None if parent_id == doc_id else parent_id))
        if not inserted.get("created"):
            raise FeishuError(-1, "替换失败：新块没有创建成功")
        # 插入可能引起下标漂移，删旧块前重新定位一次，避免删错块
        parent_now, index_now, _ = self._locate(doc_id, block_id)
        if parent_now != parent_id:
            raise FeishuError(-1, "替换失败：旧块 parent 发生变化，已停止删除以免误删")
        self._request(
            "DELETE",
            "/docx/v1/documents/%s/blocks/%s/children/batch_delete" % (doc_id, parent_id),
            body={"start_index": index_now, "end_index": index_now + 1},
        )
        return {
            "document_id": doc_id,
            "replaced_block_id": block_id,
            "parent_block_id": parent_id,
            "created": inserted.get("created") or [],
        }

    # ---- 组合编辑 ----

    def edit_document(self, ref, operations, dry_run=False):
        """按顺序执行一组编辑操作（update / patch / insert / delete / replace / delete_range）。

        每个操作都是独立的一次 API 调用；失败会停在该步并返回已完成的部分。
        dry_run=True 只校验参数与 block_id 是否存在，不写入。
        """
        doc = self._document_id(ref)
        doc_id = doc["document_id"]
        results = []
        for i, op in enumerate(operations):
            if not isinstance(op, dict):
                raise FeishuError(-1, "第 %d 个操作不是 dict" % i)
            kind = (op.get("op") or op.get("action") or "").strip().lower()
            try:
                if kind == "update":
                    if dry_run:
                        self.get_block(ref, op["block_id"])
                        results.append({"op": "update", "block_id": op["block_id"], "dry_run": True})
                    else:
                        results.append({"op": "update",
                                        **self.update_block(ref, op["block_id"],
                                                            text=op.get("text"),
                                                            spans=op.get("spans"),
                                                            style=op.get("style"),
                                                            block_style=op.get("block_style"))})
                elif kind == "patch":
                    if dry_run:
                        self.get_block(ref, op["block_id"])
                        results.append({"op": "patch", "block_id": op["block_id"], "dry_run": True})
                    else:
                        results.append({"op": "patch",
                                        **self.patch_block(ref, op["block_id"], op["find"],
                                                          op.get("replace", ""),
                                                          all=bool(op.get("all")))})
                elif kind == "insert":
                    if dry_run:
                        for b in op.get("blocks") or []:
                            self._block_payload(self._normalize_spec(b))
                        results.append({"op": "insert", "index": op.get("index", -1), "dry_run": True})
                    else:
                        results.append({"op": "insert",
                                        **self.insert_blocks(ref, op.get("blocks") or [],
                                                             index=op.get("index", -1),
                                                             parent_block_id=op.get("parent_block_id"))})
                elif kind == "append":
                    if dry_run:
                        results.append({"op": "append", "dry_run": True})
                    else:
                        results.append({"op": "append",
                                        **self.append_document(ref, op.get("blocks") or [])})
                elif kind == "delete":
                    if dry_run:
                        self._locate(doc_id, op["block_id"])
                        results.append({"op": "delete", "block_id": op["block_id"], "dry_run": True})
                    else:
                        results.append({"op": "delete", **self.delete_block(ref, op["block_id"])})
                elif kind == "delete_range":
                    if dry_run:
                        results.append({"op": "delete_range", "dry_run": True})
                    else:
                        results.append({"op": "delete_range",
                                        **self.delete_range(ref, op["start_index"], op["end_index"],
                                                            op.get("parent_block_id"))})
                elif kind == "replace":
                    if dry_run:
                        self._locate(doc_id, op["block_id"])
                        for b in op.get("blocks") or []:
                            self._block_payload(self._normalize_spec(b))
                        results.append({"op": "replace", "block_id": op["block_id"], "dry_run": True})
                    else:
                        results.append({"op": "replace",
                                        **self.replace_block(ref, op["block_id"],
                                                             op.get("blocks") or [])})
                else:
                    raise FeishuError(-1, "未知操作：%r（可用 update/patch/insert/append/"
                                         "delete/delete_range/replace）" % kind)
            except KeyError as exc:
                raise FeishuError(
                    -1,
                    "第 %d 个操作（%s）缺少参数 %s。已成功 %d 步"
                    % (i, kind or "?", exc, len(results)),
                ) from None
            except FeishuError as exc:
                raise FeishuError(
                    exc.code,
                    "第 %d 个操作（%s）失败：%s。已成功 %d 步"
                    % (i, kind or "?", exc.msg, len(results)),
                    status=exc.status,
                    path=exc.path,
                ) from None
        return {"document_id": doc_id, "dry_run": bool(dry_run),
                "applied": len(results), "results": results}


# ---------------------------------------------------------------------------
# 文本替换（保留内联样式）
# ---------------------------------------------------------------------------


def _replace_in_elements(elements, find, replace, all_matches=False):
    """在 text_run 列表里做查找替换，跨 run 也能匹配，插入文本继承起始 run 的样式。

    只改动匹配到的字符，其余 run 的内容和样式原样保留。
    """
    runs = [(idx, el["text_run"].get("content", ""))
            for idx, el in enumerate(elements) if "text_run" in el]
    full_text = "".join(c for _, c in runs)
    if find not in full_text:
        return 0

    matches = []
    start = 0
    while True:
        hit = full_text.find(find, start)
        if hit < 0:
            break
        matches.append((hit, hit + len(find)))
        start = hit + len(find)
        if not all_matches:
            break

    deleted = set()
    inserts = {}
    for a, b in matches:
        deleted.update(range(a, b))
        inserts[a] = inserts.get(a, "") + replace

    pos = 0
    for idx, content in runs:
        out = []
        for i, ch in enumerate(content):
            g = pos + i
            if g in inserts:
                out.append(inserts[g])
            if g not in deleted:
                out.append(ch)
        pos += len(content)
        new = "".join(out)
        if new != content:
            elements[idx]["text_run"]["content"] = new
    return len(matches)
