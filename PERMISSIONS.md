# 飞书应用权限清单（feishu-mcp）

> 应用：`cli_aa1ab7fbdd795d01`（自建应用，使用 `tenant_access_token`）
> 状态依据：`scope_check.py` 逐项实测（2026-10-28），不是抄文档
> 复验命令：`uv run --directory . python scope_check.py`

## 1. 已具备（实测通过）

| 能力 | 相关 scope |
| --- | --- |
| 读文档块 | `docx:document:readonly` |
| 改文档块（原地更新/插入/删除） | `docx:document:write_only` |
| 解析知识库节点 | `wiki:node:read` |
| 以应用身份发消息 | `im:message:send_as_bot` |
| 群组信息 | `im:chat` / `im:chat:readonly` |
| 通讯录读取 | contact 相关（已通） |
| 上传图片/文件到 IM | `im:resource` |

## 2. 缺口（按优先级）

### P0 —— 直接影响“图文文档”能力

| 能力 | 需要的 scope | 说明 |
| --- | --- | --- |
| 读文档里的图片本体 | `docs:document.media:download` | 现在只能拿到 token/宽高/caption，看不到画面；开通后可把图片作为 MCP image content 返回给多模态模型 |
| 往文档插图片 / 画图方案 B | `docs:document.media:upload` | 渲染成 PNG 后上传并插入图片块 |
| 画板里画图（方案 A） | `board:whiteboard:node:create` | 用画板节点 API 生成方块+连线，飞书原生可编辑 |
| 读画板内容 | `board:whiteboard:node:read` | 校验生成结果、导出已有画板 |

> 注：流程图/UML 图块（block_type 21）飞书不开放创建（`1770029`），加任何权限都绕不过，只能走画板或图片。

### P1 —— 文档全生命周期

| 能力 | 需要的 scope | 说明 |
| --- | --- | --- |
| 新建文档 | `docx:document:create`（或 `docx:document`） | 现在只能编辑已有文档，不能从零建一篇写进去 |
| 列出/定位云文档 | `drive:drive:readonly` | 按文件夹列文档、找 token，省掉手工贴链接 |
| 复制文档 | `docs:document:copy` | 以模板建新文档 |
| 删除文档 | `space:document:delete` | 清理临时文档 |
| 知识库空间列表 / 建页 / 移页 | `wiki:wiki` | 一次覆盖 `wiki:wiki:readonly` / `wiki:node:create` / `wiki:node:move` |
| 读写电子表格 | `sheets:spreadsheet` | 读单元格、批量写数据 |
| 新建电子表格 | `sheets:spreadsheet:create` | |
| 读写多维表格 | `bitable:app` | 记录增删改查 |
| 新建多维表格 | `base:app:create` | |

### P2 —— 周边能力（按需）

| 能力 | 需要的 scope | 说明 |
| --- | --- | --- |
| 读任务 | `task:task:read` | 把待办同步进文档 |
| 建/改任务 | `task:task:write` | |
| 删任务 | `task:task:delete` | |
| 读日历 | `calendar:calendar:readonly` | 会议纪要 / 日程 |
| 写日历 | `calendar:calendar` | |
| 图片 OCR 转文字 | `optical_char_recognition:image` | 截图、扫描件里的字，图不能看但能识字 |

## 3. 一次性开通清单（复制到权限管理）

最小够用（16 个，已去掉互相覆盖的冗余项）：

```
docs:document.media:upload,drive:drive:readonly,board:whiteboard:node:create,board:whiteboard:node:read,docx:document:create,docs:document:copy,space:document:delete,wiki:wiki,sheets:spreadsheet,bitable:app,task:task:read,task:task:write,task:task:delete,calendar:calendar:readonly,calendar:calendar,optical_char_recognition:image
```

省事版（11 个，用宽权限换数量）：

```
drive:drive,board:whiteboard:node:create,board:whiteboard:node:read,docx:document:create,wiki:wiki,bitable:app,task:task:read,task:task:write,task:task:delete,calendar:calendar,optical_char_recognition:image
```

覆盖关系（避免重复申请）：

- `drive:drive:readonly` 已包含“下载素材”和“读电子表格”，所以不用再单独申请 `docs:document.media:download`
- `sheets:spreadsheet` 已包含“创建电子表格”，`bitable:app` 已包含“创建多维表格”，不用再申请 `sheets:spreadsheet:create` / `base:app:create`
- `wiki:wiki` 一项顶掉 `wiki:wiki:readonly` + `wiki:node:create` + `wiki:node:move`
- `calendar:calendar` 一项顶掉 `calendar:calendar:readonly`
- `drive:drive`（查看、评论、编辑和管理云空间中所有文件）一项顶掉：素材上传+下载、文档列表、复制、删除、电子表格读写。权限面更大，管理员是否放行看你们规范

## 4. 开通步骤

1. 进开发者后台 → 应用 `cli_aa1ab7fbdd795d01` → 权限管理，按清单逐个开通。
   也可直接点带 `q=` 的链接，例如：
   - [docs:document.media:download](https://open.feishu.cn/app/cli_aa1ab7fbdd795d01/auth?q=docs:document.media:download&op_from=openapi&token_type=tenant)
   - [docs:document.media:upload](https://open.feishu.cn/app/cli_aa1ab7fbdd795d01/auth?q=docs:document.media:upload&op_from=openapi&token_type=tenant)
   - [board:whiteboard:node:create](https://open.feishu.cn/app/cli_aa1ab7fbdd795d01/auth?q=board:whiteboard:node:create&op_from=openapi&token_type=tenant)
   - [board:whiteboard:node:read](https://open.feishu.cn/app/cli_aa1ab7fbdd795d01/auth?q=board:whiteboard:node:read&op_from=openapi&token_type=tenant)
2. **创建版本并发布**。不发版，tenant token 不会带上新权限，这一步最容易漏。
3. 若开了「应用权限审批」，让管理员在管理后台通过；敏感/高级权限通常需要审批。
4. 重启 MCP 进程（`tenant_access_token` 有 2 小时缓存，重启最省事）。
5. 跑 `scope_check.py` 确认，缺的项会直接列出来。

## 5. 验证

```bash
uv run --directory . python scope_check.py
```

输出每项 `✅ 已具备` / `⛔ 缺失 + 需要的 scope`，退出码 0 表示全通过。
所有探测都用假 ID 或空参数，**不会改你的文档/表格**。

## 6. 备注

- 本清单只针对 `tenant_access_token`（应用身份）。OKR、个人日历、个人任务等需要 `user_access_token` 的能力不在内。
- 云文档搜索（`/suite/docs-api/search/object`）返回 `99991672 No permission` 但不列 scope，无法确定具体权限，未列入清单；开通 `drive:drive` 后可再试。
- 权限名以飞书控制台为准，scope ID 以本文为准（都是从接口报错里取的原值）。
