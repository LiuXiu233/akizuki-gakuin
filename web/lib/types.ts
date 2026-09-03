/** 与后端 server/schemas.py 对应的类型。只声明前端真正用到的字段。 */

export interface Health {
  ok: boolean;
  server_version: string;
  engine_version: string;
  auth_required: boolean;
  server_llm_configured: boolean;
  server_llm_provider: string | null;
  server_llm_model: string | null;
  image_enabled: boolean;
  server_image_configured: boolean;
  image_sfw: boolean;
  max_worlds_per_user: number;
}

export interface WorldMeta {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  pipeline: string;
  seed: number | null;
  turn: number;
  date: string;
  time: string;
  player_name: string | null;
  npc_count: number;
  image_count: number;
}

export interface PlayerStatus {
  health: number;
  energy: number;
  stress: number;
  mood: string;
  money: number;
}

export interface PlayerState {
  ok?: boolean;
  id: string;
  name: string | null;
  age: number | null;
  gender: string | null;
  class: string | null;
  attributes: Record<string, number>;
  status: PlayerStatus;
  conditions: string[];
  skills: Record<string, number>;
  knowledge: Record<string, number>;
  skill_xp: Record<string, number>;
  knowledge_xp: Record<string, number>;
  clubs: string[];
  location: string;
  inventory: Record<string, number>;
}

export interface WorldState {
  ok?: boolean;
  date: string;
  time: string;
  weekday: string;
  weekday_zh: string;
  day_index: number;
  turn: number;
  weather: string;
  weather_zh: string;
  block: string | null;
  day_type: string;
  term: { id: string; name: string } | null;
  school: string;
  location: { id: string; name: string; description: string; tags: string[]; open: boolean };
  calendar_events: Array<{ id: string; name: string; tags: string[] }>;
  class_subjects_today: string[];
  is_class_time: boolean;
  is_club_time: boolean;
  must_sleep: boolean;
}

export interface NearbyCharacter {
  id: string;
  name: string;
  age: number;
  role: string;
  tier: string;
  activity: string | null;
  mood: string | null;
  relationship: string;
  stage: string;
}

export interface ActionContext {
  current_time: { date: string; time: string; weekday: string; block: string | null };
  current_location: { id: string; name: string; tags: string[] };
  nearby_characters: NearbyCharacter[];
  available_locations: Array<{ id: string; name: string; minutes: number; tags: string[] }>;
  current_events: Array<{ id: string; name: string; tags: string[] }>;
  player_energy: number;
  player_stress: number;
  player_money: number;
  player_conditions: string[];
  clubs: string[];
  club_time: boolean;
  class_time: boolean;
  must_sleep: boolean;
  relationships: Array<{ id: string; name: string; label: string; stage: string }>;
  recent_actions: Array<Record<string, unknown>>;
  recent_recommendations: string[];
  romance_opportunity: boolean;
  romance_candidates: Array<{ id: string; name: string; stage: string }>;
  suggested_categories: string[];
  player_skills: Record<string, number>;
  player_knowledge: Record<string, number>;
  constraints: string[];
}

export interface TurnPanel {
  ok: boolean;
  text: string;
  date: string;
  time: string;
  weekday: string;
  location: { id: string; name: string };
  weather: string;
  status: PlayerStatus;
  conditions: string[];
  skills: Record<string, number>;
  knowledge: Record<string, number>;
}

export interface Recommendation {
  text: string;
  minutes: string;
  category: string;
}

export interface DialogueLine {
  npc_id: string;
  name: string;
  text: string;
}

export interface ImageSuggestion {
  kind: "avatar" | "portrait" | "scene" | "cg";
  subject_id: string;
  prompt: string;
  reason?: string;
}

export interface StageInfo {
  id: string;
  name: string;
  role: string;
  text?: string;
  usage?: { input_tokens: number; output_tokens: number; total_tokens: number };
  duration_ms?: number;
  error?: string;
  calls?: number;
}

export interface ToolLogEntry {
  type: "tool_call" | "tool_result";
  stage: string;
  name: string;
  arguments?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
}

export interface TurnResult {
  ok: boolean;
  pipeline: string;
  narration: string;
  narration_clean: string;
  check_text: string;
  growth_text: string;
  random_event: { event_id: string; name: string; category: string } | null;
  dialogue: DialogueLine[];
  recommendations: Recommendation[];
  images: ImageSuggestion[];
  panel: TurnPanel;
  panel_text: string;
  world: WorldState;
  context: ActionContext;
  turn: number;
  stages: StageInfo[];
  tool_log: ToolLogEntry[];
  usage: { input_tokens: number; output_tokens: number; total_tokens: number };
  stage_errors?: string[];
}

export interface CharacterState {
  ok: boolean;
  id: string;
  name: string;
  age: number;
  gender: string;
  class: string | null;
  role: string;
  tier: string;
  appearance: string;
  personality: string;
  speech_style: string;
  interests: string[];
  club: string | null;
  attributes: Record<string, number>;
  skills: Record<string, number>;
  knowledge: Record<string, number>;
  status: PlayerStatus;
  location: string;
  current_activity: string | null;
  relationship_with_player: { label: string; stage: string; hints: string[]; shared_experiences: number };
  schedule_now: { location: string; activity: string };
  social_circle?: string[];
  romance_available?: boolean;
}

export interface MetaBundle {
  ok: boolean;
  rules: Record<string, any>;
  content_rules: Record<string, any>;
  locations: Array<{ id: string; name: string; area: string; zone: string; tags: string[]; open: boolean; minutes_from_here: number }>;
  clubs: Array<{ id: string; name: string; location: string; members: string[]; description: string; activity_days?: string[] }>;
  registry: Record<string, { count: number; ids: string[] }>;
  player_presets: Array<{
    id: string; name: string; description: string;
    attributes: Record<string, number>; skills: string[]; knowledge: string[];
  }>;
  creation_rules: Record<string, any>;
  attributes: Record<string, { name: string; description: string }>;
  skills: Array<{ id: string; name: string; category: string; attribute: string; description: string }>;
  knowledge: Array<{ id: string; name: string; category: string; description: string }>;
}

export interface PipelineInfo {
  id: string;
  name: string;
  description: string;
  stages: string[];
  stage_count: number;
}

/** SSE 事件 */
export type TurnEvent =
  | { type: "turn_start"; pipeline: string; pipeline_name: string; stages: StageInfo[] }
  | { type: "stage_start"; stage: string; name: string; role: string; index: number; total: number }
  | { type: "delta"; stage: string; subject?: string; iteration: number; text: string }
  | { type: "tool_call"; stage: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; stage: string; name: string; ok: boolean; summary: string }
  | { type: "subject_start"; stage: string; subject: string }
  | { type: "dialogue"; stage: string; npc_id: string; name: string; text: string }
  | { type: "stage_end"; stage: string; result: StageInfo }
  | { type: "stage_error"; stage: string; message: string; subject?: string }
  | { type: "turn_end"; turn: TurnResult }
  | { type: "error"; error?: string; message?: string };
