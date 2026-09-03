# 秋月学院 · 规则手册 (rules.md)

> **一句话原则：LLM 负责内容，Python 负责规则，存档负责历史。**

---

## 0. 权限边界（最重要）

| 谁 | 可以 | 不可以 |
|---|---|---|
| LLM Agent | 创造人物、地点、事件、对话、剧情、兴趣、氛围 | 直接改 JSON、自己骰骰子、自己加钱/减体力/涨技能/涨好感、自己改时间、自己瞬移角色、自己决定 XP |
| Python 引擎 | 所有数值、时间、随机、关系、存档、注册、校验 | 写剧情 |
| 存档 | 记录发生过的一切 | —— |

所有状态变更 **只能** 通过 `engine/tools.py` 的工具函数。任何绕过都是 bug。

---

## 1. 属性

范围 **1–10**，7 项：体魄 physique / 灵巧 agility / 智力 intellect / 感知 perception / 魅力 charm / 意志 willpower / 幸运 luck。

```
属性修正 = attribute - 5
```

创建时：基础 4，自由分配 12 点，下限 3，上限 8。

**Charm 高 ≠ 任何人必须喜欢玩家。**

---

## 2. 技能与知识

- **技能 = 会做**，等级 0–5，`技能修正 = level × 2`。
- **知识 = 知道**，等级 0–5，`知识修正 = 0 ~ +3`（见 knowledge_registry.meta.modifier_table）。
- 两者完全分离。知道菜谱不代表做得好；会做菜不代表懂食材史。
- 知识的主要作用不是加值，而是：解锁描述细节、解锁对话、识别事物、跳过简单判定、影响推荐行动。

**动态扩展**：真正独立的新能力 → `register_skill()`；真正独立的新领域 → `register_knowledge()`。
创建前必须 `get_registry()` 查重（含 aliases）。禁止 `photography` / `camera_skill` / `摄影技巧` 这样的同义重复。
禁止过度细分：煎鸡蛋、炒饭、咖喱 → 一律 `cooking`。

---

## 3. 检定

```
D20 + 属性修正 + 技能修正 + 知识修正 + 情境修正(-5~+5)   VS   DC
```

固定 DC：

| 难度 | DC |
|---|---|
| very_easy 很容易 | 8 |
| easy 容易 | 11 |
| normal 普通 | 14 |
| hard 困难 | 17 |
| very_hard 很困难 | 20 |
| extreme 极困难 | 23 |

成功等级：

```
margin = total - DC
margin >= 5   → strong_success
margin >= 0   → success
margin >= -4  → failure
margin <= -5  → major_failure
```

- **Natural 20**：结果等级 +1（不能完成物理不可能的事，不能剥夺 NPC 自主权）。
- **Natural 1**：结果等级 -1（不能凭空制造荒谬灾难）。

调用：

```python
resolve_check(actor_id, action_type, attribute,
              skill=None, knowledge=None,
              difficulty="normal", situational_modifiers=None)
```

返回真实结果，Agent **只能**基于这个结果继续叙述。

---

## 4. 什么时候不骰

不骰：结果毫无悬念的事（买水、走去教室、和熟人打招呼）。
骰：存在**合理失败概率**且结果会影响世界的事。
**永远不骰**：NPC 是否答应邀请 / 告白 / 亲密行为——那不是检定，那是 NPC 的选择。

---

## 5. 社交检定的铁律

> 即使 Natural 20，社交检定也 **只能** 决定玩家表达得是否自然、是否留下好印象、气氛是否变好。
> NPC 是否答应，永远由人设、关系、当前情绪、个人边界、恋爱倾向和过去经历决定。

引擎层面：`resolve_check` 的返回值里带有 `npc_autonomy_note`，社交类检定不会产生任何强制 NPC 的字段。
NPC 的决定统一走 `relationship_manager.npc_decide_invitation()` / `npc_decide_confession()`。

---

## 6. 随机

所有随机来自 `engine/rng.py` 的 `GameRNG`。
测试用固定种子 `GameRNG(seed=42)`，正式游戏用系统随机源。
LLM 不得自己骰、不得重骰、不得为剧情作弊。

---

## 7. 失败也推进剧情

失败 ≠ 什么都没发生：

- 邀请失败 → 对方那天真的有安排（而且这件安排是真实存在的）。
- 摄影失败 → 照片构图不好，但拍到了别的东西。
- 料理失败 → 味道普通，有人还是吃完了。
- 表演失败 → 因为紧张漏了一小节，台下有人替你紧张。

---

## 8. 成长

- 维护 `skill_xp` / `knowledge_xp`。
- 升级门槛：`[0, 100, 300, 700, 1400, 2600]`。
- 来源：使用、学习、被 NPC 教、社团、阅读、事件、课程、练习。
- 失败也给经验（40%）。
- 防刷：
  - `daily_training_limit`：单日技能 XP 上限 60，知识 45。
  - `diminishing_returns`：同一技能当日第 4 次起收益 ×0.5 递减。
  - `difficulty_requirement`：等级越高，越低难度的行动给不了经验（Lv3 需要 normal 以上）。

---

## 9. 时间

时间是核心资源，**所有推进必须由代码执行** (`advance_time`)。

