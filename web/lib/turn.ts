"use client";

/** 回合执行：后端流水线（流式 / 非流式）与浏览器直连三条路径。 */

import { useCallback, useRef } from "react";

import { api, ApiError } from "./api";
import { callBrowserLLM, type BrowserWire } from "./llm-browser";
import { apiConfig, imageCredentials, llmCredentials, useGame, useSettings, type LogEntry } from "./store";
import type { DialogueLine, Recommendation, TurnResult } from "./types";

const FORBIDDEN = new Set(["new_game", "load_game"]);

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function parseRecommendations(text: string): Recommendation[] {
  const markers = ["【你可以……】", "【你可以…】", "【你可以】", "【接下来】"];
  let block = "";
  for (const marker of markers) {
    const index = text.indexOf(marker);
    if (index >= 0) { block = text.slice(index + marker.length); break; }
  }
  if (!block) return [];
  const out: Recommendation[] = [];
  let current: Recommendation | null = null;
  for (const raw of block.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("你也可以")) break;
    // 模型可能写成 "1. xxx" 也可能写成 "- xxx"，时长可能在括号里
    const bullet = line.match(/^(?:\d+\s*[.、)）]|[-*•·—]\s|\d+\s*[:：])\s*(.+)$/);
    if (bullet) {
      if (current) out.push(current);
      let body = bullet[1].trim();
      let minutes = "";
      let category = "";
      const paren = body.match(/[（(]([^（()）]*)[)）]\s*$/);
      if (paren && /(分钟|小时|自由|约)/.test(paren[1])) {
        body = body.slice(0, paren.index).trim().replace(/[·\-—\s]+$/, "");
        const pieces = paren[1].split(/[，,、/|]/).map((p) => p.trim());
        minutes = pieces.find((p) => /(分钟|小时|自由)/.test(p)) ?? paren[1].trim();
        category = pieces.find((p) => !/(分钟|小时|自由)/.test(p)) ?? "";
      }
      current = { text: body, minutes, category };
    } else if (current && !current.minutes && /(约|分钟|小时|时间自由)/.test(line)) {
      current.minutes = line;
    }
  }
  if (current) out.push(current);
  return out.filter((item) => item.text).slice(0, 5);
}

function stripRecommendations(text: string): string {
  for (const marker of ["【你可以……】", "【你可以…】", "【你可以】", "【接下来】"]) {
    const index = text.indexOf(marker);
    if (index >= 0) return text.slice(0, index).trim();
  }
  return text.trim();
}

