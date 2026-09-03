/**
 * 引擎内部标识 → 中文显示名。
 *
 * 引擎里的 id 一律是英文 snake_case（这是规则层的稳定标识，不能改），
 * 但它们**不该出现在玩家眼前**。所有面向玩家的地方都要过这里。
 */

export const MOOD: Record<string, string> = {
  normal: "平静", sleepy: "困倦", tired: "疲惫", energetic: "精神很好",
  inspired: "有灵感", nervous: "紧张", embarrassed: "尴尬", confident: "自信",
  stressed: "焦躁", hungry: "饿", focused: "专注", sick: "不舒服",
  excited: "兴奋", relaxed: "放松",
};

export const CONDITION: Record<string, string> = {
  tired: "有点累", exhausted: "精疲力竭", stressed: "压力有点大", overloaded: "压力过载",
  hungry: "稍微有些饿", sleepy: "困", focused: "注意力集中", inspired: "有灵感",
  confident: "状态不错", nervous: "有点紧张", embarrassed: "有点尴尬", sick: "身体不舒服",
  excited: "兴奋", relaxed: "放松", energetic: "精力充沛",
};

export const TIER: Record<string, string> = {
  background: "路人", supporting: "熟人", core: "重要角色",
};

export const ROLE: Record<string, string> = {
  student: "学生", teacher: "教职员",
};

export const CLASS_NAME: Record<string, string> = {
  class_1a: "一年A班", class_1b: "一年B班", class_1c: "一年C班",
  class_2a: "二年A班", class_2b: "二年B班", class_2c: "二年C班",
  class_3a: "三年A班", class_3b: "三年B班", class_3c: "三年C班",
};

export const RELATIONSHIP_STAGE: Record<string, string> = {
  stranger: "还不认识", acquaintance: "认识", friend: "关系不错",
  close_friend: "关系亲近", ambiguous: "似乎有些暧昧", dating: "正在约会阶段",
  relationship: "正在交往", strained: "关系紧张", former_partner: "曾经的恋人",
};

export const CATEGORY: Record<string, string> = {
  social: "社交", romance: "恋爱", hobby: "兴趣", study: "学习",
  explore: "探索", rest: "休息", club: "社团", event: "活动",
  life: "生活", other: "其它",
};

export const SUBJECT: Record<string, string> = {
  mathematics: "数学", physics: "物理", chemistry: "化学", biology: "生物",
  literature: "国文", history: "历史", geography: "地理", economics: "经济",
  english: "英语", art_or_music: "美术/音乐", home_ec: "家政", pe: "体育",
  free_study: "自习", exam_prep: "备考", homeroom_activity: "班会",
};

export const DAY_TYPE: Record<string, string> = {
  school: "上课日", school_no_club: "上课日（社团休息）",
  half_day: "半天课", holiday: "休息日", vacation: "假期",
};

export const LOCATION_TAG: Record<string, string> = {
  entrance: "出入口", meeting: "碰面", morning: "清晨", letters: "信件",
  rumor: "传闻", classroom: "教室", home: "据点", study: "学习",
  social: "社交", senior: "高年级", transit: "通行", encounter: "偶遇",
  quiet: "安静", private: "私密", lunch: "午休", romantic: "适合独处",
  view: "视野好", outdoor: "户外", photo: "适合拍照", knowledge: "知识",
  rest: "休息", care: "照料", sports: "运动", club: "社团", noisy: "吵闹",
  summer: "夏季限定", music: "音乐", art: "艺术", cooking: "料理",
  organization: "组织", formal: "正式", hub: "枢纽", writing: "写作",
  games: "游戏", media: "媒体", cheap: "便宜", food: "吃饭", shop: "购物",
  date: "适合约会", group: "多人", walk: "散步", festival: "祭典",
  entertainment: "娱乐", coffee: "咖啡", tradition: "传统", night: "夜晚",
  trip: "出行", sunset: "夕阳", teacher: "教师", chore: "杂务", info: "信息",
  result: "结果", small: "小事", comedy: "轻松", seasonal: "季节",
  atmosphere: "氛围", memory: "回忆", kindness: "善意", life: "生活",
  bicycle: "自行车", sleep: "睡眠", penalty: "深夜", free: "自由",
  message: "消息", intent: "心意", ambiguous: "暧昧", classic: "经典",
  object: "物件", deep: "深谈", trust: "信任", milestone: "里程碑",
  subtle: "微妙", jealousy: "吃醋", drama: "波折", misunderstanding: "误会",
  reconcile: "和好", casual: "随意", play: "玩乐", couple: "恋人",
  confession: "告白", major: "重要", prep: "筹备", farewell: "告别",
  emotional: "情绪", serious: "严肃", family: "家庭", body: "身体",
  setback: "挫折", growth: "成长", observe: "旁观", network: "人脉",
  new_npc: "新面孔", help: "帮助", concern: "在意", mentor: "前辈",
  reconnect: "重新联系", callback: "旧事", celebration: "庆祝",
  chocolate: "巧克力", yukata: "浴衣", performance: "演出", exam: "考试",
  ceremony: "典礼", busy: "忙碌", rain: "雨", weather: "天气",
  check: "判定", pair: "两人", intimacy: "亲密", adult: "成人话题",
  rejection: "拒绝", npc_npc: "他人之间", mystery: "谜", trouble: "麻烦",
  interest: "兴趣", pressure: "压力", conflict: "分歧", routine: "日常",
  wait: "等待", sport: "运动", town: "街区", swimming: "游泳",
};

export const PIPELINE: Record<string, string> = {
  single: "单 Agent", dual: "双 Agent", multi: "多 Agent",
};

export const LLM_ORIGIN: Record<string, string> = {
  backend: "自建后端", vercel: "Vercel 边缘", browser: "浏览器直连",
};

function pick(table: Record<string, string>, value: unknown, fallback = ""): string {
  if (typeof value !== "string" || !value) return fallback;
  return table[value] ?? value;
}

export const zh = {
  mood: (v: unknown) => pick(MOOD, v, "—"),
  condition: (v: unknown) => pick(CONDITION, v),
  tier: (v: unknown) => pick(TIER, v),
  role: (v: unknown) => pick(ROLE, v),
  className: (v: unknown) => pick(CLASS_NAME, v),
  stage: (v: unknown) => pick(RELATIONSHIP_STAGE, v),
  category: (v: unknown) => pick(CATEGORY, v),
  subject: (v: unknown) => pick(SUBJECT, v),
  dayType: (v: unknown) => pick(DAY_TYPE, v),
  tag: (v: unknown) => pick(LOCATION_TAG, v),
  pipeline: (v: unknown) => pick(PIPELINE, v),
  llmOrigin: (v: unknown) => pick(LLM_ORIGIN, v),
  /** 标签数组：翻译已知的，丢掉纯英文的未知项，避免把引擎内部词甩给玩家 */
  tags: (values: unknown, limit = 3): string[] => {
    if (!Array.isArray(values)) return [];
    return values
      .map((v) => (typeof v === "string" ? LOCATION_TAG[v] : null))
      .filter((v): v is string => !!v)
      .slice(0, limit);
  },
};
