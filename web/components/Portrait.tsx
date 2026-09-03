"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { useAsset } from "@/lib/assets";
import { apiConfig, imageCredentials, useGame, useSettings } from "@/lib/store";

/** 从名字取一个稳定的柔和配色，用作没有立绘时的占位。 */
function tint(seed: string): { from: string; to: string } {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  return { from: `hsl(${hash} 42% 82%)`, to: `hsl(${(hash + 40) % 360} 38% 66%)` };
}

interface Props {
  kind: "avatar" | "portrait" | "scene" | "cg";
  subjectId: string;
  name: string;
  worldId: string;
  className?: string;
  rounded?: string;
  showGenerate?: boolean;
}

export function Portrait({
  kind, subjectId, name, worldId, className = "", rounded = "rounded-2xl", showGenerate = true,
}: Props) {
  const settings = useSettings();
  const images = useGame((state) => state.images);
  const setImage = useGame((state) => state.setImage);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const key = `${kind}:${subjectId}`;
  const url = useAsset(images[key]);
  const colors = tint(subjectId || name);
  const initial = (name || "?").trim().slice(0, 1);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const cfg = apiConfig(settings);
      const result = await api.generateImage(cfg, worldId, {
        kind, subject_id: subjectId, credentials: imageCredentials(settings),
      });
      if (result.ok && result.image) setImage(key, result.image.url);
      else setError(result.error ?? result.skipped ?? "生成失败");
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception));
    } finally {
      setBusy(false);
    }
  }

  if (url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={url} alt={name} className={`${rounded} object-cover ${className}`} />;
  }

  return (
    <div
      className={`${rounded} relative flex items-center justify-center overflow-hidden ${className}`}
      style={{ background: `linear-gradient(150deg, ${colors.from}, ${colors.to})` }}
      title={settings.image.enabled ? name : `${name}（立绘功能未开启）`}
    >
      <span className="select-none font-serif text-[min(42%,2.5rem)] text-white/85 drop-shadow">{initial}</span>
      {settings.image.enabled && showGenerate ? (
        <button
          onClick={(event) => { event.stopPropagation(); void generate(); }}
          disabled={busy}
          className="absolute bottom-1 right-1 rounded-lg bg-black/45 px-1.5 py-0.5 text-[10px] text-white/90 backdrop-blur transition hover:bg-black/65 disabled:opacity-50"
        >
          {busy ? "生成中…" : "生成"}
        </button>
      ) : null}
      {error ? (
        <span className="absolute inset-x-1 bottom-1 truncate rounded bg-black/60 px-1 text-[9px] text-white/90" title={error}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
