# Web 版架构与决策记录

> 本文件记录「把单机引擎包装成网页版」这次改造的全部关键决策。
> 每条决策都是和使用者确认过的，不要在没有新决策的情况下推翻它们。

## 0. 不变的东西

```
LLM 负责内容 —— Python 负责规则 —— 存档负责历史
```

`engine/` 的 13 个模块、51 个工具、141 条事件、七维关系、D20 判定、
年龄下限、隐藏数值不外泄、骰子不能控制 NPC —— **全部原样保留**。
CLI（`python3 -m engine.tools`）、MCP 服务器、128 个引擎单元测试、
一致性检查、smoke test 全部继续可用。

Web 层只做三件事：**传输、隔离、编排**。

---

## 1. 决策清单

| # | 决策 | 选择 |
|---|---|---|
| 1 | 前端框架 | Next.js（App Router）+ TypeScript，部署 Vercel |
| 2 | 后端 | FastAPI 包装引擎，部署到自有服务器 |
| 3 | LLM 调用位置 | 三选一可切换：浏览器直连 / Vercel 边缘代理 / 自建后端代理 |
| 4 | LLM 格式 | OpenAI 与 Anthropic 双格式适配 |
| 5 | 访问控制 | 服务端口令（env）+ 玩家可自带 key 覆盖 |
| 6 | 状态隔离 | 多用户 + 多存档：`data/users/<uid>/worlds/<wid>/` |
| 7 | Agent 架构 | YAML 可配置流水线；single / dual / multi 为三份预设 |
| 8 | 界面 | 双模式切换：沉浸模式 / 面板模式 |
| 9 | 页面范围 | 开局与存档管理、角色图鉴、地图与日程、回合日志与调试面板 |
| 10 | 流式 | SSE 流式与一次性返回都支持，可切换 |
| 11 | 图像类型 | 头像、立绘、场景背景、事件 CG，按需生成 + 缓存 |
| 12 | 图像存储 | 服务器磁盘，**按用户完全隔离** |
| 13 | 图像 API | OpenAI 兼容 + 自定义 HTTP 模板（可接 SD/ComfyUI/国内厂商） |
| 14 | 图像尺度 | 强制 SFW（文字叙事不受影响） |
| 15 | 交付 | 4 个里程碑连续做完，分别提交 |

---

## 2. 拓扑

```
                         ┌─────────────────────────────┐
   浏览器 ───────────────►│ Vercel (Next.js App Router) │
                         │  · 双模式 UI                 │
                         │  · /api/llm     LLM 边缘代理 │
                         │  · /api/proxy   后端转发     │◄── 解决 https→http 混合内容
                         └──────────────┬──────────────┘
                                        │
   浏览器 ─────────────────────────────►│ (也可直连)
                                        ▼
                         ┌─────────────────────────────┐
                         │ 你的服务器 (FastAPI)         │
                         │  /api/session   用户令牌     │
                         │  /api/worlds    存档 CRUD    │
                         │  /api/worlds/{id}/tools/*    │◄── 唯一的世界写入通道
                         │  /api/turn      流水线编排   │
                         │  /api/llm       LLM 代理     │
                         │  /api/images    文生图       │
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │ engine/  （完全未改动的规则）│
                         └─────────────────────────────┘
```

**混合内容注意**：Vercel 前端是 https，如果你的后端是 http，浏览器会拦截直连请求。
两个解法：给后端配 HTTPS（反代 / Cloudflare Tunnel），或让前端走 Next.js 的
`/api/proxy` 服务端转发（服务端请求不受混合内容限制）。前端设置里可切换。

---

## 3. 数据布局

```
data/                                    ← AKIZUKI_DATA_DIR，已 gitignore
└─ users/
   └─ <user_id 32hex>/
      ├─ profile.json                    用户偏好（不存任何 API key）
      └─ worlds/
         └─ <world_id 12hex>/            一个世界 = 一个存档
            ├─ meta.json                 名称/回合/日期/玩家名/流水线
            ├─ state/*.json              引擎的六个状态文件
            ├─ saves/*.json              世界内部的手动存档点
            └─ images/                   该世界的头像/立绘/场景/CG
```

用户令牌 = 存档钥匙，存在浏览器 localStorage，可导出到另一台设备继续玩。

### 引擎侧的两处最小改动

1. `StateManager(root, data_root)` —— 静态资料（只读、全局共享）与可变数据（每世界一份）分离。
2. `engine.tools` 增加 `use_session()` 上下文管理器 —— 用 `contextvars` 把 51 个工具
   绑定到当前请求的世界。CLI / MCP / 测试走的进程级全局会话路径完全不变。

并发：每个世界一把 `asyncio.Lock`，同一世界的请求串行；不同世界互不阻塞。
引擎调用跑在 threadpool 里，不阻塞事件循环。

