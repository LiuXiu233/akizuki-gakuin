"use client";

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { apiConfig, useGame, useSettings } from "@/lib/store";
import type { CharacterState, MetaBundle } from "@/lib/types";

import { Portrait } from "./Portrait";
import { Card, Empty, Meter, Modal, Section, Spinner } from "./ui";

const ATTRIBUTE_ORDER = ["physique", "agility", "intellect", "perception", "charm", "willpower", "luck"];
const ATTRIBUTE_ZH: Record<string, string> = {
  physique: "体魄", agility: "灵巧", intellect: "智力",
  perception: "感知", charm: "魅力", willpower: "意志", luck: "幸运",
};
const MOOD_ZH: Record<string, string> = {
  normal: "平静", sleepy: "困倦", tired: "疲惫", energetic: "精神很好", inspired: "有灵感",
  nervous: "紧张", embarrassed: "尴尬", confident: "自信", stressed: "焦躁", hungry: "饿",
  focused: "专注", sick: "不舒服", excited: "兴奋", relaxed: "放松",
};
const CONDITION_ZH: Record<string, string> = {
  tired: "有点累", exhausted: "精疲力竭", stressed: "压力有点大", overloaded: "压力过载",
  hungry: "稍微有些饿", sleepy: "困", focused: "注意力集中", inspired: "有灵感",
};

function label(meta: MetaBundle | null, kind: "skills" | "knowledge", id: string): string {
  const list = meta?.[kind] as Array<{ id: string; name: string }> | undefined;
  return list?.find((item) => item.id === id)?.name ?? id;
}

/* ------------------------------------------------------------------ */

