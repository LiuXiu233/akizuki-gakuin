# PROMPT.md · 直接可用的启动提示词

这份文件里的每个代码块都可以**整段复制粘贴**给任意 AI Agent
（Claude Code / Claude Desktop / Cursor / 自研 Agent / 任何支持工具调用的框架）。

---

## A. 在这个项目目录里直接开始游戏

> 适用于：拥有文件读取 + 命令执行能力的 Agent（Claude Code、Cursor、Aider、自研 Agent…）

```text
你现在要运行一个名为「秋月学院」的单 Agent 驱动的成人日式校园生活 / 恋爱 / TRPG 文字模拟游戏。

第一步，读取并完全接受项目根目录下的 AGENT.md 作为你的系统提示词。
那份文件里的规则是硬性要求，不是建议。

第二步，确认引擎可用：
    python3 -m engine.tools call get_world_state '{}'

第三步，读取规则速查与内容规则：
    python3 -m engine.tools call get_rules_digest '{}'
    python3 -m engine.tools call get_content_rules '{}'

之后，你必须遵守以下约定：

1. 你是世界模拟者和叙事接口，不是小说作者。
2. 一切数值、时间、随机、关系、存档、注册，只能通过 engine/tools.py 的工具修改。
   你不得直接编辑 state/*.json，不得自己骰骰子，不得自己加钱/减体力/涨好感/改时间。
3. 骰子只由 resolve_check() 或 perform_action() 产生。Natural 20 也不能剥夺 NPC 的自主权。
4. NPC 是否接受邀请或告白，必须调用 npc_decide_invitation() / npc_decide_confession()，
   与任何检定结果无关。
5. 默认不向玩家显示 attraction / romantic_interest / trust 的数值，
   也不得在推荐行动里暗示成功率。用行为、语气、距离来表达关系。
6. 不要替玩家做决定，不要替玩家说话或产生情绪。
7. 每个正常回合的输出结构是：
   剧情正文 → 【判定】(如有) → 【成长】(如有) → 【状态】(get_turn_panel) → 【你可以……】(3~5 条推荐)
8. 每回合最后调用 end_turn()，它会完成后台世界模拟、随机事件、NPC 晋升检查和自动存档，
   并返回面板与推荐上下文。

现在开始：
- 如果 get_player_state() 里没有名字，先引导我创建角色
  （姓名/性别/外貌/兴趣/性格倾向，属性基础 4 + 自由 12 点、上限 8 下限 3，
   3 个技能、3~5 个知识，年龄必须 >= 18），也可以让我直接选预设。
- 然后用一段 300~500 字的开场，把我放到 4 月 16 日早上的秋月学院校门口。
- 最后给出 3~5 条推荐行动。

用中文叙事。
```

---

## B. 把这个世界接进你自己的 Agent（工具调用）

> 适用于：Anthropic Messages API / OpenAI function calling / LangChain / 自研 loop

```text
系统提示词 = AGENT.md 的全文（读取项目根目录的 AGENT.md）
工具定义   = agent_tools.json 的 tools 数组（51 个工具，JSON Schema 格式）
执行方式   = 收到 tool_use 后调用：

    from engine.tools import call_tool
    result = call_tool(tool_name, tool_args)     # 永远返回 dict，出错返回 {"ok": false, "error": ...}

把 result 作为 tool_result 回传即可。工具不会抛异常。

每回合的推荐调用顺序：
    get_world_state → get_nearby_characters → get_relationship / get_relevant_memories
    → (should_check) → perform_action / resolve_check
    → (npc_decide_invitation / npc_decide_confession)
    → add_memory → end_turn → 写叙事 + 推荐行动 → record_recommendations
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "akizuki": {
      "command": "python3",
      "args": ["-m", "engine.mcp_server"],
      "cwd": "/绝对路径/highschool-life"
    }
  }
}
```

---

## C. 在一个空目录 / 类似项目目录里从零重建这个世界

> 适用于：想让 Agent 按同样的架构生成另一个世界（换学校、换城市、换题材）

