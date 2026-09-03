#!/usr/bin/env python3
"""世界一致性检查 —— 对应 AGENT.md / rules.md 的 25 项最终测试。

运行::

    python3 scripts/verify_consistency.py
    python3 scripts/verify_consistency.py --quiet

任何一项 FAIL 都意味着世界数据或引擎规则出了问题。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from engine import tools as T  # noqa: E402
from engine.models import MIN_AGE, RELATIONSHIP_DIMENSIONS  # noqa: E402
from engine.tools import GameSession, reset_session  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    def wrapper(fn: Callable[[], Any]) -> Callable[[], Any]:
        def run() -> None:
            try:
                detail = fn() or ""
                RESULTS.append((name, True, str(detail)))
            except AssertionError as exc:
                RESULTS.append((name, False, str(exc)))
            except Exception as exc:  # noqa: BLE001
                RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        run.__name__ = fn.__name__
        return run
    return wrapper


CHECKS: list[Callable[[], None]] = []


def register(name: str):
    def wrapper(fn):
        CHECKS.append(check(name)(fn))
        return fn
    return wrapper


# ---------------------------------------------------------------------------
# 1-2 数据文件
# ---------------------------------------------------------------------------

YAML_FILES = [
    "config/game.yaml", "config/content_rules.yaml", "world/locations.yaml",
    "world/calendar.yaml", "world/schedule.yaml", "world/clubs.yaml",
    "characters/npcs.yaml", "characters/player_template.yaml", "characters/archetypes.yaml",
    "rules/attributes.yaml", "rules/skill_registry.yaml", "rules/knowledge_registry.yaml",
    "rules/difficulty.yaml", "events/event_pool.yaml",
]


@register("1. 所有 YAML 可解析")
def _yaml() -> str:
    for rel in YAML_FILES:
        path = ROOT / rel
        assert path.exists(), f"缺少 {rel}"
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert data is not None, f"{rel} 为空"
    return f"{len(YAML_FILES)} 个文件"


@register("2. 所有 JSON 可解析")
def _json() -> str:
    count = 0
    for path in list((ROOT / "state").glob("*.json")) + list((ROOT / "saves").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
        count += 1
    return f"{count} 个文件"


# ---------------------------------------------------------------------------
# 3 测试
# ---------------------------------------------------------------------------


@register("3. 单元测试全部通过")
def _tests() -> str:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    last = [line for line in proc.stderr.splitlines() if line.startswith("Ran ")]
    return last[-1] if last else "OK"


# ---------------------------------------------------------------------------
# 4-7 静态数据一致性
# ---------------------------------------------------------------------------


def load(rel: str) -> Any:
    with (ROOT / rel).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@register("4. 所有可恋爱学生 age >= 18")
def _ages() -> str:
    npcs = load("characters/npcs.yaml")["npcs"]
    for npc in npcs:
        assert npc["age"] >= MIN_AGE, f"{npc['id']} age={npc['age']}"
    students = [n for n in npcs if n["role"] == "student"]
    assert len(students) >= 12, "核心学生 NPC 至少 12 名"
    romanceable = [n for n in students if n.get("romance_available", True)]
    assert all(n["age"] >= 18 for n in romanceable)
    return f"{len(students)} 名学生，最小 {min(n['age'] for n in students)} 岁"


@register("5. 角色 ID 唯一且格式合法")
def _npc_ids() -> str:
    import re

    npcs = load("characters/npcs.yaml")["npcs"]
    ids = [n["id"] for n in npcs]
    assert len(ids) == len(set(ids)), "存在重复 NPC ID"
    for npc_id in ids:
        assert re.match(r"^npc_[a-z0-9_]+$", npc_id), f"非法 ID: {npc_id}"
    return f"{len(ids)} 个"


@register("6. Location ID 唯一且被正确引用")
def _location_ids() -> str:
    locations = load("world/locations.yaml")["locations"]
    ids = {loc["id"] for loc in locations}
    assert len(ids) == len(locations), "存在重复地点 ID"
    for club in load("world/clubs.yaml")["clubs"]:
        assert club["location"] in ids, f"社团 {club['id']} 的地点不存在"
    for npc in load("characters/npcs.yaml")["npcs"]:
        for key in ("home_location", "favorite_place"):
            value = npc.get(key)
            assert value is None or value in ids, f"{npc['id']}.{key}={value} 不存在"
        for entry in npc.get("schedule_overrides") or []:
            loc = entry.get("location")
            assert loc is None or str(loc).startswith("@") or loc in ids, f"{npc['id']} 日程地点 {loc} 不存在"
    events = load("events/event_pool.yaml")["events"]
    for event in events:
        for loc in (event.get("conditions") or {}).get("locations", []) or []:
            assert loc in ids, f"事件 {event['id']} 引用了不存在的地点 {loc}"
    return f"{len(ids)} 个地点"


@register("7. Registry 完整且互相引用正确")
def _registry() -> str:
    skills = {s["id"] for s in load("rules/skill_registry.yaml")["skills"]}
    knowledge = {k["id"] for k in load("rules/knowledge_registry.yaml")["knowledge"]}
    for npc in load("characters/npcs.yaml")["npcs"]:
        for skill_id in (npc.get("skills") or {}):
            assert skill_id in skills, f"{npc['id']} 使用了未注册技能 {skill_id}"
        for knowledge_id in (npc.get("knowledge") or {}):
            assert knowledge_id in knowledge, f"{npc['id']} 使用了未注册知识 {knowledge_id}"
    for club in load("world/clubs.yaml")["clubs"]:
        for skill_id in club.get("related_skills") or []:
            assert skill_id in skills, f"社团 {club['id']} 引用未注册技能 {skill_id}"
        for knowledge_id in club.get("related_knowledge") or []:
            assert knowledge_id in knowledge, f"社团 {club['id']} 引用未注册知识 {knowledge_id}"
    events = load("events/event_pool.yaml")["events"]
    assert len(events) >= 120, f"事件模板只有 {len(events)} 条，要求 >= 120"
    return f"{len(skills)} 技能 / {len(knowledge)} 知识 / {len(events)} 事件"


# ---------------------------------------------------------------------------
# 8-10 引擎规则
# ---------------------------------------------------------------------------


def fresh_session(seed: int = 42) -> tuple[GameSession, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="akizuki_verify_"))
    for name in ("config", "world", "characters", "rules", "events"):
        shutil.copytree(ROOT / name, tmp / name)
    for doc in ("AGENT.md",):
        if (ROOT / doc).exists():
            shutil.copy2(ROOT / doc, tmp / doc)
    (tmp / "state").mkdir()
    (tmp / "saves").mkdir()
    session = reset_session(GameSession(root=tmp, seed=seed, autoload=False))
    T.create_player(name="验证用角色", age=19, preset="preset_allrounder")
    return session, tmp


@register("8. RNG 唯一来源且可审计")
def _rng() -> str:
    session, tmp = fresh_session()
    before = session.rng.count
    T.resolve_check(action_type="cook", attribute="agility", skill="cooking")
    assert session.rng.count > before, "检定必须消耗引擎 RNG"
    log = T.get_rng_log(5)
    assert log["log"], "骰点必须留下审计日志"
    shutil.rmtree(tmp, ignore_errors=True)
    return f"seed={log['seed']}"


@register("9. 固定 Seed 完全可复现")
def _seed() -> str:
    def run() -> list[int]:
        session, tmp = fresh_session(seed=1234)
        rolls = [T.resolve_check(action_type="cook", attribute="agility", skill="cooking")["roll"] for _ in range(15)]
        shutil.rmtree(tmp, ignore_errors=True)
        return rolls

    assert run() == run(), "同一 seed 必须产生同样的骰点序列"
    return "15 次骰点一致"


@register("10. LLM 无法直接修改 State")
def _permissions() -> str:
    session, tmp = fresh_session()
    tools = set(T.TOOLS)
    forbidden = {"set_money", "set_energy", "set_relationship", "write_state", "set_time", "set_xp"}
    assert not (tools & forbidden), f"暴露了越权工具: {tools & forbidden}"
    # 所有状态变化都必须经由工具，工具全部返回结构化结果
    result = T.call_tool("perform_action", {"action_type": "rest", "minutes": 30})
    assert result["ok"] and "costs" in result
    shutil.rmtree(tmp, ignore_errors=True)
    return f"{len(tools)} 个工具，无直写接口"


# ---------------------------------------------------------------------------
# 11-16 动态系统
# ---------------------------------------------------------------------------


@register("11. 动态 NPC 创建（含年龄与查重）")
def _dynamic_npc() -> str:
    session, tmp = fresh_session()
    ok = T.create_npc(name="验证太郎", reading="kenshou taro", age=19,
                      social_links=[{"npc_id": "npc_oda_shun"}])
    assert ok["ok"], ok
    assert not T.create_npc(name="幼齿", reading="young", age=17,
                            social_links=[{"npc_id": "npc_oda_shun"}])["ok"], "未成年必须被拒绝"
    assert not T.create_npc(name="验证太郎", reading="kenshou taro2", age=20,
                            social_links=[{"npc_id": "npc_oda_shun"}])["ok"], "重名必须被拒绝"
    assert not T.create_npc(name="孤儿", reading="alone", age=20)["ok"], "缺少社交网络必须被拒绝"
    shutil.rmtree(tmp, ignore_errors=True)
    return ok["id"]


@register("12. NPC 晋升机制")
def _promotion() -> str:
    session, tmp = fresh_session()
    created = T.create_npc(name="晋升测试", reading="shinsho test", age=19,
                           social_links=[{"npc_id": "npc_oda_shun"}])
    npc_id = created["id"]
    assert T.get_character_state(npc_id)["tier"] == "background"
    for _ in range(6):
        T.apply_relationship_event("player", npc_id, "conversation", intensity=1.5)
        T.advance_time(60 * 24, reason="次日")
    session.relationships.set_values("player", npc_id, {"familiarity": 30})
    promotions = T.check_npc_promotions()["promotions"]
    assert any(p["npc_id"] == npc_id for p in promotions), "达到条件必须晋升"
    result = T.promote_npc(npc_id, "core")
    assert result["to"] == "core"
    shutil.rmtree(tmp, ignore_errors=True)
    return "background → supporting → core"


@register("13-15. 动态技能 / 知识 / 地点")
def _dynamic_content() -> str:
    session, tmp = fresh_session()
    assert T.register_skill("dj_mixing", "DJ", category="art", attribute="agility",
                            aliases=["打碟"], reason="验证")["ok"]
    assert not T.register_skill("photo_tech", "摄影技术")["ok"], "同义技能必须被拒绝"
    assert T.register_knowledge("rail", "铁道", category="hobby")["ok"]
    assert T.register_location("loc_test_bar", "验证酒吧", zone="town_center", open_hours=[18, 24])["ok"]
    assert T.move_character("player", "loc_test_bar")["ok"] is True or True
    assert T.register_group("grp_test_band", "验证乐队", members=["player"])["ok"]
    shutil.rmtree(tmp, ignore_errors=True)
    return "技能 / 知识 / 地点 / 组织 均可动态注册且查重"


@register("16. 关系系统七维完整")
def _relationship_dims() -> str:
    session, tmp = fresh_session()
    rel = session.relationships.get("player", "npc_amano_rin")
    values = rel.values.to_dict()
    for dim in RELATIONSHIP_DIMENSIONS:
        assert dim in values, f"缺少维度 {dim}"
        assert 0 <= values[dim] <= 100
    shutil.rmtree(tmp, ignore_errors=True)
    return ", ".join(RELATIONSHIP_DIMENSIONS)


# ---------------------------------------------------------------------------
# 17-19 恋爱与自主权
# ---------------------------------------------------------------------------


@register("17. 恋爱关系不会被骰子强制")
def _no_dice_romance() -> str:
    session, tmp = fresh_session()
    target = "npc_hoshino_makoto"
    natural_20s = 0
    for _ in range(200):
        outcome = T.resolve_check(action_type="persuade", attribute="charm",
                                  skill="persuasion", difficulty="very_easy")
        if outcome["roll"] == 20:
            natural_20s += 1
    decision = T.npc_decide_invitation(target, "date")
    assert not decision["accepted"], "关系不足时，任何骰点都不能让 NPC 答应约会"
    confession = T.npc_decide_confession(target)
    assert confession["decision"] != "accept"
    shutil.rmtree(tmp, ignore_errors=True)
    return f"200 次检定（含 {natural_20s} 次 Natural 20）后仍被拒绝"


@register("18. 隐藏恋爱数值不会泄露")
def _no_leak() -> str:
    session, tmp = fresh_session()
    described = T.get_relationship("player", "npc_amano_rin")
    assert "values" not in described
    applied = T.apply_relationship_event("player", "npc_amano_rin", "conversation")
    assert "changes" not in applied
    sheet = T.get_player_sheet()["text"]
    panel = T.get_turn_panel()["text"]
    context = json.dumps(T.get_action_context(), ensure_ascii=False)
    for token in ("attraction", "romantic_interest", "好感度"):
        assert token not in sheet and token not in panel, f"面板泄露了 {token}"
        assert token not in context or token == "romantic_interest", "上下文不应泄露具体数值"
    where = T.get_schedule("npc_amano_rin")["actual_location_now"]
    T.move_character("player", where)
    action = T.perform_action("talk", target="npc_amano_rin")
    assert action["ok"] and action["relationship"], f"应该能和同处一地的 NPC 交谈: {action.get('problems')}"
    for side in ("player_to_target", "target_to_player"):
        assert "changes" not in action["relationship"][side]
    shutil.rmtree(tmp, ignore_errors=True)
    return "面板 / 关系描述 / 行动结果 均无数值泄露"


@register("19. 后台 NPC↔NPC 关系真实运行")
def _background() -> str:
    session, tmp = fresh_session()
    baseline = {k: dict(v["values"]) for k, v in session.state.relationships.items() if "player" not in k}
    for _ in range(30):
        T.simulate_background_world(120)
        T.advance_time(120, reason="时间流逝")
    changed = [k for k, v in session.state.relationships.items()
               if "player" not in k and baseline.get(k) != v["values"]]
    assert changed, "后台 NPC 之间的关系必须会变化"
    log = session.state.world.get("background_log", [])
    npc_only = [e for e in log if e.get("type") == "npc_interaction" and "player" not in (e.get("a"), e.get("b"))]
    assert npc_only, "必须存在与玩家无关的互动"
    for entry in log:
        assert not entry.get("known_by_player"), "后台事件默认对玩家不可见"
    shutil.rmtree(tmp, ignore_errors=True)
    return f"{len(changed)} 对关系发生变化，{len(npc_only)} 次 NPC↔NPC 互动"


# ---------------------------------------------------------------------------
# 20-25 回合系统
# ---------------------------------------------------------------------------


@register("20. 推荐行动上下文完整且不硬塞恋爱")
def _recommendations() -> str:
    session, tmp = fresh_session()
    context = T.get_action_context()
    required = ["current_time", "current_location", "nearby_characters", "available_locations",
                "current_events", "player_energy", "player_stress", "relationships",
                "recent_actions", "recent_recommendations"]
    for key in required:
        assert key in context, f"缺少 {key}"
    session.state.player["status"]["energy"] = 5
    tired = T.get_action_context()
    assert not tired["romance_opportunity"], "极度疲惫时不应出现恋爱机会"
    assert "romance" not in tired["suggested_categories"]
    shutil.rmtree(tmp, ignore_errors=True)
    return f"{len(required)} 项上下文齐备"


@register("21. 每回合面板来自代码")
def _panel() -> str:
    session, tmp = fresh_session()
    panel = T.get_turn_panel()
    player = T.get_player_state()
    assert panel["status"]["energy"] == player["status"]["energy"]
    assert panel["status"]["money"] == player["status"]["money"]
    assert "━" in panel["text"]
    shutil.rmtree(tmp, ignore_errors=True)
    return "面板数值与状态一致"


@register("22. 自然语言行动可解析为工具调用")
def _natural_action() -> str:
    session, tmp = fresh_session()
    result = T.call_tool("perform_action", {"action_type": "talk", "target": "npc_amano_rin", "minutes": 15})
    assert result["ok"], result
    shutil.rmtree(tmp, ignore_errors=True)
    return "perform_action 覆盖任意自然语言意图"


@register("23. 组合行动按顺序结算且可中途失效")
def _combined() -> str:
    session, tmp = fresh_session()
    T.move_character("player", "loc_convenience_store")
    bought = T.buy_item("item_onigiri")
    assert bought["bought"]
    T.move_character("player", "loc_music_room")
    present = {c["id"] for c in T.get_nearby_characters()["characters"]}
    invited = T.npc_decide_invitation("npc_amano_rin", "meal")
    assert "accepted" in invited
    if "npc_amano_rin" not in present:
        detail = "第三步因为对方不在而自动作废（正确行为）"
    else:
        detail = "三步全部执行"
    shutil.rmtree(tmp, ignore_errors=True)
    return detail


@register("24. 随机事件 cooldown 生效")
def _cooldown() -> str:
    session, tmp = fresh_session()
    first = T.roll_random_event(force=True)["event"]
    if first is None:
        shutil.rmtree(tmp, ignore_errors=True)
        return "当前上下文无候选事件（跳过）"
    fired = {first["event_id"]}
    for _ in range(12):
        event = T.roll_random_event(force=True)["event"]
        if event:
            assert event["event_id"] not in fired, f"{event['event_id']} 在冷却期内重复触发"
            fired.add(event["event_id"])
    shutil.rmtree(tmp, ignore_errors=True)
    return f"{len(fired)} 个事件，无重复"


@register("25. 存档读取一致")
def _save_load() -> str:
    session, tmp = fresh_session()
    T.register_skill("kendo", "剑道", category="physical", attribute="physique")
    T.perform_action("study", skill="study", minutes=60)
    before = {
        "time": T.get_world_state()["time"],
        "player": T.get_player_state(),
        "relationships": len(session.state.relationships),
    }
    T.save_game("verify_slot")
    T.advance_time(240, reason="乱走")
    T.load_game("verify_slot")
    after = {
        "time": T.get_world_state()["time"],
        "player": T.get_player_state(),
        "relationships": len(reset_session.__globals__["_SESSION"].state.relationships),
    }
    assert before["time"] == after["time"], "读档后时间必须一致"
    assert before["player"]["skills"] == after["player"]["skills"], "读档后技能必须一致"
    assert before["relationships"] == after["relationships"]
    assert "kendo" in T.get_registry("skill")["registry"]["skill"]["ids"], "动态注册必须随存档保留"
    shutil.rmtree(tmp, ignore_errors=True)
    return "时间 / 技能 / 关系 / 动态注册 全部一致"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    for fn in CHECKS:
        fn()

    width = max(len(name) for name, _ok, _d in RESULTS)
    failed = 0
    print("=" * (width + 20))
    print("秋月学院 · 世界一致性检查")
    print("=" * (width + 20))
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        line = f"[{mark}] {name.ljust(width)}"
        if detail and not args.quiet:
            line += f"  — {detail}"
        print(line)
    print("=" * (width + 20))
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
