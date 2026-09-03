/**
 * 后端转发。
 *
 * 存在的唯一理由：Vercel 上的页面是 https，而自建后端很可能是 http，
 * 浏览器会因为「混合内容」直接拦截直连请求。服务端转发不受这个限制，
 * 顺便也绕开了 CORS。
 *
 * 目标后端来自请求头 X-Backend-Url，或环境变量 BACKEND_URL / NEXT_PUBLIC_BACKEND_URL。
 */

import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const FORWARD_HEADERS = ["content-type", "x-access-password", "x-user-token", "accept"];

type RouteContext = { params: Promise<{ path: string[] }> };

function target(request: NextRequest, path: string[]): string | null {
  const base =
    request.headers.get("x-backend-url") ||
    request.nextUrl.searchParams.get("backend") ||
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "";
  if (!base) return null;
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(url.protocol)) return null;
  const suffix = path.join("/");
  const query = new URLSearchParams(request.nextUrl.searchParams);
  query.delete("backend");
  const search = query.toString();
  return `${url.origin}${url.pathname.replace(/\/$/, "")}/${suffix}${search ? `?${search}` : ""}`;
}

async function forward(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const destination = target(request, path ?? []);
  if (!destination) {
    return Response.json(
      { ok: false, detail: "没有配置后端地址（X-Backend-Url 或 BACKEND_URL）" },
      { status: 400 },
    );
  }

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let body: BodyInit | undefined;
  if (!["GET", "HEAD"].includes(request.method)) body = await request.text();

  let upstream: Response;
  try {
    upstream = await fetch(destination, {
      method: request.method,
      headers,
      body,
      // 回合可能跑几十秒
      signal: AbortSignal.timeout(300_000),
    });
  } catch (error) {
    return Response.json(
      { ok: false, detail: `转发失败：${error instanceof Error ? error.message : String(error)}` },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);
  responseHeaders.set("cache-control", "no-store");
  // SSE 必须原样透传流
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
