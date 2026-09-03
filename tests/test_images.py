"""文生图测试：提示词构造、SFW 约束、缓存、两种上游格式、路径安全。

用本地假上游，不联网、不花钱。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401

    HAS_SERVER = True
except Exception:  # noqa: BLE001
    HAS_SERVER = False

# 1x1 透明 PNG
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeImageHandler(BaseHTTPRequestHandler):
    """同时假装是 OpenAI 兼容端点和一个自定义端点。"""

    prompts: list[str] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeImageHandler.prompts.append(body.get("prompt", ""))
        if "/custom" in self.path:
            payload = {"images": [base64.b64encode(PIXEL).decode()]}
        elif "/fail" in self.path:
            self.send_response(400)
            data = json.dumps({"error": {"message": "内容被拒绝"}}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        else:
            payload = {"data": [{"b64_json": base64.b64encode(PIXEL).decode()}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # noqa: D102
        pass


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class ImageTestCase(unittest.IsolatedAsyncioTestCase):
    server: HTTPServer
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), FakeImageHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    async def asyncSetUp(self) -> None:
        from engine.tools import GameSession, use_session
        from server.config import Settings

        FakeImageHandler.prompts.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="akizuki_img_"))
        (self.tmp / "state").mkdir()
        (self.tmp / "saves").mkdir()
        self.session = GameSession(root=ROOT, data_root=self.tmp, seed=1, autoload=False)
        with use_session(self.session):
            from engine import tools as T

            T.create_player(name="佐藤悠", age=19, preset="preset_artist")
        self.settings = Settings()
        self.settings.image_enabled = True
        self.settings.image_sfw = False      # 让测试能验证客户端开关
        from server.images import ImageService

        self.service = ImageService(self.settings, self.session, self.tmp / "images")

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def creds(self, **overrides):
        return {
            "provider": "openai", "base_url": self.base, "api_key": "test-key",
            "model": "fake-image", "size": "512x512", **overrides,
        }

    # -- 提示词 ------------------------------------------------------
    async def test_npc_prompt_uses_engine_appearance(self) -> None:
        config = self.service.resolve(self.creds())
        prompt = self.service.build_prompt("portrait", "npc_amano_rin", "", config)
        self.assertIn("天野凛", prompt)
        self.assertIn("偏短的黑发", prompt)
        self.assertIn("adult", prompt.lower())

    async def test_person_prompt_always_marks_adult(self) -> None:
        config = self.service.resolve(self.creds())
        for kind in ("avatar", "portrait"):
            prompt = self.service.build_prompt(kind, "npc_hoshino_makoto", "", config)
            self.assertIn("18+", prompt)

    async def test_scene_prompt_has_no_people(self) -> None:
        config = self.service.resolve(self.creds())
        prompt = self.service.build_prompt("scene", "loc_rooftop", "", config)
        self.assertIn("天台", prompt)
        self.assertIn("No characters in frame", prompt)

    async def test_sfw_strips_risky_words(self) -> None:
        config = self.service.resolve(self.creds(sfw=True))
        prompt = self.service.build_prompt("portrait", "npc_amano_rin", "nude, lingerie, smiling", config)
        self.assertNotIn("nude", prompt.lower())
        self.assertNotIn("lingerie", prompt.lower())
        self.assertIn("smiling", prompt)
        self.assertIn("safe for work", prompt)

    async def test_server_can_force_sfw(self) -> None:
        self.settings.image_sfw = True
        config = self.service.resolve(self.creds(sfw=False))
        self.assertTrue(config["sfw"], "服务器强制 SFW 时客户端不能关掉")

    async def test_client_can_relax_when_server_allows(self) -> None:
        self.settings.image_sfw = False
        config = self.service.resolve(self.creds(sfw=False))
        self.assertFalse(config["sfw"])

    # -- 生成 --------------------------------------------------------
    async def test_generate_and_cache(self) -> None:
        first = await self.service.generate(world_id="w1", kind="avatar", subject_id="npc_amano_rin",
                                            credentials=self.creds())
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        path = self.service.path_for("avatar", "npc_amano_rin")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), PIXEL)

        second = await self.service.generate(world_id="w1", kind="avatar", subject_id="npc_amano_rin",
                                             credentials=self.creds())
        self.assertTrue(second["cached"], "第二次应该直接用缓存，不再请求上游")
        self.assertEqual(len(FakeImageHandler.prompts), 1)

        forced = await self.service.generate(world_id="w1", kind="avatar", subject_id="npc_amano_rin",
                                             credentials=self.creds(), force=True)
        self.assertFalse(forced["cached"])
        self.assertEqual(len(FakeImageHandler.prompts), 2)

    async def test_custom_template_provider(self) -> None:
        result = await self.service.generate(
            world_id="w1", kind="scene", subject_id="loc_courtyard",
            credentials={
                "provider": "custom", "base_url": f"{self.base}/custom", "api_key": "",
                "model": "sd", "size": "768x768",
                "request_template": '{"prompt": "{prompt}", "steps": 28, "model": "{model}"}',
                "response_path": "images.0",
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.service.path_for("scene", "loc_courtyard").exists())

    async def test_custom_template_bad_json(self) -> None:
        from server.images import ImageError

        with self.assertRaises(ImageError):
            await self.service.generate(
                world_id="w1", kind="scene", subject_id="loc_park",
                credentials={"provider": "custom", "base_url": f"{self.base}/custom",
                             "request_template": "{ 这不是 JSON", "response_path": "images.0"},
            )

    async def test_upstream_error_is_reported(self) -> None:
        from server.images import ImageError

        with self.assertRaises(ImageError) as ctx:
            await self.service.generate(world_id="w1", kind="avatar", subject_id="player",
                                        credentials=self.creds(base_url=f"{self.base}/fail"))
        self.assertIn("内容被拒绝", str(ctx.exception))

    async def test_skipped_without_config(self) -> None:
        result = await self.service.generate(world_id="w1", kind="avatar", subject_id="player",
                                             credentials={"provider": "openai", "api_key": "", "base_url": ""})
        self.assertFalse(result["ok"])
        self.assertIn("skipped", result)

    async def test_disabled_globally(self) -> None:
        self.settings.image_enabled = False
        result = await self.service.generate(world_id="w1", kind="avatar", subject_id="player",
                                             credentials=self.creds())
        self.assertIn("skipped", result)

    async def test_bad_subject_id_rejected(self) -> None:
        from server.images import ImageError

        with self.assertRaises(ImageError):
            await self.service.generate(world_id="w1", kind="avatar", subject_id="../../etc/passwd",
                                        credentials=self.creds())

    async def test_unknown_kind_rejected(self) -> None:
        from server.images import ImageError

        with self.assertRaises(ImageError):
            await self.service.generate(world_id="w1", kind="poster", subject_id="x",
                                        credentials=self.creds())

    async def test_listing(self) -> None:
        await self.service.generate(world_id="w1", kind="avatar", subject_id="npc_amano_rin",
                                    credentials=self.creds())
        await self.service.generate(world_id="w1", kind="scene", subject_id="loc_rooftop",
                                    credentials=self.creds())
        listing = self.service.listing("w1")
        self.assertEqual(len(listing), 2)
        self.assertTrue(all(item["url"].startswith("/api/worlds/w1/images/file/") for item in listing))


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class TestImageAPI(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        import server.deps
        import server.routes.meta
        from server.config import get_settings

        self.tmp = Path(tempfile.mkdtemp(prefix="akizuki_imgapi_"))
        os.environ["AKIZUKI_DATA_DIR"] = str(self.tmp)
        os.environ.pop("AKIZUKI_ACCESS_PASSWORD", None)
        get_settings.cache_clear()
        server.deps.reset_state()
        server.routes.meta._READONLY_SESSION = None

        from server.app import create_app

        self.client = TestClient(create_app())
        self.user = self.client.post("/api/session", json={}).json()["user_id"]
        self.headers = {"X-User-Token": self.user}
        self.world = self.client.post("/api/worlds", json={"name": "图像测试"}, headers=self.headers).json()["world"]["id"]

    def tearDown(self) -> None:
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AKIZUKI_DATA_DIR", None)

    def test_list_empty(self) -> None:
        data = self.client.get(f"/api/worlds/{self.world}/images", headers=self.headers).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["images"], [])

    def test_generate_without_config_is_graceful(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/images",
            json={"kind": "avatar", "subject_id": "npc_amano_rin"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertIn("skipped", data)

    def test_path_traversal_blocked(self) -> None:
        response = self.client.get(
            f"/api/worlds/{self.world}/images/file/avatar/..%2F..%2Fmeta.json", headers=self.headers
        )
        self.assertIn(response.status_code, (400, 404))

    def test_unknown_kind_blocked(self) -> None:
        response = self.client.get(
            f"/api/worlds/{self.world}/images/file/secrets/x.png", headers=self.headers
        )
        self.assertEqual(response.status_code, 400)

    def test_probe_needs_no_world(self) -> None:
        """回归：设置里的「测试图像服务」不能借用假的 world_id。

        之前它往 /api/worlds/probe/images 发请求，被引擎的 ID 校验拦成
        400「非法的世界 ID」，看起来像是用户填错了配置。
        """
        response = self.client.post(
            "/api/images/probe",
            json={"credentials": {"provider": "openai", "api_key": "", "base_url": ""}},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertIn("API Key", data["error"])          # 说的是配置问题，不是世界 ID

    def test_fake_world_id_message_is_actionable(self) -> None:
        response = self.client.post(
            "/api/worlds/probe/images",
            json={"kind": "scene", "subject_id": "x"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("12 位十六进制", response.json()["detail"])

    def test_images_isolated_between_users(self) -> None:
        other = self.client.post("/api/session", json={}).json()["user_id"]
        response = self.client.get(
            f"/api/worlds/{self.world}/images", headers={"X-User-Token": other}
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
