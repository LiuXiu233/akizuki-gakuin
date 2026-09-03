"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { apiConfig, imageCredentials, useSettings, type LLMOrigin } from "@/lib/store";
import type { Health, PipelineInfo } from "@/lib/types";

import { Field, Modal, Spinner, Tabs, Toggle } from "./ui";

type Tab = "connection" | "llm" | "pipeline" | "image" | "about";

const ORIGIN_INFO: Record<LLMOrigin, { title: string; detail: string }> = {
  backend: {
    title: "自建后端",
    detail: "key 随每次请求发给你的后端，后端用完即弃、不落盘；也可以完全不填，用后端环境变量里的 key。支持全部流水线。",
  },
  vercel: {
    title: "Vercel 边缘",
    detail: "key 存在 Vercel 的环境变量（LLM_API_KEY）里，后端把 Vercel 当成上游端点。你自己的服务器上不存在任何 key。支持全部流水线。",
  },
  browser: {
    title: "浏览器直连",
    detail: "key 只留在这台设备，直接请求上游。服务器和 Vercel 都碰不到它。代价：编排在浏览器里做，只能跑单 Agent；上游必须允许跨域。",
  },
};

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const settings = useSettings();
  const [tab, setTab] = useState<Tab>("connection");
  const [health, setHealth] = useState<Health | null>(null);
  const [pipelines, setPipelines] = useState<PipelineInfo[]>([]);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    const cfg = apiConfig(settings);
    api.health(cfg).then(setHealth).catch(() => setHealth(null));
    api.pipelines(cfg).then((data) => setPipelines(data.pipelines)).catch(() => setPipelines([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, settings.backendUrl, settings.transport]);

  async function testBackend() {
    setChecking(true);
    setMessage(null);
    try {
      const result = await api.health(apiConfig(settings));
      setHealth(result);
      setMessage({ ok: true, text: `连上了：引擎 ${result.engine_version}${result.auth_required ? "（需要口令）" : ""}` });
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : String(error) });
    } finally {
      setChecking(false);
    }
  }

  async function testLLM() {
    setChecking(true);
    setMessage(null);
    try {
      if (settings.llm.origin === "browser") {
        const { callBrowserLLM } = await import("@/lib/llm-browser");
        const result = await callBrowserLLM(
          { provider: settings.llm.provider, baseUrl: settings.llm.baseUrl, apiKey: settings.llm.apiKey, model: settings.llm.model },
          "你是连通性测试端点。", [{ role: "user", content: "回复两个字：可用" }], [], { maxTokens: 16 },
        );
        setMessage({ ok: true, text: `可用：${result.text.slice(0, 40)}` });
      } else {
        const credentials = settings.llm.origin === "vercel"
          ? { provider: settings.llm.provider, base_url: `${window.location.origin}/api/llm/${settings.llm.provider}`, api_key: "vercel-managed", model: settings.llm.model }
          : { provider: settings.llm.provider, base_url: settings.llm.baseUrl || undefined, api_key: settings.llm.apiKey || undefined, model: settings.llm.model || undefined };
        const result = await api.verifyLLM(apiConfig(settings), credentials);
        setMessage(result.ok
          ? { ok: true, text: `可用：${result.reply ?? ""}` }
          : { ok: false, text: result.error ?? "验证失败" });
      }
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : String(error) });
    } finally {
      setChecking(false);
    }
  }

  async function testImage() {
    setChecking(true);
    setMessage(null);
    try {
      const cfg = apiConfig(settings);
      const result = await api.generateImage(cfg, "probe", {
        kind: "scene", subject_id: "probe", credentials: imageCredentials(settings), probe: true,
      });
      setMessage(result.ok ? { ok: true, text: "图像服务可用" } : { ok: false, text: result.error ?? "不可用" });
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : String(error) });
    } finally {
      setChecking(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="设置" wide>
      <Tabs<Tab>
        active={tab}
        onChange={setTab}
        tabs={[
          { id: "connection", label: "连接" },
          { id: "llm", label: "模型" },
          { id: "pipeline", label: "Agent" },
          { id: "image", label: "立绘" },
          { id: "about", label: "关于" },
        ]}
      />

      <div className="mt-4">
        {tab === "connection" ? (
          <>
            <Field label="后端地址" hint="运行 python3 -m server 的机器。本地开发填 http://localhost:8000">
              <input className="field" value={settings.backendUrl}
                     onChange={(event) => settings.set("backendUrl", event.target.value)} placeholder="http://localhost:8000" />
            </Field>

            <Field label="访问方式"
                   hint="页面是 https 而后端是 http 时，浏览器会拦截直连请求；这时选「经 Vercel 转发」。">
              <div className="flex gap-2">
                {(["direct", "proxy"] as const).map((value) => (
                  <button key={value} onClick={() => settings.set("transport", value)}
                          className={settings.transport === value ? "btn-primary flex-1" : "btn-ghost flex-1"}>
                    {value === "direct" ? "浏览器直连后端" : "经 Vercel 转发"}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="访问口令" hint={health?.auth_required ? "这个后端要求口令" : "这个后端没有设置口令，留空即可"}>
              <input className="field" type="password" value={settings.accessPassword}
                     onChange={(event) => settings.set("accessPassword", event.target.value)} placeholder="留空表示不需要" />
            </Field>

            <Field label="用户令牌（存档钥匙）" hint="换设备时把它复制过去，就能继续同一批存档。">
              <input className="field font-mono text-xs" value={settings.userToken}
                     onChange={(event) => settings.set("userToken", event.target.value.trim())} placeholder="首次进入会自动生成" />
            </Field>

            <button className="btn-ghost w-full" onClick={testBackend} disabled={checking}>
              {checking ? <Spinner label="测试中…" /> : "测试后端连接"}
            </button>
          </>
        ) : null}

        {tab === "llm" ? (
          <>
            <Field label="请求从哪里发出">
              <div className="grid gap-2 sm:grid-cols-3">
                {(Object.keys(ORIGIN_INFO) as LLMOrigin[]).map((value) => (
                  <button key={value} onClick={() => settings.setLLM({ origin: value })}
                          className={`rounded-xl border p-2.5 text-left text-xs transition ${
                            settings.llm.origin === value ? "border-dusk bg-dusk/10" : "border-paper-edge bg-white/60 hover:bg-white"}`}>
                    <div className="text-sm">{ORIGIN_INFO[value].title}</div>
                  </button>
                ))}
              </div>
            </Field>
            <p className="mb-4 rounded-xl bg-black/[0.03] p-2.5 text-[11px] leading-relaxed text-ink-mute">
              {ORIGIN_INFO[settings.llm.origin].detail}
            </p>

            <Field label="接口格式">
              <div className="flex gap-2">
                {(["openai", "anthropic"] as const).map((value) => (
                  <button key={value} onClick={() => settings.setLLM({ provider: value })}
                          className={settings.llm.provider === value ? "btn-primary flex-1" : "btn-ghost flex-1"}>
                    {value === "openai" ? "OpenAI 兼容" : "Anthropic"}
                  </button>
                ))}
              </div>
            </Field>

            {settings.llm.origin !== "vercel" ? (
              <>
                <Field label="Base URL"
                       hint={settings.llm.provider === "openai"
                         ? "写到 /v1 为止，例如 https://api.openai.com/v1 或你的中转站地址"
                         : "例如 https://api.anthropic.com"}>
                  <input className="field" value={settings.llm.baseUrl}
                         onChange={(event) => settings.setLLM({ baseUrl: event.target.value })}
                         placeholder={settings.llm.provider === "openai" ? "https://api.openai.com/v1" : "https://api.anthropic.com"} />
                </Field>
                <Field label="API Key"
                       hint={settings.llm.origin === "browser"
                         ? "只保存在这台设备的浏览器里。"
                         : health?.server_llm_configured
                           ? "留空则使用服务器预置的 key。填了就用你自己的（服务器不保存）。"
                           : "服务器没有预置 key，必须填你自己的。"}>
                  <input className="field font-mono text-xs" type="password" value={settings.llm.apiKey}
                         onChange={(event) => settings.setLLM({ apiKey: event.target.value })} placeholder="sk-..." />
                </Field>
              </>
            ) : (
              <p className="mb-3 rounded-xl bg-amber/10 p-2.5 text-[11px] leading-relaxed">
                这种模式需要在 Vercel 项目里设置环境变量 <code>LLM_API_KEY</code>
                （可选 <code>LLM_BASE_URL</code>）。前端不保存任何 key。
              </p>
            )}

            <Field label="模型" hint={health?.server_llm_model ? `服务器默认：${health.server_llm_model}` : "例如 gpt-4o / claude-sonnet-5"}>
              <input className="field" value={settings.llm.model}
                     onChange={(event) => settings.setLLM({ model: event.target.value })} placeholder="留空用默认" />
            </Field>

            <button className="btn-ghost w-full" onClick={testLLM} disabled={checking}>
              {checking ? <Spinner label="测试中…" /> : "测试模型连通性"}
            </button>
          </>
        ) : null}

        {tab === "pipeline" ? (
          <>
            <Field label="Agent 流水线">
              <div className="space-y-2">
                {pipelines.map((pipeline) => {
                  const disabled = settings.llm.origin === "browser" && pipeline.id !== "single";
                  return (
                    <button key={pipeline.id} disabled={disabled}
                            onClick={() => settings.set("pipeline", pipeline.id)}
                            className={`w-full rounded-xl border p-3 text-left transition ${
                              settings.pipeline === pipeline.id ? "border-dusk bg-dusk/10"
                                : disabled ? "border-paper-edge/50 opacity-45"
                                  : "border-paper-edge bg-white/60 hover:bg-white"}`}>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">{pipeline.name}</span>
                        <span className="chip">{pipeline.stage_count} 阶段</span>
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{pipeline.description}</p>
                      <p className="mt-1 font-mono text-[10px] text-ink-mute">{pipeline.stages.join(" → ")}</p>
                      {disabled ? <p className="mt-1 text-[10px] text-sakura-deep">浏览器直连模式只支持单 Agent</p> : null}
                    </button>
                  );
                })}
                {!pipelines.length ? <p className="text-xs text-ink-mute">连上后端后才能看到可用流水线。</p> : null}
              </div>
            </Field>

            <Toggle checked={settings.stream} onChange={(value) => settings.set("stream", value)}
                    label="流式输出"
                    hint="实时显示每个阶段的进度和逐字叙事。关掉则等整回合跑完一次性显示。" />
            <Toggle checked={settings.debug} onChange={(value) => settings.set("debug", value)}
                    label="调试面板"
                    hint="显示每个 Agent 的耗时、token 消耗和真实的工具调用日志。" />
          </>
        ) : null}

        {tab === "image" ? (
          <>
            <Toggle checked={settings.image.enabled} onChange={(value) => settings.setImage({ enabled: value })}
                    label="开启立绘 / 头像 / 场景图"
                    hint="不开启时界面用配色占位符代替，完全不会请求任何图像服务。" />

            {settings.image.enabled ? (
              <>
                <Toggle checked={settings.image.auto} onChange={(value) => settings.setImage({ auto: value })}
                        label="自动出图"
                        hint="由画师 Agent 判断值得画的时刻自动生成。关掉则只在你点「生成」时出图。" />

                <Field label="接口格式">
                  <div className="flex gap-2">
                    {(["openai", "custom"] as const).map((value) => (
                      <button key={value} onClick={() => settings.setImage({ provider: value })}
                              className={settings.image.provider === value ? "btn-primary flex-1" : "btn-ghost flex-1"}>
                        {value === "openai" ? "OpenAI 兼容" : "自定义模板"}
                      </button>
                    ))}
                  </div>
                </Field>

                <Field label="Base URL" hint={settings.image.provider === "openai" ? "例如 https://api.openai.com/v1" : "完整的生成接口地址"}>
                  <input className="field" value={settings.image.baseUrl}
                         onChange={(event) => settings.setImage({ baseUrl: event.target.value })} />
                </Field>
                <Field label="API Key">
                  <input className="field font-mono text-xs" type="password" value={settings.image.apiKey}
                         onChange={(event) => settings.setImage({ apiKey: event.target.value })} />
                </Field>
                <Field label="模型">
                  <input className="field" value={settings.image.model}
                         onChange={(event) => settings.setImage({ model: event.target.value })} placeholder="gpt-image-1 / dall-e-3 / ..." />
                </Field>
                <Field label="尺寸">
                  <input className="field" value={settings.image.size}
                         onChange={(event) => settings.setImage({ size: event.target.value })} placeholder="1024x1024" />
                </Field>
                <Field label="画风前缀" hint="留空使用内置的日系动画风。会加在每个提示词前面，保证整体风格统一。">
                  <input className="field" value={settings.image.style}
                         onChange={(event) => settings.setImage({ style: event.target.value })}
                         placeholder="anime illustration, soft light, warm colors" />
                </Field>

                {settings.image.provider === "custom" ? (
                  <>
                    <Field label="请求体模板"
                           hint="JSON 模板，{prompt} / {model} / {size} 会被替换。用来接 SD WebUI、ComfyUI、国内厂商等。">
                      <textarea className="field h-24 font-mono text-[11px]" value={settings.image.requestTemplate}
                                onChange={(event) => settings.setImage({ requestTemplate: event.target.value })}
                                placeholder='{"prompt": "{prompt}", "steps": 28, "width": 1024, "height": 1024}' />
                    </Field>
                    <Field label="取图路径" hint="从响应 JSON 里取图的路径，例如 data.0.b64_json 或 images.0">
                      <input className="field font-mono text-xs" value={settings.image.responsePath}
                             onChange={(event) => settings.setImage({ responsePath: event.target.value })} placeholder="images.0" />
                    </Field>
                  </>
                ) : null}

                <Toggle checked={settings.image.sfw} onChange={(value) => settings.setImage({ sfw: value })}
                        label="强制 SFW 提示词"
                        hint="主流图像服务会直接拒绝尺度内容。关掉只在你接私有服务时有意义。文字叙事不受这个开关影响。" />

                <button className="btn-ghost w-full" onClick={testImage} disabled={checking}>
                  {checking ? <Spinner label="测试中…" /> : "测试图像服务"}
                </button>
              </>
            ) : null}
          </>
        ) : null}

        {tab === "about" ? (
          <div className="space-y-3 text-xs leading-relaxed text-ink-mute">
            <p>
              <strong className="text-ink">秋月学院</strong> —— 规则全部在服务端的 Python 引擎里：
              D20 判定、七维单向关系、成长与防刷、时间与日程、后台世界模拟、存档。
              模型只负责内容，改变世界只能通过工具。
            </p>
            <p>
              骰子不会决定 NPC 的选择；关系数值永远不会显示给你；
              NPC 在你不在场时也在生活。这些都是引擎强制的，不是提示词里的约定。
            </p>
            {health ? (
              <div className="rounded-xl bg-white/60 p-3 font-mono text-[11px]">
                <div>engine {health.engine_version} · server {health.server_version}</div>
                <div>口令 {health.auth_required ? "已启用" : "未设置"}</div>
                <div>服务器 LLM {health.server_llm_configured ? `已配置（${health.server_llm_model}）` : "未配置"}</div>
                <div>服务器图像 {health.server_image_configured ? "已配置" : "未配置"}</div>
              </div>
            ) : null}
            <button className="btn-ghost w-full" onClick={() => { settings.reset(); setMessage({ ok: true, text: "已恢复默认设置" }); }}>
              恢复默认设置
            </button>
          </div>
        ) : null}

        {message ? (
          <p className={`mt-3 rounded-xl p-2.5 text-xs ${message.ok ? "bg-moss/15 text-moss" : "bg-sakura-pale text-sakura-deep"}`}>
            {message.text}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