| 行为 | 分钟 |
|---|---|
| 简单聊天 | 2–5 |
| 正常聊天 | 10–30 |
| 深谈 | 30–60 |
| 午餐 | 20–45 |
| 约会 | 60–300 |
| 社团 | 60–180 |
| 学习 | 玩家决定 |
| 移动 | 按 locations.travel 计算 |

超过 26:00（次日 02:00）强制睡眠。

---

## 10. 关系

七维，范围 0–100：

```
familiarity  熟悉度   —— 见过多少次、了解多少
trust        信任     —— 敢不敢把事情交给你
closeness    亲近     —— 心理距离
attraction   吸引     —— 身体与气质层面的吸引力
romantic_interest 恋爱兴趣 —— 是否把你当作恋爱对象考虑
comfort      自在     —— 和你相处累不累
conflict     冲突     —— 未解决的摩擦
```

同样的高分组合意义完全不同：

- `familiarity 85 / trust 80 / closeness 90 / attraction 20 / romantic_interest 10 / comfort 95 / conflict 5`
  → 非常亲密的朋友，但没有恋爱吸引力。**不要把它写成暧昧。**
- `familiarity 65 / trust 45 / closeness 50 / attraction 85 / romantic_interest 75 / comfort 55 / conflict 15`
  → 明显互相吸引，但信任不足。**这会产生犹豫、试探和退缩。**

变更只能通过 `apply_relationship_event()`，引擎会根据 NPC 人格、当前关系、重复度、情绪、场合调整实际变化量。
日常互动的变化 **非常小**（0–2）。恋爱必须长期积累。

### 关系阶段（后台状态）

```
stranger → acquaintance → friend → close_friend → ambiguous → dating → relationship
                                         ↘ strained ↘ former_partner
```

阶段 **不由数值单独决定**，而是 `relationship_values + shared_history + explicit_events + NPC choice` 四者共同决定。

### 玩家看不到数值

默认 `debug_relationship_numbers: false`。玩家只能看到描述性标签（"关系不错"、"似乎有些暧昧"、"正在交往"），
以及通过叙事观察：对方是否主动联系、是否愿意独处、是否分享私事、回消息的速度。

**禁止输出**：`【凛的好感 +2】`。
**应该输出**：`她似乎没有急着离开，而是继续站在你旁边聊了一会儿。`

---

## 11. NPC

三级：`background` → `supporting` → `core`，通过 `promote_npc()` 或自动晋升检查升级。

- **background**：id / 姓名 / 年龄 / 身份 / 少量人格标签
- **supporting**：+ 属性 / 技能 / 知识 / 日程 / 关系 / 记忆 / 兴趣 / 目标
- **core**：完整角色系统（见 characters/npcs.yaml 的字段表）

**NPC 不是攻略奖励。**NPC 可以对玩家无感、只当朋友、喜欢别人、已经在交往、拒绝玩家、也可以主动追求玩家。

动态创建 `create_npc()` 会强制检查：年龄 ≥ 18、ID 与姓名查重、初始关系、社交网络（新 NPC 必须至少认识一个已有 NPC）。

---

## 12. 记忆

每条记忆必须区分三层：

```yaml
fact: "玩家把伞借给了她。"
interpretation: "她觉得这个人似乎挺细心。"
emotion: "开心，稍微有点在意。"
```

这三者不是一回事。事实不会变，解释会随关系变化，情绪会衰减。

信息可见性分级：`global_fact` / `known_fact` / `rumor` / `private_fact` / `secret`。
**NPC 只能说出自己有渠道知道的事。**

---

## 13. 事件

- 静态模板在 `events/event_pool.yaml`（120+ 条）。
- 触发由代码决定：`roll_random_event()` 综合日期、时间、地点、在场角色、关系、恋爱关系、冷却、权重、近期事件、玩家状态、天气。
- 代码返回候选，**LLM 只负责叙事**。
- 重大情感事件必须低频（`major_event_min_interval_days: 6`）。

---

## 14. 推荐行动

每个正常回合结束必须给 **3–5 条**推荐行动，来自 `get_action_context()` 提供的真实上下文。

- 尽量覆盖多个类型：社交 / 恋爱 / 兴趣 / 学习 / 探索 / 休息。
- 有合理恋爱机会时可以提高恋爱行动比例；**没有机会时绝不硬塞**（在考试中、极度疲惫、正在重要社团事件时不要出现"找人约会"）。
- 记录 `recent_recommendations`，重复项降低权重。
- **绝不泄露隐藏信息，绝不暗示成功率。**

推荐只是建议，玩家永远可以输入任意自然语言，包括组合行动。

---

## 15. 世界不冻结

`simulate_background_world()` 每回合运行，模拟 core / supporting NPC 的日程、目标、NPC↔NPC 关系（包括友情、争执、和好、开始约会、分手、对别人产生好感）。
玩家不一定立刻知道——必须通过合理渠道（看到、听说、被告知、传闻）得知。

---

## 16. 存档

- 原子写入 + 备份（保留 5 份）。
- 存档只保存 `skill_id / level / xp`，定义留在 Registry，避免重复。
- `save_game()` / `load_game()`；每回合自动存 `autosave`。
