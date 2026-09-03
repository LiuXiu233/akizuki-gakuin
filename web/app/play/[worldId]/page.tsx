"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ImmersiveStage, InputBar, NarrativeStream, PanelTabs, Recommendations, SnapshotBar, TopBar,
  type PanelTab,
} from "@/components/game";
import { StatusPanel } from "@/components/panels";
import { SettingsDialog } from "@/components/SettingsDialog";
import { Card, Modal, Section, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { apiConfig, useGame, useHydrated, useSettings } from "@/lib/store";
import { useTurnRunner } from "@/lib/turn";

export default function PlayPage({ params }: { params: Promise<{ worldId: string }> }) {
  const { worldId } = use(params);
  const router = useRouter();
  const settings = useSettings();
  const hydrated = useHydrated();
  const game = useGame();
  const { run, cancel } = useTurnRunner(worldId);

  const [loading, setLoading] = useState(true);
  const [fatal, setFatal] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [drawer, setDrawer] = useState<PanelTab | null>(null);
  const [tab, setTab] = useState<PanelTab>("status");
  const [worldName, setWorldName] = useState("");

  const load = useCallback(async () => {
    // 从 store 现取，避免 useCallback 闭包捕获到水合前的空令牌
    const current = useSettings.getState();
    const cfg = apiConfig(current);
    try {
      const [snapshot, meta, images] = await Promise.all([
        api.readWorld(cfg, worldId),
        game.meta ? Promise.resolve(game.meta) : api.meta(cfg),
        api.listImages(cfg, worldId).catch(() => ({ images: [] })),
      ]);
      if (!snapshot.player?.name) { router.replace("/"); return; }
      setWorldName(snapshot.meta.name);
      game.setWorld({
        worldId,
        meta,
        worldMeta: snapshot.meta,
        world: snapshot.world_state,
        player: snapshot.player,
        panel: snapshot.panel,
        context: snapshot.context,
      });
      for (const image of images.images ?? []) {
        game.setImage(`${image.kind}:${image.subject_id}`, image.url);
      }
      if (snapshot.meta.pipeline && snapshot.meta.pipeline !== current.pipeline) {
        current.set("pipeline", snapshot.meta.pipeline);
      }
    } catch (exception) {
      setFatal(exception instanceof ApiError ? exception.message : String(exception));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldId]);

  useEffect(() => {
    if (!hydrated) return;
    void load();
    return () => useGame.getState().clear();
  }, [load, hydrated]);

  const move = useCallback(async (locationId: string, name: string) => {
    setDrawer(null);
    await run(`我去${name}。`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);

  if (loading || !hydrated) {
    return <main className="grid min-h-screen place-items-center"><Spinner label="正在进入秋月…" /></main>;
  }
  if (fatal) {
    return (
      <main className="mx-auto grid min-h-screen max-w-md place-items-center px-5">
        <Card className="p-6 text-center">
          <p className="text-sm text-sakura-deep">{fatal}</p>
          <div className="mt-4 flex justify-center gap-2">
            <button className="btn-primary" onClick={() => location.reload()}>重试</button>
            <button className="btn-ghost" onClick={() => router.push("/")}>回到存档列表</button>
          </div>
        </Card>
      </main>
    );
  }

  const immersive = settings.uiMode === "immersive";
  const topBar = (
    <TopBar worldName={worldName}
            onOpenSettings={() => setSettingsOpen(true)}
            onToggleMode={() => settings.set("uiMode", immersive ? "panel" : "immersive")}
            onOpenMenu={(value) => { setTab(value); setDrawer(value); }} />
  );

  /* ---------------- 沉浸模式 ---------------- */
  if (immersive) {
    return (
      <main className="relative h-[100dvh] overflow-hidden bg-dusk-deep">
        <ImmersiveStage worldId={worldId} />

        <div className="relative z-10 flex h-full flex-col">
          <div className="border-b border-white/10 bg-black/30 backdrop-blur">{topBar}</div>

          <div className="flex-1" />

          <div className="glass-dark mx-3 mb-3 flex max-h-[58%] flex-col rounded-3xl p-4 sm:mx-6 sm:mb-6 sm:p-5">
            <div className="scroll-thin mb-3 flex-1 overflow-y-auto pr-1">
              <NarrativeStream dark />
            </div>
            <Recommendations dark onPick={(text) => void run(text)} />
            <InputBar dark onSubmit={(text) => void run(text)} onCancel={cancel} />
          </div>
        </div>

        <Modal open={!!drawer} onClose={() => setDrawer(null)} title="角色与世界" wide>
          <PanelTabs worldId={worldId} active={tab} onChange={setTab} onMove={move} />
          <div className="mt-5 border-t border-paper-edge pt-4">
            <Section title="存档点"><SnapshotBar worldId={worldId} /></Section>
          </div>
        </Modal>
        <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </main>
    );
  }

  /* ---------------- 面板模式 ---------------- */
  return (
    <main className="mx-auto flex h-[100dvh] max-w-[1500px] flex-col">
      <div className="border-b border-paper-edge">{topBar}</div>

      <div className="grid min-h-0 flex-1 gap-4 p-4 lg:grid-cols-[290px_minmax(0,1fr)_330px]">
        <aside className="scroll-thin hidden min-h-0 overflow-y-auto lg:block">
          <Card className="p-4"><StatusPanel /></Card>
        </aside>

        <section className="flex min-h-0 flex-col">
          <Card className="flex min-h-0 flex-1 flex-col p-5">
            <div className="scroll-thin mb-4 flex-1 overflow-y-auto pr-2">
              <NarrativeStream />
            </div>
            <Recommendations onPick={(text) => void run(text)} />
            <InputBar onSubmit={(text) => void run(text)} onCancel={cancel} />
          </Card>
        </section>

        <aside className="scroll-thin hidden min-h-0 overflow-y-auto lg:block">
          <Card className="p-4">
            <PanelTabs worldId={worldId} active={tab === "status" ? "nearby" : tab}
                       onChange={setTab} onMove={move} />
            <div className="mt-5 border-t border-paper-edge pt-4">
              <Section title="存档点"><SnapshotBar worldId={worldId} /></Section>
            </div>
          </Card>
        </aside>
      </div>

      <Modal open={!!drawer} onClose={() => setDrawer(null)} title="角色与世界" wide>
        <PanelTabs worldId={worldId} active={tab} onChange={setTab} onMove={move} />
      </Modal>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </main>
  );
}
