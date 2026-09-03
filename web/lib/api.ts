/**
 * 后端客户端。
 *
 * 两种传输方式：
 * - direct：浏览器直接请求你的后端（后端需开 CORS；https 页面要求后端也是 https）
 * - proxy ：经由 Next.js 的 /api/proxy 服务端转发（不受混合内容与 CORS 限制）
 */

import type {
  ActionContext, CharacterState, Health, MetaBundle, PipelineInfo,
  PlayerState, TurnPanel, TurnResult, WorldMeta, WorldState,
} from "./types";

export type Transport = "direct" | "proxy";

export interface ApiConfig {
  backendUrl: string;
  accessPassword?: string;
  userToken?: string;
  transport: Transport;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

function url(cfg: ApiConfig, path: string): string {
  if (cfg.transport === "proxy") return `/api/proxy${path}`;
  return `${cfg.backendUrl.replace(/\/$/, "")}${path}`;
}

function headers(cfg: ApiConfig, extra: Record<string, string> = {}): Record<string, string> {
  const out: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (cfg.accessPassword) out["X-Access-Password"] = cfg.accessPassword;
  if (cfg.userToken) out["X-User-Token"] = cfg.userToken;
  if (cfg.transport === "proxy") out["X-Backend-Url"] = cfg.backendUrl;
  return out;
}

async function request<T>(cfg: ApiConfig, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(cfg, path), { ...init, headers: headers(cfg, (init.headers as any) ?? {}) });
  } catch (error) {
    throw new ApiError(
      `连不上后端（${cfg.backendUrl}）。检查地址是否正确、服务是否启动；如果页面是 https 而后端是 http，请在设置里切换到「经 Vercel 转发」。`,
      0, error,
    );
  }
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = (data && (data.detail || data.error)) || response.statusText;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), response.status, data);
  }
  return data as T;
}

/** 带鉴权头拉取二进制资源（图片）。`<img src>` 发不了自定义头，只能这样。 */
export async function fetchAsset(cfg: ApiConfig, path: string): Promise<Blob> {
  const response = await fetch(url(cfg, path), { headers: headers(cfg) });
  if (!response.ok) throw new ApiError(`图片加载失败（${response.status}）`, response.status);
  return response.blob();
}

