# 秋月学院 · Akizuki Gakuin

**单 AI Agent 驱动的成人日式校园生活 / 恋爱 / TRPG 文字模拟游戏引擎**

> 一个玩家可以长期生活、认识人、恋爱、学习、成长，
> 并让世界随着自己的经历不断扩张的成人日式校园模拟世界。

```
LLM 负责内容 —— Python 负责规则 —— 存档负责历史
```

---

## 这是什么

一所位于海边小城的**高等综合学院**。学生全部已经成年（`age >= 18`），
但校园保留了全部日式元素：班级、制服、鞋柜、天台、社团、文化祭、体育祭、修学旅行、
放学后的商店街和便利店。

游戏**没有主线，没有唯一正确路线，没有必须攻略的角色**。你可以：

今天去哪里 · 和谁交流 · 加入哪个社团 · 是否谈恋爱 · 和谁约会 · 如何安排学习 ·
培养什么技能 · 学习什么知识 · 如何度过周末 · 是否参加学校活动 · 是否扩大社交圈 ·
是否探索学校和城市 · 是否发展长期关系 —— 或者完全不谈恋爱。

**NPC 不是攻略奖励。** 他们是有自主性的成年人：可以对你无感、只当朋友、喜欢别人、
已经在交往、拒绝你，也可以主动追求你。世界在你不在场时照常运转。

---

## 快速开始（网页版）

```bash
# 1. 后端（规则引擎 + 多 Agent 编排 + 文生图）
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
cp .env.example .env                   # 按需填口令 / API key，全留空也能跑
.venv/bin/python -m server             # http://localhost:8000

# 2. 前端
cd web && npm install && npm run dev   # http://localhost:3000
```

打开 http://localhost:3000 →「开始新的一年」→ 创建角色 →
到「设置 → 模型」填入你的 API key（OpenAI 兼容或 Anthropic 都行）→ 开始输入你想做的事。

部署：前端丢给 Vercel（Root Directory 设为 `web`），后端放你自己的服务器。
详见 [`web/README.md`](web/README.md) 与 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 快速开始（纯引擎 / CLI）

```bash
python3 -m pip install pyyaml          # 引擎唯一的第三方依赖

python3 -m engine.tools call get_world_state '{}'
python3 -m engine.tools list
python3 -m engine.tools call new_game '{"seed": 20250416}'
python3 -m engine.tools call create_player '{"name":"佐藤悠","age":19,"preset":"preset_artist"}'
python3 -m engine.tools call get_turn_panel '{}'
```

然后把 **`AGENT.md`** 交给你的 LLM Agent 当系统提示词，把
**`PROMPT.md` 的 A 段**发给它，游戏就开始了。CLI、MCP、单元测试这条路径
在网页版加入后**完全没有改变**。

### 验证

```bash
python3 -m unittest discover -s tests -t .        # 引擎 128 个测试（无 fastapi 时自动跳过后端测试）
.venv/bin/python -m unittest discover -s tests -t .  # 全部 203 个（含后端 / Agent / 图像）
python3 scripts/verify_consistency.py             # 23 项世界一致性检查
python3 scripts/smoke_test.py                     # 50 回合 smoke test（27 项覆盖）
npm --prefix web run build                        # 前端构建
```

---

## 网页版做了什么

| 能力 | 说明 |
|---|---|
| 多 Agent 流水线 | `single` / `dual` / `multi` 三种预设，YAML 定义，界面一键切换 |
| 双格式 LLM | OpenAI 兼容与 Anthropic，均支持工具调用与流式 |
| 三种调用位置 | 自建后端 / Vercel 边缘 / 浏览器直连，对应三种信任模型 |
| 双模式界面 | 沉浸模式（立绘 + 对话框）与面板模式（三栏数据） |
| 多用户多存档 | 用户令牌即存档钥匙，可导出导入，跨设备继续 |
| 文生图 | 头像 / 立绘 / 场景 / 事件 CG，按需生成 + 缓存，不配置就自动关闭 |
| 流式与非流式 | SSE 实时显示阶段进度与逐字叙事，也可以等整回合一次性返回 |

**引擎没有为网页版做任何妥协**：规则、判定、关系、存档全部仍在 `engine/` 里，
Web 层只做传输、隔离与编排。隐藏的关系数值从来不会离开服务器。

## 给 AI Agent 的接入方式

| 方式 | 命令 / 入口 |
|---|---|
| Python 直接调用 | `from engine.tools import perform_action, end_turn` |
| 统一分发入口 | `from engine.tools import call_tool; call_tool(name, args)` |
| CLI | `python3 -m engine.tools call <tool> '<json>'` |
| JSON Schema | `agent_tools.json`（51 个工具）/ `python3 -m engine.tools schema` |
| MCP (stdio) | `python3 -m engine.mcp_server` |
| 项目自述 | `agent_manifest.json` |

工具**永不抛异常**，出错返回 `{"ok": false, "error": "...", "hint": "..."}`。

---

## 目录结构