export function StatusPanel() {
  const { player, world, meta } = useGame();
  if (!player || !world) return <Empty>还没有载入世界</Empty>;
  const status = player.status;

  return (
    <div className="space-y-4">
      <Section title="状态">
        <div className="space-y-2.5">
          <Meter label="健康" value={status.health} tone="moss" />
          <Meter label="精力" value={status.energy} tone={status.energy < 25 ? "sakura" : "dusk"} />
          <Meter label="压力" value={status.stress} tone={status.stress > 60 ? "sakura" : "amber"} />
          <div className="flex items-center justify-between pt-1 text-xs">
            <span className="text-ink-mute">心情</span>
            <span>{MOOD_ZH[status.mood] ?? status.mood}</span>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-ink-mute">金钱</span>
            <span className="tabular-nums">¥{status.money.toLocaleString()}</span>
          </div>
        </div>
        {player.conditions.length ? (
          <div className="mt-3 flex flex-wrap gap-1">
            {player.conditions.map((condition) => (
              <span key={condition} className="chip">{CONDITION_ZH[condition] ?? condition}</span>
            ))}
          </div>
        ) : null}
      </Section>

      <Section title="属性">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          {ATTRIBUTE_ORDER.map((key) => {
            const value = player.attributes[key] ?? 4;
            return (
              <div key={key} className="flex items-center justify-between text-xs">
                <span className="text-ink-mute">{ATTRIBUTE_ZH[key]}</span>
                <span className="tabular-nums">
                  {value}
                  <span className="ml-1 text-[10px] text-ink-mute">{value - 5 >= 0 ? `+${value - 5}` : value - 5}</span>
                </span>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="技能">
        {Object.keys(player.skills).length ? (
          <div className="space-y-1">
            {Object.entries(player.skills)
              .sort((a, b) => b[1] - a[1])
              .map(([id, level]) => (
                <div key={id} className="flex items-center justify-between text-xs">
                  <span>{label(meta, "skills", id)}</span>
                  <span className="flex gap-0.5">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <span key={n} className={`h-1.5 w-1.5 rounded-full ${n <= level ? "bg-dusk" : "bg-black/12"}`} />
                    ))}
                  </span>
                </div>
              ))}
          </div>
        ) : <Empty>还没有掌握的技能</Empty>}
      </Section>

      <Section title="知识">
        {Object.keys(player.knowledge).length ? (
          <div className="space-y-1">
            {Object.entries(player.knowledge)
              .sort((a, b) => b[1] - a[1])
              .map(([id, level]) => (
                <div key={id} className="flex items-center justify-between text-xs">
                  <span>{label(meta, "knowledge", id)}</span>
                  <span className="flex gap-0.5">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <span key={n} className={`h-1.5 w-1.5 rounded-full ${n <= level ? "bg-moss" : "bg-black/12"}`} />
                    ))}
                  </span>
                </div>
              ))}
          </div>
        ) : <Empty>还没有积累的知识</Empty>}
      </Section>

      {player.clubs.length ? (
        <Section title="社团">
          <div className="flex flex-wrap gap-1">
            {player.clubs.map((club) => (
              <span key={club} className="chip chip-on">
                {meta?.clubs.find((item) => item.id === club)?.name ?? club}
              </span>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function NearbyPanel({ worldId }: { worldId: string }) {
  const context = useGame((state) => state.context);
  const [selected, setSelected] = useState<string | null>(null);
  const people = context?.nearby_characters ?? [];

  return (
    <>
      <Section title={`在场 · ${people.length} 人`}>
        {people.length ? (
          <div className="space-y-2">
            {people.map((person) => (
              <div key={person.id} role="button" tabIndex={0}
                   onClick={() => setSelected(person.id)}
                   onKeyDown={(event) => { if (event.key === "Enter") setSelected(person.id); }}
                   className="flex w-full cursor-pointer items-center gap-2.5 rounded-xl border border-transparent p-1.5 text-left transition hover:border-paper-edge hover:bg-white/70">
                <Portrait kind="avatar" subjectId={person.id} name={person.name} worldId={worldId}
                          className="h-9 w-9 shrink-0" rounded="rounded-lg" showGenerate={false} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{person.name}</span>
                  <span className="block truncate text-[11px] text-ink-mute">
                    {person.relationship} · {person.activity ?? "在这里"}
                  </span>
                </span>
              </div>
            ))}
          </div>
        ) : <Empty>这里现在没有别人</Empty>}
      </Section>
      <CharacterModal worldId={worldId} characterId={selected} onClose={() => setSelected(null)} />
    </>
  );
}

/* ------------------------------------------------------------------ */

export function CharacterModal({
  worldId, characterId, onClose,
}: { worldId: string; characterId: string | null; onClose: () => void }) {
  const settings = useSettings();
  const [data, setData] = useState<CharacterState | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!characterId) { setData(null); return; }
    let cancelled = false;
    setLoading(true);
    api.character(apiConfig(settings), worldId, characterId)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [characterId, worldId]);

  return (
    <Modal open={!!characterId} onClose={onClose} title={data?.name ?? "角色"} wide>
      {loading ? <Spinner label="读取中…" /> : null}
      {data ? (
        <div className="grid gap-5 sm:grid-cols-[200px_1fr]">
          <div className="space-y-3">
            <Portrait kind="portrait" subjectId={data.id} name={data.name} worldId={worldId}
                      className="aspect-[3/4] w-full" />
            <div className="text-xs text-ink-mute">
              {data.age} 岁 · {data.class ?? data.role} · {data.tier}
            </div>
            <div className="chip chip-on">{data.relationship_with_player?.label}</div>
            {data.relationship_with_player?.hints?.length ? (
              <ul className="space-y-1 text-[11px] leading-relaxed text-ink-mute">
                {data.relationship_with_player.hints.map((hint) => <li key={hint}>· {hint}</li>)}
              </ul>
            ) : null}
          </div>

          <div className="space-y-3 text-sm">
            <Field label="外貌">{data.appearance}</Field>
            <Field label="性格">{data.personality}</Field>
            <Field label="说话方式">{data.speech_style}</Field>
            {data.interests?.length ? (
              <Field label="兴趣">{data.interests.join("、")}</Field>
            ) : null}
            <Field label="现在">
              {data.current_activity ?? data.schedule_now?.activity ?? "—"}
            </Field>
            {Object.keys(data.skills ?? {}).length ? (
              <Field label="擅长">
                {Object.entries(data.skills).sort((a, b) => b[1] - a[1]).slice(0, 6)
                  .map(([id, level]) => `${id} Lv.${level}`).join("　")}
              </Field>
            ) : null}
            <p className="rounded-xl bg-black/[0.03] p-2.5 text-[11px] leading-relaxed text-ink-mute">
              这个人的想法、秘密和对你的真实感受不会显示在这里。
              只能从他/她的行为、语气和距离里去判断。
            </p>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

function Field({ label: title, children }: { label: string; children: React.ReactNode }) {
  if (!children) return null;
  return (
    <div>
      <div className="label">{title}</div>
      <div className="leading-relaxed">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function GalleryPanel({ worldId }: { worldId: string }) {
  const settings = useSettings();
  const context = useGame((state) => state.context);
  const [people, setPeople] = useState<Array<{ id: string; name: string; label: string; hints: string[] }>>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.tool(apiConfig(settings), worldId, "get_player_sheet")
      .then((sheet) => { if (!cancelled) setPeople(sheet.relationships ?? []); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worldId, context?.current_time?.time]);

  if (loading) return <Spinner label="读取名册…" />;

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {people.map((person) => (
          <div key={person.id} role="button" tabIndex={0}
               onClick={() => setSelected(person.id)}
               onKeyDown={(event) => { if (event.key === "Enter") setSelected(person.id); }}
               className="card cursor-pointer overflow-hidden text-left transition hover:-translate-y-0.5 hover:shadow-lg">
            <Portrait kind="avatar" subjectId={person.id} name={person.name} worldId={worldId}
                      className="aspect-square w-full" rounded="rounded-none" />
            <div className="p-2.5">
              <div className="truncate text-sm">{person.name}</div>
              <div className="truncate text-[11px] text-ink-mute">{person.label}</div>
            </div>
          </div>
        ))}
      </div>
      {!people.length ? <Empty>还没有认识的人。去和人说说话吧。</Empty> : null}
      <CharacterModal worldId={worldId} characterId={selected} onClose={() => setSelected(null)} />
    </>
  );
}

/* ------------------------------------------------------------------ */

export function MapPanel({ worldId, onMove }: { worldId: string; onMove: (id: string, name: string) => void }) {
  const { meta, world, context } = useGame();
  const [area, setArea] = useState<"school" | "town">("school");
  const locations = useMemo(
    () => (meta?.locations ?? []).filter((location) => location.area === area),
    [meta, area],
  );
  const available = new Map((context?.available_locations ?? []).map((item) => [item.id, item.minutes]));

  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {(["school", "town"] as const).map((value) => (
          <button key={value} onClick={() => setArea(value)}
                  className={`rounded-lg px-3 py-1 text-xs transition ${area === value ? "bg-dusk text-paper" : "text-ink-mute hover:bg-black/5"}`}>
            {value === "school" ? "校内" : "校外"}
          </button>
        ))}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {locations.map((location) => {
          const here = world?.location.id === location.id;
          const minutes = available.get(location.id);
          return (
            <button key={location.id} disabled={here || !location.open}
                    onClick={() => onMove(location.id, location.name)}
                    className={`rounded-xl border p-2.5 text-left text-xs transition ${
                      here ? "border-dusk bg-dusk/10"
                        : location.open ? "border-paper-edge bg-white/60 hover:bg-white"
                          : "border-paper-edge/50 bg-black/[0.02] opacity-50"}`}>
              <div className="flex items-center justify-between">
                <span className="text-sm">{location.name}</span>
                {here ? <span className="chip chip-on">在这里</span>
                  : !location.open ? <span className="chip">未开放</span>
                    : minutes !== undefined ? <span className="text-[11px] text-ink-mute">{minutes} 分钟</span> : null}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {location.tags.slice(0, 3).map((tag) => <span key={tag} className="chip">{tag}</span>)}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function SchedulePanel() {
  const world = useGame((state) => state.world);
  const context = useGame((state) => state.context);
  if (!world) return <Empty>—</Empty>;
  return (
    <div className="space-y-4">
      <Section title="今天">
        <div className="space-y-1.5 text-xs">
          <Row label="日期">{world.date} · {world.weekday_zh}</Row>
          <Row label="时刻">{world.time}（{world.block ?? "—"}）</Row>
          <Row label="天气">{world.weather_zh}</Row>
          <Row label="学期">{world.term?.name ?? "—"}</Row>
          <Row label="上课时间">{world.is_class_time ? "是" : "否"}</Row>
          <Row label="社团时间">{world.is_club_time ? "是" : "否"}</Row>
        </div>
      </Section>

      {world.class_subjects_today?.length ? (
        <Section title="今日课表">
          <div className="flex flex-wrap gap-1">
            {world.class_subjects_today.map((subject, index) => (
              <span key={`${subject}-${index}`} className="chip">{index + 1}. {subject}</span>
            ))}
          </div>
        </Section>
      ) : null}

      {world.calendar_events?.length ? (
        <Section title="正在进行的活动">
          <div className="space-y-1">
            {world.calendar_events.map((event) => (
              <div key={event.id} className="rounded-lg bg-sakura-pale/60 px-2.5 py-1.5 text-xs">
                {event.name}
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {context?.romance_opportunity === false ? (
        <p className="rounded-xl bg-black/[0.03] p-2.5 text-[11px] leading-relaxed text-ink-mute">
          引擎判断现在不是发展感情的时机（没有合适的人在场、正在考试、太累，或正处于重要活动中）。
        </p>
      ) : null}
    </div>
  );
}

function Row({ label: title, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-ink-mute">{title}</span>
      <span>{children}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function LogPanel() {
  const log = useGame((state) => state.log);
  if (!log.length) return <Empty>还没有记录</Empty>;
  return (
    <div className="space-y-4">
      {[...log].reverse().map((entry) => (
        <Card key={entry.id} className="p-3.5">
          <div className="mb-2 flex items-center gap-2 text-[11px] text-ink-mute">
            <span className="chip">第 {entry.turn} 回合</span>
            <span>{entry.time}</span>
            {entry.randomEvent ? <span className="chip chip-on">{entry.randomEvent.name}</span> : null}
            {entry.usage ? <span className="ml-auto tabular-nums">{entry.usage.total_tokens} tokens</span> : null}
          </div>
          {entry.playerInput ? (
            <p className="mb-2 border-l-2 border-dusk/30 pl-2.5 text-xs text-ink-mute">「{entry.playerInput}」</p>
          ) : null}
          <div className="prose-narrative whitespace-pre-wrap text-[13px]">{entry.narration}</div>
          {entry.dialogue.map((line, index) => (
            <p key={index} className="mt-2 text-[13px]">
              <span className="text-sakura-deep">{line.name}</span>　{line.text}
            </p>
          ))}
          {entry.checkText ? (
            <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-black/[0.04] p-2 font-sans text-[11px] leading-relaxed">{entry.checkText}</pre>
          ) : null}
          {entry.growthText ? (
            <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-moss/10 p-2 font-sans text-[11px] leading-relaxed">{entry.growthText}</pre>
          ) : null}
          {entry.errors.length ? (
            <div className="mt-2 rounded-lg bg-sakura-pale p-2 text-[11px] text-sakura-deep">
              {entry.errors.map((error, index) => <div key={index}>{error}</div>)}
            </div>
          ) : null}
        </Card>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function DebugPanel() {
  const { log, toolFeed, stageProgress } = useGame();
  const settings = useSettings();
  const last = log[log.length - 1];

  return (
    <div className="space-y-4 text-xs">
      <Section title="本次运行">
        <div className="space-y-1">
          <Row label="流水线">{settings.pipeline}</Row>
          <Row label="LLM 位置">{settings.llm.origin}</Row>
          <Row label="模型">{settings.llm.model || "（用服务器默认）"}</Row>
          <Row label="流式">{settings.stream ? "开" : "关"}</Row>
          {stageProgress ? <Row label="当前阶段">{stageProgress.name}</Row> : null}
        </div>
      </Section>

      {last?.stages?.length ? (
        <Section title="阶段耗时">
          <div className="space-y-1">
            {last.stages.map((stage) => (
              <div key={stage.id} className="flex items-center justify-between">
                <span>{stage.name}{stage.error ? " ⚠" : ""}</span>
                <span className="tabular-nums text-ink-mute">
                  {stage.duration_ms ?? 0}ms · {stage.usage?.total_tokens ?? 0}t · {stage.calls ?? 0} 次工具
                </span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section title="工具调用">
        <div className="scroll-thin max-h-72 space-y-1 overflow-y-auto">
          {(toolFeed.length ? toolFeed : last?.toolLog ?? []).map((entry, index) => (
            <div key={index} className="rounded-lg bg-black/[0.03] px-2 py-1">
              <span className={entry.type === "tool_call" ? "text-dusk" : entry.ok === false ? "text-sakura-deep" : "text-moss"}>
                {entry.type === "tool_call" ? "→" : "←"}
              </span>{" "}
              <span className="font-mono text-[11px]">{entry.name}</span>
              {entry.summary ? <span className="ml-1 text-ink-mute">{entry.summary}</span> : null}
              {entry.arguments ? (
                <div className="truncate font-mono text-[10px] text-ink-mute">{JSON.stringify(entry.arguments)}</div>
              ) : null}
            </div>
          ))}
          {!toolFeed.length && !last?.toolLog?.length ? <Empty>还没有工具调用</Empty> : null}
        </div>
      </Section>

      <p className="rounded-xl bg-black/[0.03] p-2.5 leading-relaxed text-ink-mute">
        这里显示的是引擎真实执行过的调用。隐藏的关系数值仍然不会出现——
        它们从来不会离开服务器。
      </p>
    </div>
  );
}