export const api = {
  health: (cfg: ApiConfig) => request<Health>(cfg, "/api/health"),
  meta: (cfg: ApiConfig) => request<MetaBundle>(cfg, "/api/meta"),
  pipelines: (cfg: ApiConfig) => request<{ pipelines: PipelineInfo[] }>(cfg, "/api/pipelines"),
  lore: (cfg: ApiConfig, topic: string) => request<{ topic: string; text: string }>(cfg, `/api/lore/${topic}`),

  createSession: (cfg: ApiConfig, userId?: string) =>
    request<{ user_id: string; created: boolean; worlds: WorldMeta[]; settings: Record<string, unknown> }>(
      cfg, "/api/session", { method: "POST", body: JSON.stringify({ user_id: userId ?? null }) },
    ),
  readSession: (cfg: ApiConfig) =>
    request<{ user_id: string; worlds: WorldMeta[]; settings: Record<string, unknown> }>(cfg, "/api/session"),
  saveUserSettings: (cfg: ApiConfig, settings: Record<string, unknown>) =>
    request<{ ok: boolean }>(cfg, "/api/session/settings", { method: "PUT", body: JSON.stringify({ settings }) }),

  listWorlds: (cfg: ApiConfig) => request<{ worlds: WorldMeta[] }>(cfg, "/api/worlds"),
  createWorld: (cfg: ApiConfig, body: { name: string; seed?: number | null; pipeline?: string }) =>
    request<{ world: WorldMeta }>(cfg, "/api/worlds", { method: "POST", body: JSON.stringify(body) }),
  readWorld: (cfg: ApiConfig, worldId: string) =>
    request<{
      meta: WorldMeta; world_state: WorldState; player: PlayerState;
      panel: TurnPanel; context: ActionContext; nearby: { characters: any[] };
    }>(cfg, `/api/worlds/${worldId}`),
  patchWorld: (cfg: ApiConfig, worldId: string, body: { name?: string; pipeline?: string }) =>
    request<{ world: WorldMeta }>(cfg, `/api/worlds/${worldId}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteWorld: (cfg: ApiConfig, worldId: string) =>
    request<{ ok: boolean }>(cfg, `/api/worlds/${worldId}`, { method: "DELETE" }),
  exportWorld: (cfg: ApiConfig, worldId: string) =>
    request<{ snapshot: Record<string, unknown> }>(cfg, `/api/worlds/${worldId}/export`),
  importWorld: (cfg: ApiConfig, name: string, snapshot: Record<string, unknown>) =>
    request<{ world: WorldMeta }>(cfg, "/api/worlds/import", { method: "POST", body: JSON.stringify({ name, snapshot }) }),
  listSnapshots: (cfg: ApiConfig, worldId: string) =>
    request<{ saves: Array<{ slot: string; date: string; time: string; turn: number }> }>(cfg, `/api/worlds/${worldId}/snapshots`),
  createSnapshot: (cfg: ApiConfig, worldId: string, slot: string) =>
    request<{ ok: boolean; slot: string }>(cfg, `/api/worlds/${worldId}/snapshots`, { method: "POST", body: JSON.stringify({ slot }) }),
  restoreSnapshot: (cfg: ApiConfig, worldId: string, slot: string) =>
    request<{ ok: boolean }>(cfg, `/api/worlds/${worldId}/restore`, { method: "POST", body: JSON.stringify({ slot }) }),

  /** 调用任意引擎工具 —— 唯一的世界写入通道 */
  tool: <T = any>(cfg: ApiConfig, worldId: string, name: string, args: Record<string, unknown> = {}) =>
    request<T>(cfg, `/api/worlds/${worldId}/tools/${name}`, {
      method: "POST", body: JSON.stringify({ arguments: args }),
    }),
  tools: (cfg: ApiConfig, worldId: string, calls: Array<{ name: string; arguments: Record<string, unknown> }>, stopOnError = false) =>
    request<{ results: Array<{ name: string; result: any }> }>(cfg, `/api/worlds/${worldId}/tools`, {
      method: "POST", body: JSON.stringify({ calls, stop_on_error: stopOnError }),
    }),
  character: (cfg: ApiConfig, worldId: string, characterId: string) =>
    request<CharacterState>(cfg, `/api/worlds/${worldId}/tools/get_character_state`, {
      method: "POST", body: JSON.stringify({ arguments: { character_id: characterId } }),
    }),

  turn: (cfg: ApiConfig, worldId: string, body: Record<string, unknown>) =>
    request<TurnResult>(cfg, `/api/worlds/${worldId}/turn`, { method: "POST", body: JSON.stringify(body) }),

  verifyLLM: (cfg: ApiConfig, credentials: Record<string, unknown>) =>
    request<{ ok: boolean; reply?: string; error?: string; provider?: Record<string, unknown> }>(
      cfg, "/api/llm/verify",
      { method: "POST", body: JSON.stringify({ messages: [{ role: "user", content: "ping" }], credentials }) },
    ),

  generateImage: (cfg: ApiConfig, worldId: string, body: Record<string, unknown>) =>
    request<{ ok: boolean; image?: { url: string; kind: string; subject_id: string }; error?: string; skipped?: string }>(
      cfg, `/api/worlds/${worldId}/images`, { method: "POST", body: JSON.stringify(body) },
    ),
  /** 图像服务自检：不需要存档，但会真实生成一张图（产生一次计费）。 */
  probeImage: (cfg: ApiConfig, credentials: Record<string, unknown>) =>
    request<{ ok: boolean; error?: string; preview?: string; model?: string; provider?: string; size?: string }>(
      cfg, "/api/images/probe", { method: "POST", body: JSON.stringify({ credentials }) },
    ),
  listImages: (cfg: ApiConfig, worldId: string) =>
    request<{ images: Array<{ kind: string; subject_id: string; url: string; created_at: number }> }>(
      cfg, `/api/worlds/${worldId}/images`,
    ),

  /** SSE 流式回合。逐个事件回调，返回最终结果。 */
  async streamTurn(
    cfg: ApiConfig,
    worldId: string,
    body: Record<string, unknown>,
    onEvent: (event: any) => void,
    signal?: AbortSignal,
  ): Promise<TurnResult | null> {
    const response = await fetch(url(cfg, `/api/worlds/${worldId}/turn/stream`), {
      method: "POST",
      headers: headers(cfg),
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
    if (!response.ok || !response.body) {
      const text = await response.text().catch(() => "");
      let detail = text;
      try { detail = JSON.parse(text).detail ?? text; } catch { /* 原样 */ }
      throw new ApiError(detail || `流式请求失败（${response.status}）`, response.status);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let final: TurnResult | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        let payload: any;
        try { payload = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }
        onEvent(payload);
        if (payload.type === "turn_end") final = payload.turn as TurnResult;
      }
    }
    return final;
  },
};
