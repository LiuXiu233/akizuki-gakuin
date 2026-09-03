"use client";

import { useEffect, useState } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { Transport } from "./api";
import type { ActionContext, DialogueLine, MetaBundle, PlayerState, Recommendation, StageInfo, ToolLogEntry, TurnPanel, WorldMeta, WorldState } from "./types";

/**
 * LLM 请求从哪里发出 —— 三种不同的信任模型：
 * - backend：key 由浏览器随请求发给你的后端（后端不保存），或用后端环境变量里的 key。
 *            支持全部流水线。
 * - vercel ：key 存在 Vercel 环境变量里，后端把 Vercel 的 /api/llm 当成上游端点。
 *            你的服务器上不存在任何 key。支持全部流水线。
 * - browser：key 只留在浏览器，由浏览器直接请求上游。服务器与 Vercel 都碰不到它。
 *            代价：只能跑单 Agent 流水线（编排在浏览器里做）。
 */
export type LLMOrigin = "backend" | "vercel" | "browser";

export interface LLMSettings {
  origin: LLMOrigin;
  provider: "openai" | "anthropic";
  baseUrl: string;
  apiKey: string;
  model: string;
}

export interface ImageSettings {
  enabled: boolean;
  auto: boolean;
  provider: "openai" | "custom";
  baseUrl: string;
  apiKey: string;
  model: string;
  size: string;
  style: string;
  sfw: boolean;
  requestTemplate: string;
  responsePath: string;
}

export interface Settings {
  backendUrl: string;
  transport: Transport;
  accessPassword: string;
  userToken: string;
  pipeline: string;
  stream: boolean;
  uiMode: "immersive" | "panel";
  debug: boolean;
  llm: LLMSettings;
  image: ImageSettings;
}

interface SettingsStore extends Settings {
  set<K extends keyof Settings>(key: K, value: Settings[K]): void;
  setLLM(patch: Partial<LLMSettings>): void;
  setImage(patch: Partial<ImageSettings>): void;
  reset(): void;
}

const DEFAULT_BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

const defaults: Settings = {
  backendUrl: DEFAULT_BACKEND,
  transport: "direct",
  accessPassword: "",
  userToken: "",
  pipeline: "multi",
  stream: true,
  uiMode: "immersive",
  debug: false,
  llm: { origin: "backend", provider: "openai", baseUrl: "", apiKey: "", model: "" },
  image: {
    enabled: false, auto: false, provider: "openai", baseUrl: "", apiKey: "",
    model: "", size: "1024x1024", style: "", sfw: true,
    requestTemplate: "", responsePath: "",
  },
};

export const useSettings = create<SettingsStore>()(
  persist(
    (set) => ({
      ...defaults,
      set: (key, value) => set({ [key]: value } as any),
      setLLM: (patch) => set((state) => ({ llm: { ...state.llm, ...patch } })),
      setImage: (patch) => set((state) => ({ image: { ...state.image, ...patch } })),
      reset: () => set({ ...defaults }),
    }),
    { name: "akizuki-settings", version: 1 },
  ),
);

/**
 * 等待 localStorage 里的设置恢复完成。
 *
 * 没有这一步的话，页面刷新后第一次渲染拿到的是默认值（令牌为空），
 * 挂载时发出的请求会直接 401。
 */
export function useHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    if (useSettings.persist.hasHydrated()) { setHydrated(true); return; }
    return useSettings.persist.onFinishHydration(() => setHydrated(true));
  }, []);
  return hydrated;
}

/** 一条叙事记录 —— 回合日志与沉浸模式共用。 */
export interface LogEntry {
  id: string;
  turn: number;
  time: string;
  playerInput: string;
  narration: string;
  dialogue: DialogueLine[];
  checkText: string;
  growthText: string;
  panelText: string;
  recommendations: Recommendation[];
  randomEvent: { name: string; category: string } | null;
  stages: StageInfo[];
  toolLog: ToolLogEntry[];
  usage: { total_tokens: number } | null;
  errors: string[];
}

interface GameStore {
  worldId: string | null;
  meta: MetaBundle | null;
  worldMeta: WorldMeta | null;
  world: WorldState | null;
  player: PlayerState | null;
  panel: TurnPanel | null;
  context: ActionContext | null;
  log: LogEntry[];
  images: Record<string, string>;      // `${kind}:${subject_id}` -> url
  busy: boolean;
  stageProgress: { stage: string; name: string; index: number; total: number } | null;
  liveText: string;
  liveDialogue: DialogueLine[];
  toolFeed: ToolLogEntry[];
  error: string | null;

  setWorld(payload: Partial<GameStore>): void;
  pushLog(entry: LogEntry): void;
  setImage(key: string, url: string): void;
  resetRun(): void;
  clear(): void;
}

export const useGame = create<GameStore>((set) => ({
  worldId: null, meta: null, worldMeta: null, world: null, player: null,
  panel: null, context: null, log: [], images: {}, busy: false,
  stageProgress: null, liveText: "", liveDialogue: [], toolFeed: [], error: null,

  setWorld: (payload) => set(payload as any),
  pushLog: (entry) => set((state) => ({ log: [...state.log, entry].slice(-200) })),
  setImage: (key, url) => set((state) => ({ images: { ...state.images, [key]: url } })),
  resetRun: () => set({ stageProgress: null, liveText: "", liveDialogue: [], toolFeed: [], error: null }),
  clear: () => set({
    worldId: null, worldMeta: null, world: null, player: null, panel: null,
    context: null, log: [], images: {}, busy: false, stageProgress: null,
    liveText: "", liveDialogue: [], toolFeed: [], error: null,
  }),
}));

/** 从设置里拼出 API 配置。 */
export function apiConfig(settings: Settings) {
  return {
    backendUrl: settings.backendUrl,
    transport: settings.transport,
    accessPassword: settings.accessPassword || undefined,
    userToken: settings.userToken || undefined,
  };
}

/** 拼出发给后端的 LLM 凭据。origin 决定 key 到底走哪条路。 */
export function llmCredentials(settings: Settings): Record<string, unknown> {
  const { llm } = settings;
  if (llm.origin === "vercel") {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return {
      provider: llm.provider,
      base_url: `${origin}/api/llm/${llm.provider}`,
      api_key: "vercel-managed",     // 真正的 key 由 Vercel 环境变量注入
      model: llm.model || undefined,
    };
  }
  return {
    provider: llm.provider,
    base_url: llm.baseUrl || undefined,
    api_key: llm.apiKey || undefined,   // 留空则用后端环境变量里的 key
    model: llm.model || undefined,
  };
}

export function imageCredentials(settings: Settings): Record<string, unknown> {
  const { image } = settings;
  return {
    provider: image.provider,
    base_url: image.baseUrl || undefined,
    api_key: image.apiKey || undefined,
    model: image.model || undefined,
    size: image.size || undefined,
    style: image.style || undefined,
    sfw: image.sfw,
    request_template: image.requestTemplate || undefined,
    response_path: image.responsePath || undefined,
  };
}
