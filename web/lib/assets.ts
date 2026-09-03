"use client";

/**
 * 图片加载。
 *
 * 后端的图片接口需要鉴权头（访问口令 + 用户令牌），而 `<img src>` 发不了自定义头，
 * 所以统一用 fetch 拉成 blob 再显示。同一路径只会真正请求一次。
 */

import { useEffect, useState } from "react";

import { fetchAsset, type ApiConfig } from "./api";
import { apiConfig, useSettings } from "./store";

const cache = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

export async function loadAsset(cfg: ApiConfig, path: string): Promise<string> {
  const cached = cache.get(path);
  if (cached) return cached;
  const pending = inflight.get(path);
  if (pending) return pending;

  const task = (async () => {
    const blob = await fetchAsset(cfg, path);
    const objectUrl = URL.createObjectURL(blob);
    cache.set(path, objectUrl);
    return objectUrl;
  })();
  inflight.set(path, task);
  try {
    return await task;
  } finally {
    inflight.delete(path);
  }
}

/** 把后端返回的相对路径变成可以直接放进 `<img src>` 的地址。 */
export function useAsset(path?: string | null): string | null {
  const settings = useSettings();
  const [url, setUrl] = useState<string | null>(path ? cache.get(path) ?? null : null);

  useEffect(() => {
    if (!path) { setUrl(null); return; }
    if (path.startsWith("blob:") || path.startsWith("http")) { setUrl(path); return; }
    const cached = cache.get(path);
    if (cached) { setUrl(cached); return; }
    let cancelled = false;
    loadAsset(apiConfig(settings), path)
      .then((value) => { if (!cancelled) setUrl(value); })
      .catch(() => { if (!cancelled) setUrl(null); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, settings.backendUrl, settings.transport]);

  return url;
}

export function forgetAsset(path: string): void {
  const existing = cache.get(path);
  if (existing) URL.revokeObjectURL(existing);
  cache.delete(path);
}