---

## 4. API 一览（M1 已完成部分）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 服务器能力声明（永不含密钥） |
| GET | `/api/meta` | 静态世界数据：规则、地点、社团、技能、知识、预设 |
| GET | `/api/tools/schema` | 51 个工具的 JSON Schema |
| GET | `/api/lore/{topic}` | school / culture / rules / agent 文档 |
| GET | `/api/pipelines` | 可用的 Agent 流水线 |
| POST | `/api/session` | 换取 / 校验用户令牌 |
| GET | `/api/session` | 当前用户 + 存档列表 |
| PUT | `/api/session/settings` | 保存用户偏好 |
| GET/POST | `/api/worlds` | 列出 / 新建世界 |
| GET/PATCH/DELETE | `/api/worlds/{id}` | 快照 / 重命名 / 删除 |
| GET | `/api/worlds/{id}/export` | 导出存档 JSON |
| POST | `/api/worlds/import` | 导入存档 JSON |
| GET/POST | `/api/worlds/{id}/snapshots` | 世界内的手动存档点 |
| POST | `/api/worlds/{id}/restore` | 回到存档点 |
| GET | `/api/worlds/{id}/tools` | 工具清单 |
| POST | `/api/worlds/{id}/tools/{name}` | **调用工具（唯一写入通道）** |
| POST | `/api/worlds/{id}/tools` | 批量调用（组合行动） |
| POST | `/api/worlds/{id}/turn` | 跑一个完整回合（多 Agent 流水线，一次性返回） |
| POST | `/api/worlds/{id}/turn/stream` | 同上，SSE 流式 |
| POST | `/api/llm/chat` | LLM 代理（OpenAI / Anthropic 双格式，可流式） |
| POST | `/api/llm/verify` | 连通性自检（验 key / base_url / model） |

鉴权：`X-Access-Password`（配置了口令时必须）+ `X-User-Token`（世界相关接口必须）。

工具调用永远返回 HTTP 200，失败时 body 是 `{"ok": false, "error", "hint"}` ——
和引擎的约定一致，前端和 Agent 用同一套错误处理。

---

## 5. 多 Agent 流水线

流水线是 YAML，不是代码。`pipelines/` 下每个文件就是一种玩法：

| 预设 | 阶段 | 每回合模型调用 | 特点 |
|---|---|---|---|
| `single` | turn | 1~N | 带完整 AGENT.md，一个 Agent 干完所有事。最省 token |
| `dual` | act → npc_react(并行) → wrap | 3~6 | 主控管规则与旁白，NPC 各自扮演自己 |
| `multi` | intent → resolve → npc_react(并行) → narrate → direct →（可选）illustrate | 5~10 | 分工最细、信息隔离最严 |

阶段字段：

```yaml
- id: resolve            # 阶段 ID
  name: 裁判结算          # 显示名（前端进度条用）
  role: judge            # main / judge / npc / narrator / director / illustrator
  temperature: 0.2
  max_tokens: 1500
  tools: [perform_action, resolve_check, ...]   # 支持 "get_*" 通配与 "*"
  output: json           # json 时会做容错解析
  output_hint: facts     # 预置的输出格式说明
  produces: [facts]      # 写进黑板的产出类型
  inputs: [plan]         # 从黑板取哪些前序产出
  for_each: nearby_npcs  # 对每个在场 NPC 各跑一次
  parallel: true
  max_subjects: 4
  optional: true
  requires: images_enabled
  include_agent_md: true # 带完整 AGENT.md（单 Agent 模式用）
```

### 三条硬约束写死在执行器里

1. **NPC 扮演阶段永远拿不到任何写工具** —— 即使 YAML 里写了也会被过滤掉。
2. **`new_game` / `load_game` 对所有阶段不可见** —— 它们会替换整个存档。
3. **收尾时面板、世界状态、判定文本、成长文本全部从引擎重新取**，
   不采信模型的复述。模型只能决定"说什么"，不能决定"数值是多少"。

单个阶段失败（上游超时、格式错误）不会终止整个回合：
执行器发出 `stage_error` 事件后继续跑下一阶段，最终仍然返回权威面板。

### SSE 事件

```
turn_start → stage_start → [delta | tool_call | tool_result | dialogue | subject_start]* 
           → stage_end → … → turn_end
```

---

## 6. 里程碑

- [x] **M1 后端 API + 多用户多存档** — FastAPI、会话隔离、存档 CRUD、工具通道、26 个 API 测试
- [x] **M2 LLM 双格式适配 + 可配置流水线** — OpenAI/Anthropic 适配器、三份流水线预设、SSE、29 个测试
- [ ] **M3 Next.js 前端** — 双模式界面、四类页面、设置、流式渲染
- [ ] **M4 文生图** — 四类图像、按需生成、缓存、自定义模板、SFW 约束
