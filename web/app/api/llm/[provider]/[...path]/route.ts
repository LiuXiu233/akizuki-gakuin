/**
 * Vercel 边缘 LLM 代理。
 *
 * 用途：把 key 放在 Vercel 环境变量里，你自己的服务器上不存在任何 key。
 * 后端把这里当成一个普通的 OpenAI / Anthropic 兼容端点即可。
 *
 * 请求 /api/llm/openai/chat/completions  →  ${LLM_BASE_URL}/chat/completions
 * 请求 /api/llm/anthropic/v1/messages    →  https://api.anthropic.com/v1/messages
 */

import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DEFAULT_BASE: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ provider: string; path: string[] }> },
) {
  const { provider, path } = await context.params;
  if (!(provider in DEFAULT_BASE)) {
    return Response.json({ error: { message: `不支持的 provider: ${provider}` } }, { status: 400 });
  }
  const apiKey = process.env.LLM_API_KEY;
  if (!apiKey) {
    return Response.json(
      { error: { message: "Vercel 端没有配置 LLM_API_KEY。请在项目环境变量里设置，或改用其它调用位置。" } },
      { status: 500 },
    );
  }
  const base = (process.env.LLM_BASE_URL || DEFAULT_BASE[provider]).replace(/\/$/, "");
  const destination = `${base}/${path.join("/")}`;

  const headers = new Headers({ "content-type": "application/json" });
  if (provider === "anthropic") {
    headers.set("x-api-key", apiKey);
    headers.set("anthropic-version", request.headers.get("anthropic-version") || "2023-06-01");
  } else {
    headers.set("authorization", `Bearer ${apiKey}`);
  }

  const body = await request.text();
  let upstream: Response;
  try {
    upstream = await fetch(destination, {
      method: "POST",
      headers,
      body,
      signal: AbortSignal.timeout(300_000),
    });
  } catch (error) {
    return Response.json(
      { error: { message: `上游请求失败：${error instanceof Error ? error.message : String(error)}` } },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);
  responseHeaders.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}
