"use client";

import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import { apiConfig, useSettings } from "@/lib/store";
import type { MetaBundle } from "@/lib/types";

import { Field, Spinner } from "./ui";

const ATTRIBUTE_ORDER = ["physique", "agility", "intellect", "perception", "charm", "willpower", "luck"];
const ATTRIBUTE_ZH: Record<string, string> = {
  physique: "体魄", agility: "灵巧", intellect: "智力",
  perception: "感知", charm: "魅力", willpower: "意志", luck: "幸运",
};
const ATTRIBUTE_HINT: Record<string, string> = {
  physique: "力量、耐力、体育", agility: "反应、协调、精细动作",
  intellect: "学习、推理、理解", perception: "观察、察觉、读气氛",
  charm: "表达、演讲、舞台", willpower: "专注、自控、抗压", luck: "偶发、抽签、碰巧",
};

interface Props {
  meta: MetaBundle;
  worldId: string;
  onDone: () => void;
  onCancel?: () => void;
}

export function CharacterCreator({ meta, worldId, onDone, onCancel }: Props) {
  const settings = useSettings();
  const rules = meta.creation_rules ?? {};
  const base = Number(rules.attribute_base ?? 4);
  const budget = Number(rules.attribute_points ?? 12);
  const min = Number(rules.attribute_min ?? 3);
  const max = Number(rules.attribute_max ?? 8);
  const skillSlots = Number(rules.skill_choices ?? 3);
  const knowledgeMin = Number(rules.knowledge_choices_min ?? 3);
  const knowledgeMax = Number(rules.knowledge_choices_max ?? 5);
  const total = base * ATTRIBUTE_ORDER.length + budget;

  const [name, setName] = useState("");
  const [age, setAge] = useState(19);
  const [gender, setGender] = useState("");
  const [appearance, setAppearance] = useState("");
  const [interests, setInterests] = useState("");
  const [tendency, setTendency] = useState("");
  const [attributes, setAttributes] = useState<Record<string, number>>(
    Object.fromEntries(ATTRIBUTE_ORDER.map((key) => [key, base])),
  );
  const [skills, setSkills] = useState<string[]>([]);
  const [knowledge, setKnowledge] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const spent = useMemo(() => Object.values(attributes).reduce((sum, value) => sum + value, 0), [attributes]);
  const remaining = total - spent;

  function applyPreset(presetId: string) {
    const preset = meta.player_presets.find((item) => item.id === presetId);
    if (!preset) return;
    setAttributes({ ...preset.attributes });
    setSkills([...preset.skills]);
    setKnowledge([...preset.knowledge]);
  }

  function adjust(key: string, delta: number) {
    setAttributes((current) => {
      const next = Math.max(min, Math.min(max, (current[key] ?? base) + delta));
      const nextTotal = spent - (current[key] ?? base) + next;
      if (nextTotal > total) return current;
      return { ...current, [key]: next };
    });
  }

  function toggle(list: string[], setList: (value: string[]) => void, id: string, limit: number) {
    if (list.includes(id)) setList(list.filter((item) => item !== id));
    else if (list.length < limit) setList([...list, id]);
  }

  const valid =
    name.trim().length > 0 &&
    age >= 18 &&
    remaining === 0 &&
    skills.length === skillSlots &&
    knowledge.length >= knowledgeMin &&
    knowledge.length <= knowledgeMax;

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const result = await api.tool(apiConfig(settings), worldId, "create_player", {
        name: name.trim(),
        age,
        gender: gender.trim() || "unspecified",
        attributes,
        skills,
        knowledge,
        appearance: appearance.trim(),
        interests: interests.split(/[、,，\s]+/).filter(Boolean),
        personality_tendency: tendency.split(/[、,，\s]+/).filter(Boolean),
      });
      if (!result.ok) throw new Error(result.error ?? "创建失败");
      onDone();
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <div className="label">快速预设</div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {meta.player_presets.map((preset) => (
            <button key={preset.id} onClick={() => applyPreset(preset.id)}
                    className="rounded-xl border border-paper-edge bg-white/60 p-2.5 text-left transition hover:bg-white">
              <div className="text-sm">{preset.name}</div>
              <div className="mt-0.5 text-[11px] leading-relaxed text-ink-mute">{preset.description}</div>
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="姓名">
          <input className="field" value={name} onChange={(event) => setName(event.target.value)} placeholder="佐藤悠" />
        </Field>
        <Field label="年龄" hint="这个世界里所有人都是成年人，最小 18 岁">
          <input className="field" type="number" min={18} max={30} value={age}
                 onChange={(event) => setAge(Number(event.target.value))} />
        </Field>
        <Field label="性别 / 称呼" hint="自由填写">
          <input className="field" value={gender} onChange={(event) => setGender(event.target.value)} placeholder="男 / 女 / 不指定 / …" />
        </Field>
        <Field label="性格倾向" hint="用顿号分隔，2~3 个词">
          <input className="field" value={tendency} onChange={(event) => setTendency(event.target.value)} placeholder="观察型、不太会拒绝别人" />
        </Field>
      </div>

      <Field label="外貌">
        <textarea className="field h-16" value={appearance} onChange={(event) => setAppearance(event.target.value)}
                  placeholder="中等身材，总背着一个旧相机包" />
      </Field>
      <Field label="兴趣" hint="用顿号分隔">
        <input className="field" value={interests} onChange={(event) => setInterests(event.target.value)} placeholder="摄影、深夜广播" />
      </Field>

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="label mb-0">属性</span>
          <span className={`text-xs ${remaining === 0 ? "text-moss" : "text-sakura-deep"}`}>
            剩余 {remaining} 点（总和须为 {total}）
          </span>
        </div>
        <div className="space-y-1.5">
          {ATTRIBUTE_ORDER.map((key) => (
            <div key={key} className="flex items-center gap-3 rounded-xl bg-white/60 px-3 py-2">
              <div className="w-24 shrink-0">
                <div className="text-sm">{ATTRIBUTE_ZH[key]}</div>
                <div className="text-[10px] text-ink-mute">{ATTRIBUTE_HINT[key]}</div>
              </div>
              <div className="flex flex-1 items-center gap-1">
                {Array.from({ length: max }, (_, index) => index + 1).map((n) => (
                  <span key={n} className={`h-2 flex-1 rounded-full ${
                    n <= attributes[key] ? (n > 5 ? "bg-sakura-deep" : "bg-dusk") : "bg-black/10"}`} />
                ))}
              </div>
              <div className="flex items-center gap-1">
                <button className="btn-ghost btn-sm" onClick={() => adjust(key, -1)} disabled={attributes[key] <= min}>−</button>
                <span className="w-6 text-center tabular-nums text-sm">{attributes[key]}</span>
                <button className="btn-ghost btn-sm" onClick={() => adjust(key, 1)}
                        disabled={attributes[key] >= max || remaining <= 0}>+</button>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-ink-mute">
          魅力高不等于任何人必须喜欢你——它只影响你表达得好不好。
        </p>
      </div>

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="label mb-0">初始擅长的技能</span>
          <span className={`text-xs ${skills.length === skillSlots ? "text-moss" : "text-ink-mute"}`}>
            {skills.length}/{skillSlots}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {meta.skills.map((skill) => (
            <button key={skill.id} onClick={() => toggle(skills, setSkills, skill.id, skillSlots)}
                    className={skills.includes(skill.id) ? "chip chip-on" : "chip hover:bg-white"}
                    title={skill.description}>
              {skill.name}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="label mb-0">感兴趣的知识</span>
          <span className={`text-xs ${knowledge.length >= knowledgeMin ? "text-moss" : "text-ink-mute"}`}>
            {knowledge.length}/{knowledgeMin}~{knowledgeMax}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {meta.knowledge.map((item) => (
            <button key={item.id} onClick={() => toggle(knowledge, setKnowledge, item.id, knowledgeMax)}
                    className={knowledge.includes(item.id) ? "chip chip-on" : "chip hover:bg-white"}
                    title={item.description}>
              {item.name}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-ink-mute">
          技能是「会做」，知识是「知道」。两者完全分开——知道菜谱不等于做得好。
        </p>
      </div>

      {error ? <p className="rounded-xl bg-sakura-pale p-2.5 text-xs text-sakura-deep">{error}</p> : null}

      <div className="flex gap-2">
        {onCancel ? <button className="btn-ghost" onClick={onCancel}>返回</button> : null}
        <button className="btn-primary flex-1" onClick={submit} disabled={!valid || busy}>
          {busy ? <Spinner label="创建中…" /> : "开始在秋月的生活"}
        </button>
      </div>
    </div>
  );
}