```
highschool-life/
├─ README.md                 本文件
├─ AGENT.md                  ★ 完整系统提示词（Agent 的行为宪法）
├─ PROMPT.md                 ★ 可直接复制粘贴的启动提示词
├─ agent_tools.json          工具 JSON Schema（机器可读）
├─ agent_manifest.json       项目自述（机器可读）
│
├─ config/
│  ├─ game.yaml              主配置：时间/成长/关系/事件/存档/可见性
│  └─ content_rules.yaml     内容硬规则与配比表
│
├─ world/
│  ├─ school.md              学院设定与校园布局
│  ├─ culture.md             生活文化、恋爱在这个世界里的样子
│  ├─ locations.yaml         42 个地点 + 移动时间表
│  ├─ calendar.yaml          学年日历（30 个节点）+ 天气表
│  ├─ schedule.yaml          作息、课表、NPC 默认日程、行为耗时
│  └─ clubs.yaml             15 个社团
│
├─ characters/
│  ├─ player_template.yaml   玩家模板 + 创建规则 + 5 个预设
│  ├─ npcs.yaml              16 名学生 + 5 名教师 + 25 条初始 NPC↔NPC 关系
│  └─ archetypes.yaml        15 个角色原型（只是骨架，禁止套模板）
│
├─ rules/
│  ├─ rules.md               规则手册
│  ├─ attributes.yaml        7 项属性
│  ├─ skill_registry.yaml    27 个基础技能
│  ├─ knowledge_registry.yaml 19 个基础知识
│  └─ difficulty.yaml        DC 表 / 情境修正 / 何时不骰
│
├─ engine/                   规则引擎（纯标准库 + PyYAML）
│  ├─ models.py              数据模型、枚举、校验
│  ├─ rng.py                 唯一随机源（可注入 seed、可序列化、可审计）
│  ├─ checks.py              D20 判定
│  ├─ progression.py         XP / 等级 / 防刷
│  ├─ time_manager.py        时间、日程块、移动、跨日结算
│  ├─ relationship_manager.py 七维关系、阶段、NPC 自主决策
│  ├─ npc_manager.py         NPC 定义/运行时/日程/动态创建/晋升
│  ├─ registry_manager.py    技能/知识/地点/组织/NPC 注册与查重
│  ├─ event_manager.py       事件候选、冷却、权重、随机触发
│  ├─ world_simulator.py     后台世界模拟（NPC↔NPC）
│  ├─ action_resolver.py     行动结算流水线
│  ├─ state_manager.py       原子存档、备份、记忆库
│  ├─ tools.py               ★ Agent 唯一接口（51 个工具）
│  └─ mcp_server.py          MCP stdio 服务器
│
├─ events/event_pool.yaml    141 条事件模板
├─ state/*.json              运行时状态（世界/角色/关系/记忆/事件/注册表）
├─ saves/save_001.json       存档槽
├─ server/                   FastAPI 后端（LLM 适配 / 多 Agent 编排 / 文生图）
├─ pipelines/                Agent 流水线定义（single / dual / multi）
├─ web/                      Next.js 前端（部署 Vercel）
├─ tests/                    203 个单元测试
├─ docs/example_turn.md      一个真实回合的完整示例（含反面教材）
└─ scripts/
   ├─ verify_consistency.py  23 项一致性检查
   ├─ smoke_test.py          50 回合 smoke test
   └─ export_tools.py        导出机器可读清单
```

---

## 核心规则速览

### 属性（1–10，修正 = `attribute - 5`）

体魄 physique · 灵巧 agility · 智力 intellect · 感知 perception ·
魅力 charm · 意志 willpower · 幸运 luck

创建时：基础 4，自由分配 12 点，下限 3 上限 8。
**Charm 高不等于任何人必须喜欢你。**

### 判定

```
D20 + (属性-5) + (技能等级×2) + 知识修正(0~+3) + 情境(-5~+5)  VS  DC
DC: 8 / 11 / 14 / 17 / 20 / 23
margin >= 5 强成功 | >= 0 成功 | >= -4 失败 | <= -5 严重失败
Natural 20 升一级，Natural 1 降一级
```

**结果毫无悬念的事不骰。**
**NPC 是否答应，永远不骰。**

### 关系（七维 0–100，单向）

```
familiarity  trust  closeness  attraction  romantic_interest  comfort  conflict
```

同样的高分意义完全不同：

| 组合 | 含义 |
|---|---|
| 熟悉85 信任80 亲近90 吸引20 恋爱10 自在95 冲突5 | 非常亲密的朋友，但没有恋爱吸引力 |
| 熟悉65 信任45 亲近50 吸引85 恋爱75 自在55 冲突15 | 明显互相吸引，但信任不足，会犹豫和退缩 |

阶段：`stranger → acquaintance → friend → close_friend → ambiguous → dating → relationship`
（另有 `strained` / `former_partner`），由 **数值 + 共同经历 + 明确事件 + NPC 意愿** 共同决定。

**玩家永远看不到数值**，只能从对话、行为、语气、主动联系的频率、
是否愿意独处、是否分享私事来判断。

### 技能 / 知识

- **技能 = 会做**（27 个基础，0–5 级）
- **知识 = 知道**（19 个基础，0–5 级）
- 完全分离。知识的主要价值是解锁信息、对话、识别与推荐，不是加值。
- 两者都可以在游玩中动态注册，但**必须先查重**，且**禁止过度细分**
  （煎鸡蛋 / 炒饭 / 咖喱 → 一律 `cooking`）。

---

## 设计约束（为什么这样写）

| 约束 | 原因 |
|---|---|
| 骰子不能控制 NPC | NPC 是成年人，不是需要被攻克的关卡 |
| 日常互动只涨 0–2 点 | 恋爱需要过程，不是进度条 |
| 关系是单向的 | 你喜欢她和她喜欢你是两件事 |
| 隐藏数值不外泄 | 你应该看她的眼睛，不是看数字 |
| 后台世界持续运行 | 你不在的时候，别人也在生活 |
| 新 NPC 必须认识别人 | 世界不是围绕玩家搭建的 |
| 失败也给经验、也推进剧情 | 搞砸了也是故事 |
| 拒绝之后关系不归零，但会变 | 这才是人际关系 |

---

## 许可与内容分级

面向成年用户的虚构创作。世界中不存在未成年角色，引擎在数据层强制拒绝
创建 `age < 18` 的角色。所有亲密内容以成年、双方自愿、明确同意、
尊重个人边界为前提。
