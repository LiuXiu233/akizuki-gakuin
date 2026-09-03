/**
 * 浏览器直连模式下的最小 LLM 客户端。
 *
 * 只在设置里选择「浏览器直连」时使用——那种模式下 key 永远不离开这台设备，
 * 你的服务器和 Vercel 都碰不到它。代价是编排在浏览器里做，只支持单 Agent 流水线。
 */

export interface BrowserTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface BrowserToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface BrowserResult {
  text: string;
  toolCalls: BrowserToolCall[];
  usage: { input: number; output: number };
}

export interface BrowserWire {
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: BrowserToolCall[];
  toolCallId?: string;
  name?: string;
}

export interface BrowserLLMConfig {
  provider: "openai" | "anthropic";
  baseUrl: string;
  apiKey: string;
  model: string;
}

const DEFAULT_BASE = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};

function safeParse(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>;
  if (typeof raw !== "string" || !raw.trim()) return {};
  try { return JSON.parse(raw); } catch { /* 下面再试 */ }
  const start = raw.indexOf("{"), end = raw.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try { return JSON.parse(raw.slice(start, end + 1)); } catch { return {}; }
  }
  return {};
}

export async function callBrowserLLM(
  config: BrowserLLMConfig,
  system: string,
  messages: BrowserWire[],
  tools: BrowserTool[],
  options: { temperature?: number; maxTokens?: number } = {},
): Promise<BrowserResult> {
  const base = (config.baseUrl || DEFAULT_BASE[config.provider]).replace(/\/$/, "");

  if (config.provider === "anthropic") {
    const wire = messages.map((message) => {
      if (message.role === "tool") {
        return { role: "user", content: [{ type: "tool_result", tool_use_id: message.toolCallId, content: message.content }] };
      }
      if (message.role === "assistant") {
        const blocks: unknown[] = [];
        if (message.content) blocks.push({ type: "text", text: message.content });
        for (const call of message.toolCalls ?? []) {
          blocks.push({ type: "tool_use", id: call.id, name: call.name, input: call.arguments });
        }
        return { role: "assistant", content: blocks.length ? blocks : [{ type: "text", text: "" }] };
      }
      return { role: "user", content: message.content };
    });
    const response = await fetch(`${base}/v1/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": config.apiKey,
        "anthropic-version": "2023-06-01",
        // 浏览器直连 Anthropic 必须显式声明
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model: config.model,
        system,
        messages: wire,
        max_tokens: options.maxTokens ?? 4096,
        temperature: options.temperature,
        ...(tools.length ? { tools } : {}),
      }),
    });
    if (!response.ok) throw new Error(`Anthropic ${response.status}: ${(await response.text()).slice(0, 300)}`);
    const data = await response.json();
    const text = (data.content ?? []).filter((b: any) => b.type === "text").map((b: any) => b.text).join("");
    const toolCalls = (data.content ?? [])
      .filter((b: any) => b.type === "tool_use")
      .map((b: any) => ({ id: b.id, name: b.name, arguments: b.input ?? {} }));
    return { text, toolCalls, usage: { input: data.usage?.input_tokens ?? 0, output: data.usage?.output_tokens ?? 0 } };
  }

  const wire: unknown[] = [{ role: "system", content: system }];
  for (const message of messages) {
    if (message.role === "tool") {
      wire.push({ role: "tool", tool_call_id: message.toolCallId, content: message.content });
    } else if (message.role === "assistant" && message.toolCalls?.length) {
      wire.push({
        role: "assistant",
        content: message.content || null,
        tool_calls: message.toolCalls.map((call) => ({
          id: call.id, type: "function",
          function: { name: call.name, arguments: JSON.stringify(call.arguments) },
        })),
      });
    } else {
      wire.push({ role: message.role, content: message.content });
    }
  }
  const response = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${config.apiKey}` },
    body: JSON.stringify({
      model: config.model,
      messages: wire,
      temperature: options.temperature,
      max_tokens: options.maxTokens,
      ...(tools.length
        ? { tools: tools.map((tool) => ({ type: "function", function: { name: tool.name, description: tool.description, parameters: tool.input_schema } })) }
        : {}),
    }),
  });
  if (!response.ok) throw new Error(`OpenAI ${response.status}: ${(await response.text()).slice(0, 300)}`);
  const data = await response.json();
  const choice = data.choices?.[0]?.message ?? {};
  const toolCalls = (choice.tool_calls ?? []).map((call: any, index: number) => ({
    id: call.id ?? `call_${index}`,
    name: call.function?.name ?? "",
    arguments: safeParse(call.function?.arguments),
  }));
  return {
    text: typeof choice.content === "string" ? choice.content : "",
    toolCalls,
    usage: { input: data.usage?.prompt_tokens ?? 0, output: data.usage?.completion_tokens ?? 0 },
  };
}
