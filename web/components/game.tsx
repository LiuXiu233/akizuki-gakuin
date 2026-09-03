"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { apiConfig, useGame, useSettings } from "@/lib/store";
import type { LogEntry } from "@/lib/store";

import { Portrait } from "./Portrait";
import {
  DebugPanel, GalleryPanel, LogPanel, MapPanel, NearbyPanel, SchedulePanel, StatusPanel,
} from "./panels";
import { Card, Empty, Modal, Spinner, Tabs } from "./ui";

/* ------------------------------------------------------------------ 顶栏 */

export function TopBar({
  worldName, onOpenSettings, onToggleMode, onOpenMenu,
}: { worldName: string; onOpenSettings: () => void; onToggleMode: () => void; onOpenMenu: (tab: PanelTab) => void }) {
  const world = useGame((state) => state.world);
  const settings = useSettings();
  const immersive = settings.uiMode === "immersive";

  return (
    <div className={`flex items-center gap-3 px-4 py-2.5 text-xs ${immersive ? "text-paper/85" : "text-ink-mute"}`}>
      <a href="/" className={immersive ? "font-serif text-sm text-paper" : "font-serif text-sm text-ink"}>秋月学院</a>
      <span className="opacity-50">·</span>
      <span className="truncate">{worldName}</span>
      {world ? (
        <>
          <span className="ml-auto tabular-nums">{world.date.slice(5)} {world.weekday_zh}</span>
          <span className="tabular-nums">{world.time}</span>
          <span className="hidden sm:inline">{world.weather_zh}</span>
          <span className="hidden max-w-[10rem] truncate sm:inline">{world.location.name}</span>
        </>
      ) : <span className="ml-auto" />}
      <button className={immersive ? "btn-quiet btn-sm text-paper/80 hover:bg-white/10" : "btn-quiet btn-sm"}
              onClick={() => onOpenMenu("status")}>面板</button>
      <button className={immersive ? "btn-quiet btn-sm text-paper/80 hover:bg-white/10" : "btn-quiet btn-sm"}
              onClick={onToggleMode}>{immersive ? "面板模式" : "沉浸模式"}</button>
      <button className={immersive ? "btn-quiet btn-sm text-paper/80 hover:bg-white/10" : "btn-quiet btn-sm"}
              onClick={onOpenSettings}>设置</button>
    </div>
  );
}

/* ------------------------------------------------------------------ 叙事流 */