export function useTurnRunner(worldId: string) {
  const abortRef = useRef<AbortController | null>(null);
  const cacheRef = useRef<{ agentMd?: string; tools?: any[] }>({});

  const run = useCallback(
    async (input: string) => {
      const settings = useSettings.getState();
      const game = useGame.getState();
      const cfg = apiConfig(settings);
      if (game.busy) return;

      game.resetRun();
      game.setWorld({ busy: true });

      const started: LogEntry = {
        id: newId(),
        turn: game.world?.turn ?? 0,
        time: game.world?.time ?? "",
        playerInput: input,
        narration: "", dialogue: [], checkText: "", growthText: "", panelText: "",
        recommendations: [], randomEvent: null, stages: [], toolLog: [], usage: null, errors: [],
      };

      try {
        let result: TurnResult | null = null;

        if (settings.llm.origin === "browser") {
          result = await runInBrowser(worldId, input, cfg, cacheRef);
        } else if (settings.stream) {
          const controller = new AbortController();
          abortRef.current = controller;
          const liveDialogue: DialogueLine[] = [];
          result = await api.streamTurn(
            cfg, worldId,
            {
              input,
              pipeline: settings.pipeline,
              credentials: llmCredentials(settings),
              debug: settings.debug,
              generate_images: settings.image.enabled && settings.image.auto,
            },
            (event) => {
              const state = useGame.getState();
              switch (event.type) {
                case "stage_start":
                  state.setWorld({
                    stageProgress: { stage: event.stage, name: event.name, index: event.index, total: event.total },
                  });
                  break;
                case "delta":
                  state.setWorld({ liveText: state.liveText + (event.text ?? "") });
                  break;
                case "dialogue":
                  liveDialogue.push({ npc_id: event.npc_id, name: event.name, text: event.text });
                  state.setWorld({ liveDialogue: [...liveDialogue] });
                  break;
                case "tool_call":
                case "tool_result":
                  state.setWorld({ toolFeed: [...state.toolFeed, event].slice(-60) });
                  break;
                case "stage_error":
                  started.errors.push(`${event.stage}：${event.message}`);
                  break;
                case "error":
                  started.errors.push(event.error ?? event.message ?? "未知错误");
                  break;
              }
            },
            controller.signal,
          );
        } else {
          result = await api.turn(cfg, worldId, {
            input,
            pipeline: settings.pipeline,
            credentials: llmCredentials(settings),
            debug: settings.debug,
            generate_images: settings.image.enabled && settings.image.auto,
          });
        }

        if (!result) throw new Error("这一回合没有产出结果");

        const entry: LogEntry = {
          ...started,
          turn: result.turn ?? started.turn,
          narration: result.narration_clean || result.narration,
          dialogue: result.dialogue ?? [],
          checkText: result.check_text ?? "",
          growthText: result.growth_text ?? "",
          panelText: result.panel_text ?? "",
          recommendations: result.recommendations ?? [],
          randomEvent: result.random_event ? { name: result.random_event.name, category: result.random_event.category } : null,
          stages: result.stages ?? [],
          toolLog: result.tool_log ?? [],
          usage: result.usage ? { total_tokens: result.usage.total_tokens } : null,
          errors: [...started.errors, ...(result.stage_errors ?? [])],
        };
        useGame.getState().pushLog(entry);
        useGame.getState().setWorld({
          world: result.world ?? game.world,
          panel: result.panel ?? game.panel,
          context: result.context ?? game.context,
        });

        // 刷新玩家状态（技能/知识可能升级了）
        const player = await api.tool(cfg, worldId, "get_player_state");
        useGame.getState().setWorld({ player });

        // 自动出图（后端会自己判断有没有配置）
        if (settings.image.enabled && settings.image.auto && result.images?.length) {
          for (const suggestion of result.images.slice(0, 2)) {
            try {
              const image = await api.generateImage(cfg, worldId, {
                kind: suggestion.kind,
                subject_id: suggestion.subject_id,
                prompt_extra: suggestion.prompt,
                credentials: imageCredentials(settings),
              });
              if (image.ok && image.image) {
                useGame.getState().setImage(`${image.image.kind}:${image.image.subject_id}`, image.image.url);
              }
            } catch { /* 出图失败不影响回合 */ }
          }
        }
      } catch (error) {
        const message = error instanceof ApiError || error instanceof Error ? error.message : String(error);
        useGame.getState().setWorld({ error: message });
        started.errors.push(message);
        if (started.playerInput) useGame.getState().pushLog({ ...started, narration: "" });
      } finally {
        abortRef.current = null;
        useGame.getState().setWorld({ busy: false, stageProgress: null, liveText: "", liveDialogue: [] });
      }
    },
    [worldId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    useGame.getState().setWorld({ busy: false, stageProgress: null });
  }, []);

  return { run, cancel };
}

