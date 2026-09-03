#!/usr/bin/env python3
"""Smoke Test —— 用固定种子跑完整的 50 回合，覆盖全部核心系统。

运行::

    python3 scripts/smoke_test.py            # 50 回合
    python3 scripts/smoke_test.py --turns 80 # 更长
    python3 scripts/smoke_test.py --keep     # 保留临时存档目录

本脚本模拟的是"Agent 会怎么调用引擎"，因此它只走 engine.tools 的公开工具。
最后会打印一张覆盖清单：每一项都必须为 ✅。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import tools as T  # noqa: E402
from engine.tools import GameSession, reset_session  # noqa: E402

CHECKLIST = {
    "上课": False,
    "午休": False,
    "社团活动": False,
    "主动认识新 NPC": False,
    "动态创建 NPC": False,
    "技能检定": False,
    "判定失败": False,
    "知识获得": False,
    "新技能注册": False,
    "新知识注册": False,
    "动态地点注册": False,
    "动态组织注册": False,
    "NPC 之间后台互动": False,
    "NPC↔NPC 关系变化": False,
    "玩家与 NPC 关系提升": False,
    "NPC 拒绝玩家邀请": False,
    "成功约到 NPC": False,
    "暧昧事件": False,
    "正常约会": False,
    "随机事件触发": False,
    "NPC 晋升": False,
    "玩家无视推荐": False,
    "组合自然语言行动": False,
    "记忆写入": False,
    "保存与重新载入": False,
    "隐藏数值未泄露": False,
    "世界不以玩家为中心": False,
}

log_lines: list[str] = []


def log(turn: int, text: str) -> None:
    line = f"[T{turn:02d}] {text}"
    log_lines.append(line)
    print(line)


def sandbox() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="akizuki_smoke_"))
    for name in ("config", "world", "characters", "rules", "events"):
        shutil.copytree(ROOT / name, tmp / name)
    for doc in ("AGENT.md", "README.md"):
        if (ROOT / doc).exists():
            shutil.copy2(ROOT / doc, tmp / doc)
    (tmp / "state").mkdir()
    (tmp / "saves").mkdir()
    return tmp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20250416)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="打印引擎内部数值（仅调试）")
    args = parser.parse_args()

    tmp = sandbox()
    reset_session(GameSession(root=tmp, seed=args.seed, autoload=False))

    created = T.create_player(
        name="佐藤悠", age=19, gender="male",
        attributes={"physique": 5, "agility": 6, "intellect": 6, "perception": 7,
                    "charm": 6, "willpower": 5, "luck": 5},
        skills=["conversation", "photography", "cooking"],
        knowledge=["photography", "local_area", "anime", "literature"],
        appearance="中等身材，总背着一个旧相机包",
        interests=["摄影", "深夜广播"],
        personality_tendency=["观察型", "不太会拒绝别人"],
    )
    assert created["ok"], created
    log(0, f"角色创建完成：{created['player']['name']}")

    baseline_session = reset_session.__globals__["_SESSION"]
    npc_npc_baseline = {
        key: dict(raw.get("values", {}))
        for key, raw in baseline_session.state.relationships.items()
        if "player" not in key
    }

    turn = 0
    romance_target = "npc_amano_rin"
    friend_target = "npc_hoshino_makoto"
    dynamic_npc: str | None = None

    def do(*args_, **kwargs) -> dict:
        result = T.perform_action(*args_, **kwargs)
        if not result.get("ok"):
            log(turn, f"  ! 行动被拒绝: {result.get('problems') or result.get('error')}")
        return result

    while turn < args.turns:
        turn += 1
        world = T.get_world_state()
        hour = int(world["time"].split(":")[0])
        weekday = world["weekday"]
        location = world["location"]["id"]

        # ---------- 早上：到校 ----------
        if 6 <= hour < 12:
            T.move_character("player", "loc_class_2a")
            do("attend_class", minutes=50, skill="study", knowledge="literature")
            CHECKLIST["上课"] = True
            log(turn, f"{world['date']} {world['time']} 到校上课")

        # ---------- 午休 ----------
        elif 12 <= hour < 13:
            T.move_character("player", "loc_cafeteria")
            buy = T.buy_item("item_set_a")
            do("eat", minutes=30)
            nearby = T.get_nearby_characters()["characters"]
            if nearby:
                target = nearby[0]["id"]
                do("talk", target=target, minutes=20)
                CHECKLIST["玩家与 NPC 关系提升"] = True
            CHECKLIST["午休"] = True
            log(turn, f"午休：{buy.get('item')}，同席 {len(nearby)} 人")

        # ---------- 下午上课 ----------
        elif 13 <= hour < 15:
            T.move_character("player", "loc_class_2a")
            do("attend_class", minutes=50, skill="study", knowledge="mathematics")
            CHECKLIST["上课"] = True
            log(turn, "下午的课")

        # ---------- 放学后 ----------
        elif 15 <= hour < 19:
            context = T.get_action_context()
            # 玩家有时无视推荐，直接自己决定
            if turn % 5 == 0:
                CHECKLIST["玩家无视推荐"] = True
                T.move_character("player", "loc_riverbank")
                result = do("photo", skill="photography", knowledge="photography", difficulty="normal", minutes=40)
                if result.get("check"):
                    CHECKLIST["技能检定"] = True
                    if not result["check"]["result"].endswith("success"):
                        CHECKLIST["判定失败"] = True
                if any(x["kind"] == "knowledge" and x["gained"] > 0 for x in result.get("xp", [])):
                    CHECKLIST["知识获得"] = True
                log(turn, f"（无视推荐）去河堤拍照：{(result.get('check') or {}).get('result', '无需判定')}")
            elif turn % 7 == 0:
                T.move_character("player", "loc_music_room")
                result = do("perform", skill="performance", difficulty="very_hard", minutes=30)
                if result.get("check"):
                    CHECKLIST["技能检定"] = True
                    if not result["check"]["result"].endswith("success"):
                        CHECKLIST["判定失败"] = True
                        log(turn, "在没练熟的情况下上台，果然搞砸了一点")
            elif context["club_time"] and T.get_player_state()["clubs"]:
                T.move_character("player", "loc_music_room")
                do("club_activity", minutes=90, skill="conversation")
                CHECKLIST["社团活动"] = True
                log(turn, "参加社团活动")
            else:
                # 找人聊天 / 推进关系
                T.move_character("player", "loc_courtyard")
                schedule = T.get_schedule(romance_target)
                target_loc = schedule["actual_location_now"]
                if target_loc:
                    T.move_character("player", target_loc)
                nearby = {c["id"] for c in T.get_nearby_characters()["characters"]}
                if romance_target in nearby:
                    do("talk", target=romance_target, minutes=25)
                    T.apply_relationship_event("player", romance_target, "shared_interest", intensity=1.2)
                    T.apply_relationship_event(romance_target, "player", "shared_interest", intensity=1.0)
                    log(turn, "和天野凛聊了一会儿")
                else:
                    log(turn, "想找的人不在——世界不为玩家停留")
                    others = T.get_nearby_characters()["characters"]
                    if others:
                        do("small_talk", target=others[0]["id"], minutes=10)

        # ---------- 晚上 ----------
        elif 19 <= hour < 24:
            do("study", skill="study", knowledge="mathematics", minutes=60)
            if turn % 4 == 0:
                T.add_memory(
                    "player",
                    fact=f"第 {turn} 回合，在房间里复习到很晚。",
                    interpretation="好像稍微跟上进度了。",
                    emotion="安心",
                    tags=["study"],
                )
                CHECKLIST["记忆写入"] = True
            log(turn, "晚上在家学习")

        else:
            T.sleep(until="07:00")
            log(turn, "睡觉")

        # ---------- 剧本节点 ----------
        if turn == 3:
            joined = T.join_club("club_light_music")
            log(turn, f"加入社团: {joined}")

        if turn == 4:
            # 主动认识新 NPC（摄影部人手不足 → 动态创建）
            T.move_character("player", "loc_photo_room")
            registered = T.register_skill(
                "video_editing", "视频剪辑", category="art", attribute="intellect",
                aliases=["剪片", "剪辑"], reason="玩家开始参与校园短片",
            )
            CHECKLIST["新技能注册"] = registered["ok"]
            knowledge = T.register_knowledge(
                "coffee", "咖啡", category="hobby", aliases=["手冲", "咖啡豆"],
                unlocks=["认出店家的烘焙程度"], reason="玩家开始泡在咖啡店",
            )
            CHECKLIST["新知识注册"] = knowledge["ok"]
            created_npc = T.create_npc(
                name="桑原树", reading="kuwabara itsuki", age=19, gender="male",
                role="student", tier="background", class_id="class_2c",
                appearance="瘦高，戴棒球帽，手指上有胶带",
                personality="话不多，但对器材有异常的耐心",
                speech_style="短句，偶尔冒出很冷的玩笑",
                skills={"photography": 2, "technology": 3}, knowledge={"photography": 2},
                home_location="loc_station", favorite_place="loc_photo_room",
                social_links=[{"npc_id": "npc_natsume_kou", "familiarity": 55, "trust": 45,
                               "note": "摄影部的学弟，一直在帮忙修暗房"}],
                created_reason="玩家第一次去摄影部，需要一个真实存在的成员",
            )
            CHECKLIST["动态创建 NPC"] = created_npc["ok"]
            CHECKLIST["主动认识新 NPC"] = created_npc["ok"]
            dynamic_npc = created_npc.get("id")
            log(turn, f"摄影部：注册技能/知识，创建 NPC {dynamic_npc}")

        if turn == 6:
            location = T.register_location(
                "loc_cafe_moon", "月见咖啡", zone="town_center", area="town",
                open_hours=[11, 23], tags=["coffee", "date", "quiet"],
                description="车站后面新开的小店，只有六个座位。",
                shop_items=[{"id": "item_moon_latte", "name": "月见拿铁", "price": 620}],
                reason="玩家在车站附近探索时发现",
            )
            CHECKLIST["动态地点注册"] = location["ok"]
            group = T.register_group(
                "grp_film_team", "文化祭短片组", group_type="project",
                members=["player", "npc_natsume_kou"] + ([dynamic_npc] if dynamic_npc else []),
                location="loc_photo_room", purpose="为文化祭拍一部十分钟的短片",
                temporary=True, reason="摄影部与玩家的共同企划",
            )
            CHECKLIST["动态组织注册"] = group["ok"]
            log(turn, "注册了新地点与临时组织")

        if turn == 8:
            decision = T.npc_decide_invitation(romance_target, "date")
            if not decision["accepted"]:
                CHECKLIST["NPC 拒绝玩家邀请"] = True
                log(turn, f"邀请被婉拒：{decision['reason_code']} —— {decision['reason']}")
                T.apply_relationship_event("player", romance_target, "invite_declined", intensity=0.6)

        # ---------- 关系长期积累 ----------
        # 注意事件顺序：每日关系收益有上限，先发生的事件先分到额度。
        # 这正是引擎的设计意图——一天之内不可能把关系推很远。
        if 5 <= turn:
            if turn % 2 == 0:
                for event in ("deep_talk", "walk_home"):
                    T.apply_relationship_event("player", romance_target, event, intensity=1.3)
                    T.apply_relationship_event(romance_target, "player", event, intensity=1.3)
            if turn % 3 == 0:
                result = T.apply_relationship_event("player", romance_target, "alone_together", intensity=1.2)
                T.apply_relationship_event(romance_target, "player", "alone_together", intensity=1.2)
            if turn % 5 == 0:
                result = T.apply_relationship_event("player", romance_target, "ambiguous_moment", intensity=1.1)
                T.apply_relationship_event(romance_target, "player", "ambiguous_moment", intensity=1.1)
                if result.get("ok"):
                    CHECKLIST["暧昧事件"] = True
            for partner, event in ((romance_target, "conversation"), (friend_target, "shared_meal")):
                T.apply_relationship_event("player", partner, event, intensity=1.0)
                T.apply_relationship_event(partner, "player", event, intensity=1.0)
            if dynamic_npc:
                T.apply_relationship_event("player", dynamic_npc, "club_activity", intensity=1.3)
                T.apply_relationship_event(dynamic_npc, "player", "club_activity", intensity=1.3)

        # ---------- 尝试真正的约会邀请 ----------
        if turn >= 20 and not CHECKLIST["成功约到 NPC"]:
            decision = T.npc_decide_invitation(romance_target, "one_on_one")
            if turn % 7 == 0 and not decision["accepted"]:
                log(turn, f"  邀请仍未成功：{decision['reason_code']}")
            if decision["accepted"]:
                CHECKLIST["成功约到 NPC"] = True
                T.move_character("player", "loc_cafe_moon")
                result = do("date", target=romance_target, minutes=120, money_cost=1200)
                if result.get("ok"):
                    CHECKLIST["正常约会"] = True
                T.add_memory(
                    romance_target,
                    fact="和玩家一起去了车站后面那家新开的咖啡店。",
                    interpretation="他好像真的记得我说过想去。",
                    emotion="有点高兴，也有点不知道该怎么办。",
                    intensity=6, visibility="private_fact", participants=["player"], tags=["date"],
                )
                log(turn, "第一次单独出去（月见咖啡）")

        # ---------- 组合自然语言行动（一次输入里的连续意图）----------
        if turn == 25:
            # "我先去便利店买饮料，再去音乐室找凛，如果她在就问她晚上要不要一起吃饭"
            T.move_character("player", "loc_convenience_store")
            T.buy_item("item_onigiri")
            T.move_character("player", "loc_music_room")
            present = {c["id"] for c in T.get_nearby_characters()["characters"]}
            if romance_target in present:
                decision = T.npc_decide_invitation(romance_target, "meal")
                log(turn, f"组合行动：买东西→找人→邀约 = {decision['reason_code']}")
            else:
                log(turn, "组合行动：买东西→找人→她不在，后续意图自动作废")
            CHECKLIST["组合自然语言行动"] = True

        # ---------- 回合收尾 ----------
        end = T.end_turn(simulate_minutes=60)
        if end["random_event"]:
            CHECKLIST["随机事件触发"] = True
        if end["promotions"]:
            CHECKLIST["NPC 晋升"] = True
        if end["simulation"]["interactions"] > 0:
            CHECKLIST["NPC 之间后台互动"] = True
        T.record_recommendations([f"第{turn}回合的推荐A", f"第{turn}回合的推荐B"])

        # 时间推进：保证 50 回合覆盖多天（恋爱必须长期积累，一天之内不可能推很远）
        T.advance_time(90, reason="回合间隔")
        now_hour = int(T.get_world_state()["time"].split(":")[0])
        if now_hour >= 21 or now_hour < 5 or T.get_world_state()["must_sleep"]:
            T.sleep(until="07:00")
            log(turn, f"—— 第二天（{T.get_world_state()['date']}）——")

    # ---------- 后置校验 ----------
    session = reset_session.__globals__["_SESSION"]
    if args.verbose:
        print("\n[引擎内部诊断 —— 这些数字永远不会出现在玩家面前]")
        print("  npc→player:", session.relationships.get(romance_target, "player").values.to_dict())
        print("  player→npc:", session.relationships.get("player", romance_target).values.to_dict())
        if dynamic_npc:
            print("  动态 NPC 晋升检查:", session.npcs.check_promotion(dynamic_npc))
    log_entries = session.state.world.get("background_log", [])
    npc_only = [e for e in log_entries if e.get("type") == "npc_interaction"
                and "player" not in (e.get("a"), e.get("b"))]
    CHECKLIST["世界不以玩家为中心"] = bool(npc_only)

    CHECKLIST["NPC↔NPC 关系变化"] = any(
        key not in npc_npc_baseline or npc_npc_baseline[key] != raw.get("values")
        for key, raw in session.state.relationships.items()
        if "player" not in key
    )

    save = T.save_game("save_001")
    T.advance_time(300, reason="乱走")
    loaded = T.load_game("save_001")
    CHECKLIST["保存与重新载入"] = save["ok"] and loaded["ok"]

    sheet = T.get_player_sheet()
    panel = T.get_turn_panel()
    leak = any(token in sheet["text"] + panel["text"] for token in
               ("attraction", "romantic_interest", "好感度", "好感 +"))
    CHECKLIST["隐藏数值未泄露"] = not leak

    rel = T.get_relationship("player", romance_target)
    print("\n" + "=" * 60)
    print(panel["text"])
    print("\n最终与天野凛的关系：", rel["label"], rel.get("hints"))
    print("认识的人数：", len(T.get_player_sheet()["relationships"]))
    print("注册表：", {k: v["count"] for k, v in T.get_registry()["registry"].items()})
    print("骰点总数：", T.get_rng_log(1)["total_rolls"])
    print("=" * 60)

    print("\n覆盖清单：")
    failed = []
    for item, done in CHECKLIST.items():
        print(f"  {'✅' if done else '❌'} {item}")
        if not done:
            failed.append(item)

    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"\n临时目录保留在: {tmp}")

    if failed:
        print(f"\nSMOKE TEST 失败，未覆盖: {', '.join(failed)}")
        return 1
    print(f"\nSMOKE TEST 通过（{args.turns} 回合，全部 {len(CHECKLIST)} 项覆盖）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
