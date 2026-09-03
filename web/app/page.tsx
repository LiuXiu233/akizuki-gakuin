"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { CharacterCreator } from "@/components/CharacterCreator";
import { SettingsDialog } from "@/components/SettingsDialog";
import { Card, Empty, Modal, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { zh } from "@/lib/labels";
import { apiConfig, useGame, useHydrated, useSettings } from "@/lib/store";
import type { Health, MetaBundle, WorldMeta } from "@/lib/types";

export default function StartPage() {
  const router = useRouter();
  const settings = useSettings();
  const hydrated = useHydrated();
  const setWorld = useGame((state) => state.setWorld);

  const [ready, setReady] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [meta, setMeta] = useState<MetaBundle | null>(null);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [creating, setCreating] = useState<{ worldId: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const bootstrap = useCallback(async () => {
    setError("");
    setReady(false);
    const current = useSettings.getState();
    const cfg = apiConfig(current);
    try {
      const healthResult = await api.health(cfg);
      setHealth(healthResult);
      const session = await api.createSession(cfg, current.userToken || undefined);
      if (session.user_id !== current.userToken) current.set("userToken", session.user_id);
      const authed = { ...cfg, userToken: session.user_id };
      setWorlds(session.worlds ?? []);
      const metaResult = await api.meta(authed);
      setMeta(metaResult);
      setWorld({ meta: metaResult });
    } catch (exception) {
      const message = exception instanceof ApiError ? exception.message : String(exception);
      setError(message);
      if (exception instanceof ApiError && exception.status === 401) setSettingsOpen(true);
    } finally {
      setReady(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.backendUrl, settings.transport, settings.accessPassword, settings.userToken]);

  useEffect(() => { if (hydrated) void bootstrap(); }, [bootstrap, hydrated]);

  async function createWorld() {
    setBusy(true);
    try {
      const cfg = apiConfig(settings);
      const result = await api.createWorld(cfg, {
        name: `秋月 · 第 ${worlds.length + 1} 周目`,
        pipeline: settings.pipeline,
      });
      setWorlds((current) => [result.world, ...current]);
      setCreating({ worldId: result.world.id });
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception));
    } finally {
      setBusy(false);
    }
  }

  async function enter(world: WorldMeta) {
    const cfg = apiConfig(settings);
    try {
      const snapshot = await api.readWorld(cfg, world.id);
      if (!snapshot.player?.name) { setCreating({ worldId: world.id }); return; }
      router.push(`/play/${world.id}`);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception));
    }
  }

  async function remove(world: WorldMeta) {
    if (!confirm(`删除「${world.name}」？这个世界里的一切都会消失，无法恢复。`)) return;
    await api.deleteWorld(apiConfig(settings), world.id).catch(() => undefined);
    setWorlds((current) => current.filter((item) => item.id !== world.id));
  }

  async function rename(world: WorldMeta) {
    const name = prompt("新的名字", world.name);
    if (!name) return;
    const result = await api.patchWorld(apiConfig(settings), world.id, { name });
    setWorlds((current) => current.map((item) => (item.id === world.id ? result.world : item)));
  }

  async function exportWorld(world: WorldMeta) {
    const result = await api.exportWorld(apiConfig(settings), world.id);
    const blob = new Blob([JSON.stringify(result.snapshot, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `akizuki-${world.name}-${world.date}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function importWorld(file: File) {
    setBusy(true);
    try {
      const snapshot = JSON.parse(await file.text());
      const result = await api.importWorld(apiConfig(settings), file.name.replace(/\.json$/, ""), snapshot);
      setWorlds((current) => [result.world, ...current]);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-5 py-10 sm:py-16">
      <header className="mb-10 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl tracking-wide sm:text-4xl">秋月学院</h1>
          <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-ink-mute">
            一所海边小城的高等综合学院。没有主线，没有必须攻略的人。
            你可以上课、加入社团、认识人、谈恋爱，或者只是把三年过完。
          </p>
        </div>
        <button className="btn-ghost shrink-0" onClick={() => setSettingsOpen(true)}>设置</button>
      </header>

      {!ready || !hydrated ? (
        <Card className="p-8 text-center"><Spinner label="正在连接世界…" /></Card>
      ) : error ? (
        <Card className="p-6">
          <p className="text-sm text-sakura-deep">{error}</p>
          <p className="mt-2 text-xs leading-relaxed text-ink-mute">
            后端地址：<code>{settings.backendUrl}</code>（{settings.transport === "proxy" ? "经 Vercel 转发" : "浏览器直连"}）
            <br />本地开发请先运行 <code>python3 -m server</code>。
          </p>
          <div className="mt-4 flex gap-2">
            <button className="btn-primary" onClick={() => void bootstrap()}>重试</button>
            <button className="btn-ghost" onClick={() => setSettingsOpen(true)}>打开设置</button>
          </div>
        </Card>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <button className="btn-primary" onClick={createWorld} disabled={busy}>开始新的一年</button>
            <button className="btn-ghost" onClick={() => fileRef.current?.click()} disabled={busy}>导入存档</button>
            <input ref={fileRef} type="file" accept="application/json" className="hidden"
                   onChange={(event) => { const file = event.target.files?.[0]; if (file) void importWorld(file); }} />
            <span className="ml-auto text-xs text-ink-mute">
              {health?.server_llm_configured ? "服务器已配置模型" : "需要在设置里填入你的 API key"}
            </span>
          </div>

          {worlds.length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {worlds.map((world) => (
                <Card key={world.id} className="group p-4">
                  <div className="flex items-start justify-between gap-3">
                    <button className="min-w-0 flex-1 text-left" onClick={() => void enter(world)}>
                      <div className="truncate font-serif text-lg">{world.name}</div>
                      <div className="mt-1 text-xs text-ink-mute">
                        {world.player_name ? `${world.player_name} · ` : "尚未创建角色 · "}
                        {world.date || "—"} {world.time}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1">
                        <span className="chip">第 {world.turn} 回合</span>
                        <span className="chip">认识 {world.npc_count} 人</span>
                        <span className="chip">{zh.pipeline(world.pipeline)}</span>
                        {world.image_count ? <span className="chip">{world.image_count} 张图</span> : null}
                      </div>
                    </button>
                  </div>
                  <div className="mt-3 flex gap-1 opacity-60 transition group-hover:opacity-100">
                    <button className="btn-quiet btn-sm" onClick={() => void enter(world)}>进入</button>
                    <button className="btn-quiet btn-sm" onClick={() => void rename(world)}>重命名</button>
                    <button className="btn-quiet btn-sm" onClick={() => void exportWorld(world)}>导出</button>
                    <button className="btn-quiet btn-sm ml-auto text-sakura-deep" onClick={() => void remove(world)}>删除</button>
                  </div>
                </Card>
              ))}
            </div>
          ) : (
            <Card className="p-10">
              <Empty>还没有存档。点「开始新的一年」，从 4 月 16 日的早晨开始。</Empty>
            </Card>
          )}

          <p className="mt-8 text-[11px] leading-relaxed text-ink-mute">
            存档保存在你的后端上，用「用户令牌」认领。换设备时到设置里复制令牌即可继续。
            <br />本作面向成年用户；世界中不存在未成年角色，这一点由引擎在数据层强制。
          </p>
        </>
      )}

      <Modal open={!!creating} onClose={() => setCreating(null)} title="创建角色" wide>
        {creating && meta ? (
          <CharacterCreator
            meta={meta}
            worldId={creating.worldId}
            onCancel={() => setCreating(null)}
            onDone={() => { const id = creating.worldId; setCreating(null); router.push(`/play/${id}`); }}
          />
        ) : null}
      </Modal>

      <SettingsDialog open={settingsOpen} onClose={() => { setSettingsOpen(false); void bootstrap(); }} />
    </main>
  );
}