/** 浏览器直连：单 Agent 编排在本地跑，工具调用仍然走后端引擎。 */
async function runInBrowser(
  worldId: string,
  input: string,
  cfg: ReturnType<typeof apiConfig>,
  cacheRef: { current: { agentMd?: string; tools?: any[] } },
): Promise<TurnResult> {
  const settings = useSettings.getState();
  const game = useGame.getState();

  if (!settings.llm.apiKey) throw new Error("浏览器直连模式需要在设置里填入你自己的 API key");

  if (!cacheRef.current.agentMd) {
    cacheRef.current.agentMd = (await api.lore(cfg, "agent")).text;
  }
  if (!cacheRef.current.tools) {
    const schema = await fetch(
      cfg.transport === "proxy" ? "/api/proxy/api/tools/schema" : `${cfg.backendUrl}/api/tools/schema`,
      { headers: { ...(cfg.accessPassword ? { "X-Access-Password": cfg.accessPassword } : {}), ...(cfg.transport === "proxy" ? { "X-Backend-Url": cfg.backendUrl } : {}) } },
    ).then((r) => r.json());
    cacheRef.current.tools = (schema.tools ?? []).filter((tool: any) => !FORBIDDEN.has(tool.name));
  }

  const panel = game.panel?.text ?? "";
  const contextText = JSON.stringify(
    {
      time: game.world?.time, date: game.world?.date, weekday: game.world?.weekday_zh,
      location: game.world?.location, nearby: game.context?.nearby_characters,
      romance_opportunity: game.context?.romance_opportunity,
      recent_recommendations: game.context?.recent_recommendations,
    },
    null, 2,
  );
  const system = `${cacheRef.current.agentMd}\n\n## 当前世界（由引擎提供）\n\n${panel}\n\n${contextText}`;

  const messages: BrowserWire[] = [{ role: "user", content: input || "（玩家没有明确输入，让时间自然往前走一点）" }];
  const toolLog: any[] = [];
  let text = "";
  let usage = { input_tokens: 0, output_tokens: 0 };

  for (let iteration = 0; iteration < 8; iteration += 1) {
    const result = await callBrowserLLM(
      { provider: settings.llm.provider, baseUrl: settings.llm.baseUrl, apiKey: settings.llm.apiKey, model: settings.llm.model },
      system, messages, cacheRef.current.tools as any, { temperature: 0.8, maxTokens: 4000 },
    );
    usage = { input_tokens: usage.input_tokens + result.usage.input, output_tokens: usage.output_tokens + result.usage.output };
    if (!result.toolCalls.length) { text = result.text; break; }
    messages.push({ role: "assistant", content: result.text, toolCalls: result.toolCalls });
    for (const call of result.toolCalls) {
      useGame.getState().setWorld({
        toolFeed: [
          ...useGame.getState().toolFeed,
          { type: "tool_call" as const, stage: "browser", name: call.name, arguments: call.arguments },
        ].slice(-60),
      });
      const toolResult = FORBIDDEN.has(call.name)
        ? { ok: false, error: "该工具在回合中不可用" }
        : await api.tool(cfg, worldId, call.name, call.arguments).catch((error) => ({ ok: false, error: String(error) }));
      toolLog.push({ type: "tool_call", stage: "browser", name: call.name, arguments: call.arguments });
      toolLog.push({ type: "tool_result", stage: "browser", name: call.name, ok: !!(toolResult as any).ok, summary: (toolResult as any).error ?? "ok" });
      messages.push({ role: "tool", toolCallId: call.id, name: call.name, content: JSON.stringify(toolResult) });
    }
    text = result.text;
  }

  const [panelResult, worldResult, contextResult] = await Promise.all([
    api.tool(cfg, worldId, "get_turn_panel"),
    api.tool(cfg, worldId, "get_world_state"),
    api.tool(cfg, worldId, "get_action_context"),
  ]);

  return {
    ok: true, pipeline: "browser-single",
    narration: text, narration_clean: stripRecommendations(text),
    check_text: "", growth_text: "", random_event: null,
    dialogue: [], recommendations: parseRecommendations(text), images: [],
    panel: panelResult, panel_text: panelResult.text, world: worldResult, context: contextResult,
    turn: worldResult.turn, stages: [], tool_log: toolLog,
    usage: { ...usage, total_tokens: usage.input_tokens + usage.output_tokens },
  } as TurnResult;
}