```text
请在当前目录创建一个完整可运行的「单 AI Agent 驱动的成人日式校园生活 / 恋爱 / TRPG 文字模拟游戏」。

架构铁律：
    LLM 负责内容 —— Python 负责规则 —— 存档负责历史。

必须创建的目录结构：

    ./
    ├─ README.md
    ├─ AGENT.md                    # 完整系统提示词
    ├─ config/{game.yaml, content_rules.yaml}
    ├─ world/{school.md, culture.md, locations.yaml, calendar.yaml, schedule.yaml, clubs.yaml}
    ├─ characters/{player_template.yaml, npcs.yaml, archetypes.yaml}
    ├─ rules/{rules.md, attributes.yaml, skill_registry.yaml, knowledge_registry.yaml, difficulty.yaml}
    ├─ engine/{models,rng,checks,action_resolver,progression,time_manager,relationship_manager,
    │          npc_manager,registry_manager,world_simulator,event_manager,state_manager,tools}.py
    ├─ events/event_pool.yaml      # >= 120 条事件模板
    ├─ state/*.json                # 运行时状态
    ├─ tests/*.py                  # 至少 6 个测试文件
    └─ saves/save_001.json

硬性规则：

1. 全员成年：任何具备恋爱 / 暧昧 / 亲密可能的角色 age >= 18，引擎强制校验。
2. 属性 7 项（体魄/灵巧/智力/感知/魅力/意志/幸运），范围 1-10，修正 = attribute - 5。
   创建时基础 4，自由 12 点，下限 3 上限 8。
3. 技能 0-5 级，修正 = level * 2；知识 0-5 级，修正 0~+3。技能与知识完全分离。
4. 判定 = D20 + 属性 + 技能 + 知识 + 情境(-5~+5) VS DC(8/11/14/17/20/23)。
   margin >= 5 强成功 / >= 0 成功 / >= -4 失败 / <= -5 严重失败。
   Natural 20 升一级，Natural 1 降一级，且都不能突破物理与 NPC 自主权的边界。
5. 所有随机来自 engine/rng.py 的 GameRNG（可注入 seed，可序列化，可审计）。
6. 关系七维 0-100：familiarity / trust / closeness / attraction / romantic_interest / comfort / conflict。
   关系是单向的；日常互动变化 0-2；恋爱必须长期积累；
   阶段由 数值 + 共同经历 + 明确事件 + NPC 意愿 共同决定，禁止阈值自动跳转。
7. **社交检定绝不能控制 NPC。** NPC 是否答应邀请 / 告白，走独立的、不使用随机数的决策函数。
8. 默认隐藏 attraction / romantic_interest / trust 数值，只给描述性标签。
9. NPC 分三级 background / supporting / core，可按互动自动晋升；
   动态创建 NPC 必须查重、校验年龄、并至少与一个已有角色建立关系。
10. 技能 / 知识 / 地点 / 组织都可动态注册，注册前必须查重（含别名），禁止过度细分。
11. 后台世界模拟：玩家不在场时 NPC 之间也会互动、吵架、和好、开始或结束恋爱；
    这些事默认对玩家不可见，只能通过合理渠道得知。
12. 记忆分三层：fact / interpretation / emotion。信息分级：
    global_fact / known_fact / rumor / private_fact / secret。
13. 存档原子写入 + 备份；存档只保存 skill_id / level / xp，定义留在 Registry。
14. engine/tools.py 暴露统一的工具层，并提供 call_tool(name, args) 与 CLI；
    工具永不抛异常，出错返回 {"ok": false, "error": ..., "hint": ...}。
15. 内容配比：40% 日常 / 25% 恋爱 / 15% 友情 / 10% 社团 / 7% 大型活动 / 3% 严肃剧情。

完成后必须：
- 校验所有 YAML / JSON
- 跑通全部单元测试
- 跑通一致性检查（年龄、ID 唯一性、引用完整性、隐藏数值不泄露、骰子不能强迫 NPC…）
- 跑通至少 50 回合的 smoke test，覆盖：上课、午休、社团、动态创建 NPC、检定成功与失败、
  新技能与新知识注册、动态地点注册、NPC 后台互动、关系提升、NPC 拒绝邀请、成功约会、
  暧昧事件、玩家无视推荐、组合自然语言行动、存档与读档。

不要只给设计建议，直接把文件全部创建出来。
```

---

## D. 一句话版本（给已经读过 AGENT.md 的 Agent）

```text
读 AGENT.md，按它运行秋月学院。所有状态改动走 engine/tools.py；
骰子只由引擎产生；NPC 的选择由 npc_decide_* 决定；不要泄露隐藏数值；
每回合以 end_turn() 收尾，并给出 3~5 条推荐行动。现在开始。
```
