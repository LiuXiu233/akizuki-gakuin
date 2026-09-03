"use client";

import { useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { apiConfig, imageCredentials, useGame, useSettings } from "@/lib/store";

import { Spinner } from "./ui";

/** 最常待的地方。先把这些画出来，剩下的边玩边补。 */
const COMMON = [
  "loc_school_gate", "loc_shoe_lockers", "loc_class_2a", "loc_corridor",
  "loc_rooftop", "loc_courtyard", "loc_library", "loc_cafeteria",
  "loc_vending_area", "loc_club_building", "loc_convenience_store", "loc_riverbank",
];

/** 实测一张约 57 秒，用它估个总时长 */
const SECONDS_PER_IMAGE = 57;

export function SceneGenerator({ worldId }: { worldId: string }) {
  const settings = useSettings();
  const meta = useGame((state) => state.meta);
  const images = useGame((state) => state.images);
  const setImage = useGame((state) => state.setImage);

  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [current, setCurrent] = useState("");
  const [failed, setFailed] = useState<string[]>([]);
  const cancelRef = useRef(false);

  const locations = meta?.locations ?? [];
  const missing = useMemo(
    () => ({
      common: COMMON.filter((id) => !images[`scene:${id}`] && locations.some((l) => l.id === id)),
      all: locations.filter((l) => !images[`scene:${l.id}`]).map((l) => l.id),
    }),
    [locations, images],
  );

  async function generate(ids: string[]) {
    if (!ids.length || running) return;
    cancelRef.current = false;
    setRunning(true);
    setTotal(ids.length);
    setDone(0);
    setFailed([]);
    const cfg = apiConfig(settings);

    for (const [index, id] of ids.entries()) {
      if (cancelRef.current) break;
      setCurrent(locations.find((l) => l.id === id)?.name ?? id);
      try {
        const result = await api.generateImage(cfg, worldId, {
          kind: "scene", subject_id: id, credentials: imageCredentials(settings),
        });
        if (result.ok && result.image) setImage(`scene:${result.image.subject_id}`, result.image.url);
        else setFailed((f) => [...f, locations.find((l) => l.id === id)?.name ?? id]);
      } catch {
        setFailed((f) => [...f, locations.find((l) => l.id === id)?.name ?? id]);
      }
      setDone(index + 1);
    }
    setRunning(false);
    setCurrent("");
  }

  if (!settings.image.enabled) {
    return <p className="text-[11px] leading-relaxed text-ink-mute">先在上面开启立绘功能，才能生成场景图。</p>;
  }

  const haveCount = locations.filter((l) => images[`scene:${l.id}`]).length;

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] leading-relaxed text-ink-mute">
        场景图是沉浸模式的背景。已有 <span className="text-ink">{haveCount}</span> / {locations.length} 个地点。
        一张约 {SECONDS_PER_IMAGE} 秒，生成后永久缓存，不会重复计费。
      </p>

      {running ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <Spinner label={`正在画：${current}`} />
            <span className="tabular-nums text-ink-mute">{done}/{total}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-black/10">
            <div className="h-full rounded-full bg-dusk transition-all duration-500"
                 style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
          </div>
          <button className="btn-ghost btn-sm w-full" onClick={() => { cancelRef.current = true; }}>
            停止（已生成的会保留）
          </button>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <button className="btn-primary" disabled={!missing.common.length}
                  onClick={() => generate(missing.common)}>
            {missing.common.length
              ? `常用地点（${missing.common.length} 张，约 ${Math.ceil(missing.common.length * SECONDS_PER_IMAGE / 60)} 分钟）`
              : "常用地点已齐"}
          </button>
          <button className="btn-ghost" disabled={!missing.all.length}
                  onClick={() => generate(missing.all)}>
            {missing.all.length
              ? `全部地点（${missing.all.length} 张，约 ${Math.ceil(missing.all.length * SECONDS_PER_IMAGE / 60)} 分钟）`
              : "全部地点已齐"}
          </button>
        </div>
      )}

      {failed.length ? (
        <p className="rounded-xl bg-sakura-pale p-2 text-[11px] text-sakura-deep">
          有 {failed.length} 个没画成：{failed.slice(0, 5).join("、")}
          {failed.length > 5 ? " 等" : ""}。再点一次会只补没画成的。
        </p>
      ) : null}
    </div>
  );
}
