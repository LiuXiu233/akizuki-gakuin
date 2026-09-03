# 秋月学院 · 前端

Next.js 15（App Router）+ TypeScript + Tailwind + zustand。

## 本地开发

```bash
# 1. 先起后端（在仓库根目录）
python3 -m server                     # http://localhost:8000

# 2. 再起前端
cd web
npm install
npm run dev                           # http://localhost:3000
```

首次打开会自动申请一个**用户令牌**（存档钥匙，存在 localStorage）。
到「设置 → 模型」里填入你的 API key 就能开始玩。

## 部署到 Vercel

1. 把仓库连到 Vercel，**Root Directory 设为 `web`**。
2. 环境变量：

| 变量 | 说明 |
|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | 你的后端公网地址，例如 `https://akizuki.example.com` |
| `BACKEND_URL` | 服务端转发用（可与上面相同）；只在使用「经 Vercel 转发」时需要 |
| `LLM_API_KEY` | 可选。设了它，玩家可以选「Vercel 边缘」模式，key 不落在你的服务器上 |
| `LLM_BASE_URL` | 可选。配合上面使用 |

3. 后端记得放开 CORS：`AKIZUKI_ALLOW_ORIGINS=https://你的域名.vercel.app`

### 关于 https → http

Vercel 页面是 https。如果你的后端是裸 http，浏览器会拦截直连请求。
两个办法：

* 给后端配 HTTPS（Caddy / Nginx + 证书，或 Cloudflare Tunnel）—— 推荐
* 在「设置 → 连接」里把访问方式切成**经 Vercel 转发**，请求会走
  `/api/proxy`，由 Next.js 在服务端转发，不受混合内容限制

## 目录

```
app/
├─ page.tsx                      存档列表 / 新建世界 / 角色创建
├─ play/[worldId]/page.tsx       主游戏（沉浸模式 / 面板模式）
├─ api/proxy/[...path]/          后端转发（解决混合内容与 CORS）
└─ api/llm/[provider]/[...path]/ Vercel 边缘 LLM 代理

components/
├─ game.tsx                      顶栏 / 叙事流 / 输入 / 推荐 / 沉浸舞台 / 存档点
├─ panels.tsx                    角色 / 在场 / 图鉴 / 地图 / 日程 / 日志 / 调试
├─ CharacterCreator.tsx          属性分配 + 技能知识选择
├─ SettingsDialog.tsx            连接 / 模型 / Agent / 立绘 / 关于
├─ Portrait.tsx                  立绘与头像（未配置时用配色占位）
└─ ui/                           基础组件

lib/
├─ api.ts                        后端客户端（含 SSE 解析）
├─ turn.ts                       回合执行（后端流式 / 非流式 / 浏览器直连）
├─ llm-browser.ts                浏览器直连模式的最小 LLM 客户端
├─ store.ts                      zustand（设置持久化 + 运行时状态）
└─ types.ts
```