export function NarrativeStream({ dark = false }: { dark?: boolean }) {
  const { log, liveText, liveDialogue, busy, stageProgress, error } = useGame();
  const settings = useSettings();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [log.length, liveText, liveDialogue.length, busy]);

  const recent = log.slice(-12);

  return (
    <div className={`space-y-6 ${dark ? "text-paper" : ""}`}>
      {!recent.length && !busy ? (
        <p className={`text-sm leading-relaxed ${dark ? "text-paper/70" : "text-ink-mute"}`}>
          输入你想做的事。可以很具体（「去天台吃午饭」），也可以是一串连续的打算
          （「先去便利店买饮料，再去音乐室找凛，如果她在就问她晚上有没有空」）。
        </p>
      ) : null}

      {recent.map((entry) => <TurnBlock key={entry.id} entry={entry} dark={dark} />)}

      {busy ? (
        <div className="animate-fade-up space-y-2">
          {stageProgress ? (
            <div className={`flex items-center gap-2 text-xs ${dark ? "text-paper/70" : "text-ink-mute"}`}>
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-current/25 border-t-current" />
              {stageProgress.name}
              <span className="opacity-60">{stageProgress.index + 1}/{stageProgress.total}</span>
            </div>
          ) : <Spinner label="世界正在运转…" />}

          {liveDialogue.map((line, index) => (
            <p key={index} className="text-[15px] leading-relaxed">
              <span className={dark ? "text-sakura" : "text-sakura-deep"}>{line.name}</span>　{line.text}
            </p>
          ))}

          {liveText ? (
            <div className="prose-narrative caret whitespace-pre-wrap opacity-90">{liveText}</div>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p className={`rounded-xl p-3 text-xs ${dark ? "bg-sakura-deep/25 text-paper" : "bg-sakura-pale text-sakura-deep"}`}>
          {error}
        </p>
      ) : null}

      {settings.debug && !busy ? null : null}
      <div ref={endRef} />
    </div>
  );
}

function TurnBlock({ entry, dark }: { entry: LogEntry; dark: boolean }) {
  return (
    <article className="animate-fade-up space-y-3">
      {entry.playerInput ? (
        <p className={`border-l-2 pl-3 text-xs ${dark ? "border-paper/25 text-paper/60" : "border-dusk/25 text-ink-mute"}`}>
          「{entry.playerInput}」
        </p>
      ) : null}

      {entry.narration ? (
        <div className="prose-narrative whitespace-pre-wrap">{entry.narration}</div>
      ) : null}

      {entry.dialogue.map((line, index) => (
        <p key={index} className="prose-narrative">
          <span className={dark ? "text-sakura" : "text-sakura-deep"}>{line.name}</span>　{line.text}
        </p>
      ))}

      {entry.checkText ? (
        <pre className={`whitespace-pre-wrap rounded-xl p-3 font-sans text-[11px] leading-relaxed ${
          dark ? "bg-white/10 text-paper/85" : "bg-black/[0.04]"}`}>{entry.checkText}</pre>
      ) : null}

      {entry.growthText ? (
        <pre className={`whitespace-pre-wrap rounded-xl p-3 font-sans text-[11px] leading-relaxed ${
          dark ? "bg-moss/25 text-paper/90" : "bg-moss/10"}`}>{entry.growthText}</pre>
      ) : null}

      {entry.randomEvent ? (
        <p className={`text-[11px] ${dark ? "text-paper/50" : "text-ink-mute"}`}>◇ {entry.randomEvent.name}</p>
      ) : null}

      {entry.errors.length ? (
        <p className={`rounded-xl p-2.5 text-[11px] ${dark ? "bg-sakura-deep/25 text-paper/90" : "bg-sakura-pale text-sakura-deep"}`}>
          {entry.errors.join("；")}
        </p>
      ) : null}
    </article>
  );
}

/* ------------------------------------------------------------------ 输入与推荐 */

export function Recommendations({
  onPick, dark = false,
}: { onPick: (text: string) => void; dark?: boolean }) {
  const log = useGame((state) => state.log);
  const busy = useGame((state) => state.busy);
  const last = log[log.length - 1];
  const items = last?.recommendations ?? [];
  if (!items.length || busy) return null;

  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {items.map((item, index) => (
        <button key={index} onClick={() => onPick(item.text)}
                className={`group rounded-xl border px-3 py-1.5 text-left text-xs transition ${
                  dark ? "border-white/15 bg-white/10 text-paper hover:bg-white/20"
                       : "border-paper-edge bg-white/70 hover:bg-white"}`}>
          <span className="opacity-50">{index + 1}.</span> {item.text}
          {item.minutes ? <span className="ml-1.5 opacity-55">{item.minutes}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function InputBar({
  onSubmit, onCancel, dark = false,
}: { onSubmit: (text: string) => void; onCancel: () => void; dark?: boolean }) {
  const busy = useGame((state) => state.busy);
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const text = value.trim();
    if (!text || busy) return;
    setValue("");
    onSubmit(text);
  }

  return (
    <div className="flex items-end gap-2">
      <textarea
        ref={ref}
        rows={1}
        value={value}
        disabled={busy}
        onChange={(event) => {
          setValue(event.target.value);
          const element = event.target;
          element.style.height = "auto";
          element.style.height = `${Math.min(element.scrollHeight, 140)}px`;
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
        }}
        placeholder={busy ? "世界正在运转……" : "你想做什么？（Enter 发送，Shift+Enter 换行）"}
        className={`field max-h-36 flex-1 resize-none ${
          dark ? "border-white/15 bg-black/40 text-paper placeholder:text-paper/40 focus:border-white/30 focus:ring-white/10" : ""}`}
      />
      {busy ? (
        <button className="btn-ghost" onClick={onCancel}>中断</button>
      ) : (
        <button className="btn-primary" onClick={submit} disabled={!value.trim()}>行动</button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 侧面板 */

export type PanelTab = "status" | "nearby" | "gallery" | "map" | "schedule" | "log" | "debug";

export function PanelTabs({
  worldId, active, onChange, onMove,
}: { worldId: string; active: PanelTab; onChange: (tab: PanelTab) => void; onMove: (id: string, name: string) => void }) {
  const settings = useSettings();
  const tabs: Array<{ id: PanelTab; label: string }> = [
    { id: "status", label: "角色" },
    { id: "nearby", label: "在场" },
    { id: "gallery", label: "图鉴" },
    { id: "map", label: "地图" },
    { id: "schedule", label: "日程" },
    { id: "log", label: "日志" },
  ];
  if (settings.debug) tabs.push({ id: "debug", label: "调试" });

  return (
    <>
      <Tabs tabs={tabs} active={active} onChange={onChange} />
      <div className="mt-3">
        {active === "status" ? <StatusPanel /> : null}
        {active === "nearby" ? <NearbyPanel worldId={worldId} /> : null}
        {active === "gallery" ? <GalleryPanel worldId={worldId} /> : null}
        {active === "map" ? <MapPanel worldId={worldId} onMove={onMove} /> : null}
        {active === "schedule" ? <SchedulePanel /> : null}
        {active === "log" ? <LogPanel /> : null}
        {active === "debug" ? <DebugPanel /> : null}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ 沉浸模式舞台 */

export function ImmersiveStage({ worldId }: { worldId: string }) {
  const world = useGame((state) => state.world);
  const context = useGame((state) => state.context);
  const images = useGame((state) => state.images);
  const settings = useSettings();
  const [selected, setSelected] = useState<string | null>(null);

  const sceneKey = `scene:${world?.location.id ?? ""}`;
  const sceneUrl = images[sceneKey];
  const people = (context?.nearby_characters ?? []).slice(0, 3);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {sceneUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={sceneUrl} alt="" className="h-full w-full object-cover opacity-70" />
      ) : (
        <div className="h-full w-full bg-gradient-to-br from-dusk-deep via-dusk to-dusk-soft" />
      )}
      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/35 to-black/45" />

      {!sceneUrl && world ? (
        <div className="absolute inset-x-0 top-[16%] text-center text-paper/70">
          <div className="font-serif text-2xl tracking-[0.3em] sm:text-3xl">{world.location.name}</div>
          <div className="mt-2 text-xs tracking-[0.25em]">
            {world.date.slice(5).replace("-", " 月 ")} 日　{world.weekday_zh}　{world.time}　{world.weather_zh}
          </div>
          {world.location.description ? (
            <p className="mx-auto mt-4 max-w-md px-8 text-[11px] leading-relaxed text-paper/45">
              {world.location.description}
            </p>
          ) : null}
          {!settings.image.enabled ? (
            <p className="mt-4 text-[10px] text-paper/25">（在设置里配置文生图后，这里会显示场景插画）</p>
          ) : null}
        </div>
      ) : null}

      <div className="pointer-events-auto absolute inset-x-0 bottom-[42%] flex items-end justify-center gap-4 px-6">
        {people.map((person) => (
          <button key={person.id} onClick={() => setSelected(person.id)} className="group text-center">
            <Portrait kind="portrait" subjectId={person.id} name={person.name} worldId={worldId}
                      className="h-32 w-24 opacity-95 transition group-hover:opacity-100 sm:h-48 sm:w-36"
                      showGenerate={settings.image.enabled} />
            <div className="mt-1.5 text-[11px] text-paper/80">{person.name}</div>
          </button>
        ))}
      </div>

      {selected ? (
        <div className="pointer-events-auto">
          <CharacterQuickView worldId={worldId} characterId={selected} onClose={() => setSelected(null)} />
        </div>
      ) : null}
    </div>
  );
}

function CharacterQuickView({
  worldId, characterId, onClose,
}: { worldId: string; characterId: string; onClose: () => void }) {
  const settings = useSettings();
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api.character(apiConfig(settings), worldId, characterId).then(setData).catch(() => setData(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [characterId]);
  return (
    <Modal open onClose={onClose} title={data?.name ?? "…"}>
      {data ? (
        <div className="space-y-3 text-sm">
          <Portrait kind="portrait" subjectId={data.id} name={data.name} worldId={worldId} className="aspect-[3/4] w-40" />
          <div className="chip chip-on">{data.relationship_with_player?.label}</div>
          <p className="leading-relaxed text-ink-mute">{data.appearance}</p>
          <p className="leading-relaxed">{data.personality}</p>
        </div>
      ) : <Spinner label="读取中…" />}
    </Modal>
  );
}

/* ------------------------------------------------------------------ 存档点 */

export function SnapshotBar({ worldId }: { worldId: string }) {
  const settings = useSettings();
  const [saves, setSaves] = useState<Array<{ slot: string; date: string; time: string; turn: number }>>([]);
  const [message, setMessage] = useState("");

  async function refresh() {
    const result = await api.listSnapshots(apiConfig(settings), worldId).catch(() => null);
    setSaves(result?.saves ?? []);
  }
  useEffect(() => { void refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [worldId]);

  async function create() {
    const slot = `save_${new Date().toISOString().slice(5, 16).replace(/[-T:]/g, "")}`;
    await api.createSnapshot(apiConfig(settings), worldId, slot);
    setMessage("已存档");
    void refresh();
    setTimeout(() => setMessage(""), 1500);
  }

  async function restore(slot: string) {
    if (!confirm(`回到存档点 ${slot}？之后发生的一切都会丢失。`)) return;
    await api.restoreSnapshot(apiConfig(settings), worldId, slot);
    location.reload();
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button className="btn-ghost btn-sm" onClick={create}>打存档点</button>
        {message ? <span className="text-xs text-moss">{message}</span> : null}
      </div>
      {saves.length ? (
        <div className="space-y-1">
          {saves.map((save) => (
            <button key={save.slot} onClick={() => restore(save.slot)}
                    className="flex w-full items-center justify-between rounded-lg bg-white/60 px-2.5 py-1.5 text-xs hover:bg-white">
              <span className="font-mono">{save.slot}</span>
              <span className="text-ink-mute">{save.date} {save.time} · 第 {save.turn} 回合</span>
            </button>
          ))}
        </div>
      ) : <Empty>还没有存档点</Empty>}
    </div>
  );
}
