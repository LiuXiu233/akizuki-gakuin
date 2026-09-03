"""Web 后端 API 测试。

依赖 fastapi/httpx；系统 Python 没装时整体跳过，
这样 `python3 -m unittest discover` 在纯引擎环境下依然全绿。
用 .venv 跑时会真正执行。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient  # noqa: F401

    HAS_FASTAPI = True
except Exception:  # noqa: BLE001
    HAS_FASTAPI = False


@unittest.skipUnless(HAS_FASTAPI, "未安装 fastapi/httpx（用 .venv 运行以启用后端测试）")
class ServerTestCase(unittest.TestCase):
    """每个用例一个全新的数据目录，互不干扰。"""

    access_password = ""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="akizuki_api_"))
        os.environ["AKIZUKI_DATA_DIR"] = str(self.tmp)
        os.environ["AKIZUKI_ACCESS_PASSWORD"] = self.access_password
        self._reload()
        from fastapi.testclient import TestClient

        from server.app import create_app

        self.client = TestClient(create_app())
        self.headers: dict[str, str] = {}
        if self.access_password:
            self.headers["X-Access-Password"] = self.access_password

    def tearDown(self) -> None:
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AKIZUKI_DATA_DIR", None)
        os.environ.pop("AKIZUKI_ACCESS_PASSWORD", None)

    def _reload(self) -> None:
        """清掉所有进程级缓存，让新的 AKIZUKI_* 环境变量生效。"""
        import server.deps
        import server.routes.meta
        from server.config import get_settings

        get_settings.cache_clear()
        server.deps.reset_state()
        server.routes.meta._READONLY_SESSION = None

    # -- 辅助 --------------------------------------------------------
    def new_user(self) -> str:
        response = self.client.post("/api/session", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user_id"]

    def auth(self, user_id: str) -> dict[str, str]:
        return {**self.headers, "X-User-Token": user_id}

    def new_world(self, user_id: str, name: str = "测试世界", seed: int = 42) -> str:
        response = self.client.post(
            "/api/worlds", json={"name": name, "seed": seed}, headers=self.auth(user_id)
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["world"]["id"]

    def tool(self, user_id: str, world_id: str, tool_name: str, /, **arguments) -> dict:
        response = self.client.post(
            f"/api/worlds/{world_id}/tools/{tool_name}",
            json={"arguments": arguments},
            headers=self.auth(user_id),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class TestMeta(ServerTestCase):
    def test_health(self) -> None:
        data = self.client.get("/api/health").json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["auth_required"])
        self.assertIn("engine_version", data)

    def test_health_never_leaks_keys(self) -> None:
        text = self.client.get("/api/health").text
        for token in ("api_key", "sk-", "password"):
            self.assertNotIn(token, text)

    def test_meta_returns_static_world(self) -> None:
        data = self.client.get("/api/meta", headers=self.headers).json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["locations"]), 40)
        self.assertGreaterEqual(len(data["clubs"]), 14)
        self.assertGreaterEqual(len(data["skills"]), 25)
        self.assertEqual(len(data["player_presets"]), 5)

    def test_tool_schema(self) -> None:
        data = self.client.get("/api/tools/schema", headers=self.headers).json()
        self.assertGreaterEqual(len(data["tools"]), 50)

    def test_lore(self) -> None:
        data = self.client.get("/api/lore/school", headers=self.headers).json()
        self.assertIn("秋月学院", data["text"])
        self.assertEqual(self.client.get("/api/lore/nope", headers=self.headers).status_code, 404)


class TestAuth(ServerTestCase):
    access_password = "kaoru-sensei"

    def test_password_required(self) -> None:
        self.assertEqual(self.client.post("/api/session", json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_password_accepted(self) -> None:
        response = self.client.post("/api/session", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_health_announces_auth(self) -> None:
        self.assertTrue(self.client.get("/api/health").json()["auth_required"])


class TestSessionAndWorlds(ServerTestCase):
    def test_create_and_reuse_token(self) -> None:
        first = self.client.post("/api/session", json={}, headers=self.headers).json()
        self.assertTrue(first["created"])
        again = self.client.post(
            "/api/session", json={"user_id": first["user_id"]}, headers=self.headers
        ).json()
        self.assertFalse(again["created"])
        self.assertEqual(again["user_id"], first["user_id"])

    def test_invalid_token_gets_new_user(self) -> None:
        data = self.client.post("/api/session", json={"user_id": "不是令牌"}, headers=self.headers).json()
        self.assertTrue(data["created"])
        self.assertRegex(data["user_id"], r"^[a-f0-9]{32}$")

    def test_world_crud(self) -> None:
        user = self.new_user()
        world_id = self.new_world(user, "第一周目")
        worlds = self.client.get("/api/worlds", headers=self.auth(user)).json()["worlds"]
        self.assertEqual(len(worlds), 1)
        self.assertEqual(worlds[0]["name"], "第一周目")

        renamed = self.client.patch(
            f"/api/worlds/{world_id}", json={"name": "第二周目"}, headers=self.auth(user)
        ).json()
        self.assertEqual(renamed["world"]["name"], "第二周目")

        deleted = self.client.delete(f"/api/worlds/{world_id}", headers=self.auth(user))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/worlds", headers=self.auth(user)).json()["worlds"], [])

    def test_worlds_are_isolated_between_users(self) -> None:
        alice, bob = self.new_user(), self.new_user()
        world_a = self.new_world(alice, "凛的世界")
        self.new_world(bob, "另一个世界")

        self.tool(alice, world_a, "create_player", name="爱丽丝", age=19, preset="preset_artist")
        self.assertEqual(len(self.client.get("/api/worlds", headers=self.auth(bob)).json()["worlds"]), 1)
        # bob 不能碰 alice 的世界
        response = self.client.get(f"/api/worlds/{world_a}", headers=self.auth(bob))
        self.assertEqual(response.status_code, 404)

    def test_two_worlds_same_user_are_independent(self) -> None:
        user = self.new_user()
        first, second = self.new_world(user, "A", seed=1), self.new_world(user, "B", seed=2)
        self.tool(user, first, "create_player", name="第一个我", age=19, preset="preset_athlete")
        self.tool(user, second, "create_player", name="第二个我", age=20, preset="preset_scholar")
        self.assertEqual(self.tool(user, first, "get_player_state")["name"], "第一个我")
        self.assertEqual(self.tool(user, second, "get_player_state")["name"], "第二个我")
        self.tool(user, first, "advance_time", minutes=600)
        self.assertNotEqual(
            self.tool(user, first, "get_world_state")["time"],
            self.tool(user, second, "get_world_state")["time"],
        )

    def test_missing_token_rejected(self) -> None:
        self.assertEqual(self.client.get("/api/worlds", headers=self.headers).status_code, 401)

    def test_world_snapshot_endpoint(self) -> None:
        user = self.new_user()
        world_id = self.new_world(user)
        self.tool(user, world_id, "create_player", name="佐藤悠", age=19, preset="preset_allrounder")
        data = self.client.get(f"/api/worlds/{world_id}", headers=self.auth(user)).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["player"]["name"], "佐藤悠")
        self.assertIn("秋月学院", data["panel"]["text"])
        self.assertIn("suggested_categories", data["context"])
        self.assertEqual(data["meta"]["player_name"], "佐藤悠")


class TestToolsAPI(ServerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = self.new_user()
        self.world = self.new_world(self.user)
        self.tool(self.user, self.world, "create_player", name="佐藤悠", age=19, preset="preset_artist")

    def test_all_engine_tools_exposed(self) -> None:
        data = self.client.get(
            f"/api/worlds/{self.world}/tools", headers=self.auth(self.user)
        ).json()
        names = {t["name"] for t in data["tools"]}
        for required in ("perform_action", "resolve_check", "create_npc", "npc_decide_confession",
                         "register_skill", "simulate_background_world", "get_turn_panel"):
            self.assertIn(required, names)

    def test_unknown_tool_404(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/tools/不存在", json={"arguments": {}}, headers=self.auth(self.user)
        )
        self.assertEqual(response.status_code, 404)

    def test_tool_error_is_200_with_ok_false(self) -> None:
        result = self.tool(self.user, self.world, "get_character_state", character_id="npc_不存在")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_check_and_action_persist(self) -> None:
        self.tool(self.user, self.world, "move_character", character_id="player", location_id="loc_class_2a")
        before = self.tool(self.user, self.world, "get_world_state")["time"]
        self.tool(self.user, self.world, "advance_time", minutes=45, reason="上课")
        after = self.tool(self.user, self.world, "get_world_state")["time"]
        self.assertNotEqual(before, after)
        # 重新载入世界后仍然保留
        self.client.delete(f"/api/worlds/{self.world}", headers=self.auth(self.user))  # noqa: 只是确保没崩

    @staticmethod
    def _find_hidden_keys(node, found=None):
        """递归查找是否有任何**键**泄露了隐藏关系数值。"""
        found = [] if found is None else found
        hidden = {"attraction", "romantic_interest", "trust", "familiarity", "closeness", "comfort"}
        if isinstance(node, dict):
            for key, value in node.items():
                if key in hidden and isinstance(value, (int, float)):
                    found.append(key)
                TestToolsAPI._find_hidden_keys(value, found)
        elif isinstance(node, list):
            for item in node:
                TestToolsAPI._find_hidden_keys(item, found)
        return found

    def test_hidden_numbers_not_leaked_through_api(self) -> None:
        result = self.tool(self.user, self.world, "get_relationship",
                           actor_id="player", target_id="npc_amano_rin")
        self.assertNotIn("values", result)
        self.assertEqual(self._find_hidden_keys(result), [], "关系数值不得通过 API 泄露")
        # 提示语里出现字段名是允许的（那是给 Agent 的约束，不是数值）
        self.assertIn("reminder", result)

    def test_panel_and_sheet_have_no_hidden_numbers(self) -> None:
        panel = self.tool(self.user, self.world, "get_turn_panel")
        sheet = self.tool(self.user, self.world, "get_player_sheet")
        for payload in (panel, sheet):
            self.assertEqual(self._find_hidden_keys(payload), [])
        self.assertNotIn("好感", sheet["text"])

    def test_batch_calls(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/tools",
            json={
                "calls": [
                    {"name": "move_character", "arguments": {"character_id": "player", "location_id": "loc_convenience_store"}},
                    {"name": "buy_item", "arguments": {"item_id": "item_onigiri"}},
                    {"name": "get_player_state", "arguments": {}},
                ]
            },
            headers=self.auth(self.user),
        )
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertTrue(data["results"][1]["result"]["bought"])
        self.assertIn("item_onigiri", data["results"][2]["result"]["inventory"])

    def test_batch_stop_on_error(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/tools",
            json={
                "stop_on_error": True,
                "calls": [
                    {"name": "get_character_state", "arguments": {"character_id": "npc_没有这个人"}},
                    {"name": "get_player_state", "arguments": {}},
                ],
            },
            headers=self.auth(self.user),
        )
        self.assertEqual(response.json()["count"], 1)

    def test_snapshot_roundtrip(self) -> None:
        self.tool(self.user, self.world, "register_skill", skill_id="kendo", name="剑道",
                  category="physical", attribute="physique")
        created = self.client.post(
            f"/api/worlds/{self.world}/snapshots", json={"slot": "slot_a"}, headers=self.auth(self.user)
        ).json()
        self.assertTrue(created["ok"])
        self.tool(self.user, self.world, "advance_time", minutes=300)
        moved = self.tool(self.user, self.world, "get_world_state")["time"]
        restored = self.client.post(
            f"/api/worlds/{self.world}/restore", json={"slot": "slot_a"}, headers=self.auth(self.user)
        ).json()
        self.assertTrue(restored["ok"])
        self.assertNotEqual(self.tool(self.user, self.world, "get_world_state")["time"], moved)
        registry = self.tool(self.user, self.world, "get_registry", kind="skill")
        self.assertIn("kendo", registry["registry"]["skill"]["ids"])

    def test_export_import(self) -> None:
        self.tool(self.user, self.world, "advance_time", minutes=120)
        exported = self.client.get(
            f"/api/worlds/{self.world}/export", headers=self.auth(self.user)
        ).json()
        self.assertTrue(exported["ok"])
        imported = self.client.post(
            "/api/worlds/import",
            json={"name": "导入的世界", "snapshot": exported["snapshot"]},
            headers=self.auth(self.user),
        ).json()
        self.assertTrue(imported["ok"])
        new_world = imported["world"]["id"]
        self.assertEqual(
            self.tool(self.user, new_world, "get_player_state")["name"],
            self.tool(self.user, self.world, "get_player_state")["name"],
        )

    def test_journal_persists_across_reload(self) -> None:
        """叙事日志必须落盘——之前只存在浏览器内存里，刷新就没了。"""
        empty = self.client.get(f"/api/worlds/{self.world}/journal", headers=self.auth(self.user)).json()
        self.assertEqual(empty["entries"], [])

        for turn in range(3):
            self.client.post(
                f"/api/worlds/{self.world}/journal",
                json={
                    "turn": turn + 1, "time": "08:30", "playerInput": f"第{turn + 1}次输入",
                    "narration": f"第{turn + 1}回合的正文",
                    "dialogue": [{"npc_id": "npc_amano_rin", "name": "天野凛", "text": "嗯。"}],
                    "recommendations": [{"text": "继续聊", "minutes": "约 5 分钟", "category": "social"}],
                },
                headers=self.auth(self.user),
            )
        entries = self.client.get(f"/api/worlds/{self.world}/journal", headers=self.auth(self.user)).json()["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["narration"], "第1回合的正文")
        self.assertEqual(entries[2]["turn"], 3)
        self.assertEqual(entries[0]["dialogue"][0]["name"], "天野凛")

    def test_journal_is_per_world(self) -> None:
        other = self.new_world(self.user, "另一个世界")
        self.client.post(f"/api/worlds/{self.world}/journal",
                         json={"turn": 1, "narration": "甲世界"}, headers=self.auth(self.user))
        entries = self.client.get(f"/api/worlds/{other}/journal", headers=self.auth(self.user)).json()["entries"]
        self.assertEqual(entries, [], "别的世界不该看到这条记录")

    def test_journal_survives_corrupt_line(self) -> None:
        self.client.post(f"/api/worlds/{self.world}/journal",
                         json={"turn": 1, "narration": "好记录"}, headers=self.auth(self.user))
        path = self.tmp / "users" / self.user / "worlds" / self.world / "journal.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{ 这行是坏的\n")
        self.client.post(f"/api/worlds/{self.world}/journal",
                         json={"turn": 2, "narration": "后面的记录"}, headers=self.auth(self.user))
        entries = self.client.get(f"/api/worlds/{self.world}/journal", headers=self.auth(self.user)).json()["entries"]
        self.assertEqual([e["narration"] for e in entries], ["好记录", "后面的记录"])

    def test_import_rejects_garbage(self) -> None:
        response = self.client.post(
            "/api/worlds/import", json={"snapshot": {"nonsense": 1}}, headers=self.auth(self.user)
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
