# 部署指南

当前线上环境的完整说明。**这份文档是给你自己看的操作手册**，出问题先翻这里。

---

## 0. 现在部署在哪

| 组件 | 地址 | 说明 |
|---|---|---|
| 前端 | https://ga.xiu.moe | Vercel，已连 GitHub，推 main 自动部署 |
| 前端（备用域名） | https://akizuki-gakuin.vercel.app | 同一个部署 |
| 后端 | https://mo.xiu.moe | 腾讯云 43.165.178.110，Caddy + Let's Encrypt |
| 仓库 | https://github.com/LiuXiu233/akizuki-gakuin | 私有 |
| 服务器登录 | `ssh -i ~/Downloads/xiusg.pem ubuntu@43.165.178.110` | 免密 sudo |
| 代码目录 | `/opt/akizuki` | git 克隆，可 `git pull` 更新 |
| 数据目录 | `/opt/akizuki/data` | 所有用户的存档与图片 |

### 已配置的上游（2026-09-03 实测可用）

| | 配置 | 备注 |
|---|---|---|
| LLM | DeepSeek `deepseek-v4-pro` @ `https://api.deepseek.com` | 可选 `deepseek-v4-flash`（更便宜更快） |
| 文生图 | `gpt-image-2` @ `http://api.xiu.moe/v1` | **注意是 http**：该网关只开了 80 端口，443 拒绝连接；一张图约 57 秒 |
| 推理开关 | `AKIZUKI_LLM_EXTRA_PARAMS={"reasoning_effort":"none"}` | 见下文 |

### 访问口令

```
mHkqDVdBUFsGfORU42Kpn3-1
```

第一次打开网页 → 设置 → 连接 → 填进「访问口令」。没有它任何接口都是 401。

要改口令：改 `/opt/akizuki/.env` 里的 `AKIZUKI_ACCESS_PASSWORD`，然后 `sudo systemctl restart akizuki`。

### 用户令牌（存档钥匙）

首次进入会自动生成一串 32 位十六进制，存在浏览器 localStorage。
**换设备时到「设置 → 连接」把它复制过去，存档就跟着走。**
丢了就找不回存档了（服务器上以它作为目录名）。

---

## 1. LLM API 放哪

有两个位置，**优先级：玩家自带 > 服务器预置**。

### A. 服务器预置（你自己用最省事）

```bash
ssh -i ~/Downloads/xiusg.pem ubuntu@43.165.178.110
sudo -u ubuntu nano /opt/akizuki/.env
```

```bash
# ---------- OpenAI 兼容（官方 / Azure / 各类中转站 / 本地 vLLM）----------
AKIZUKI_LLM_PROVIDER=openai
AKIZUKI_LLM_BASE_URL=https://api.openai.com/v1      # 中转站就填中转站的，写到 /v1 为止
AKIZUKI_LLM_API_KEY=sk-xxxxxxxx
AKIZUKI_LLM_MODEL=gpt-4o

# ---------- 或者 Anthropic ----------
AKIZUKI_LLM_PROVIDER=anthropic
AKIZUKI_LLM_BASE_URL=https://api.anthropic.com      # 留空也会用这个默认值
AKIZUKI_LLM_API_KEY=sk-ant-xxxxxxxx
AKIZUKI_LLM_MODEL=claude-sonnet-5
```

```bash
sudo systemctl restart akizuki
curl -s https://mo.xiu.moe/api/health | grep server_llm     # 应该显示 true
```

配好之后，网页里「设置 → 模型 → API Key」**留空**即可，会自动用服务器的 key。

### B. 玩家自带（每个人用自己的额度）

网页 →「设置 → 模型」，填 Base URL / API Key / 模型。
key 只随请求发一次，**服务器不落盘、不写日志**。

三种「请求从哪里发出」的含义：

| 选项 | key 存在哪 | 支持的流水线 | 什么时候用 |
|---|---|---|---|
| **自建后端**（默认） | 随请求发到 mo.xiu.moe，用完即弃 | 全部 | 一般情况 |
| Vercel 边缘 | Vercel 环境变量 `LLM_API_KEY` | 全部 | 不想让 key 出现在自己服务器上 |
| 浏览器直连 | 只在这台设备的 localStorage | 仅单 Agent | 完全不信任任何服务器时 |

### 模型怎么选

| 用途 | 推荐 | 理由 |
|---|---|---|
| 当前配置 | `deepseek-v4-pro` | 已实测：中文叙事细腻，工具调用稳定 |
| 想更快更省 | `deepseek-v4-flash` | 同一个 key 就能用，NPC 台词这类短输出完全够 |
| 换 Anthropic | `claude-sonnet-5` / `claude-opus-5` | 记得把 provider 改成 anthropic，并删掉 reasoning_effort |

**分阶段用不同模型**（`multi` 流水线最划算的用法）：编辑
`/opt/akizuki/pipelines/multi.yaml`，给某个阶段加一行 `model:` ——

```yaml
  - id: npc_react
    name: NPC 反应
    role: npc
    model: claude-haiku-4-5-20251001    # NPC 台词短，用便宜模型
  - id: narrate
    name: 旁白合成
    role: narrator
    model: claude-opus-5                # 旁白最吃质量，用最好的
```

改完 `sudo systemctl restart akizuki`。

### 每回合大概花多少（真机实测，DeepSeek v4-pro，关闭推理）

| 流水线 | 耗时 | token | 叙事 | NPC 台词分离 |
|---|---|---|---|---|
| `single` | ~24 秒 | ~85,000 | 短 | 否 |
| `dual` | ~23 秒 | ~46,000 | 中 | 是 |
| `multi` | ~45 秒 | ~60,000 | 长 | 是 |

**和直觉相反，`single` 最贵**——它带完整 AGENT.md 且拿到全部 51 个工具，
而 system prompt 会在每一轮工具迭代里重发。

**日常用 `dual`，重要场景（告白 / 文化祭 / 修学旅行）再切 `multi`。**
网页「设置 → Agent」一键切换，不用重启任何东西。

### 推理模型（重要）

`deepseek-v4-pro` 是推理模型：一句话回答要先烧 182 个思考 token（5.7 秒），
关掉后只要 52 token（2.3 秒），而且**工具调用完全不受影响**。
一个 multi 回合有 5~10 次调用，这是 3 倍的时间和费用差。

服务器已经配置为关闭推理：

```bash
AKIZUKI_LLM_EXTRA_PARAMS={"reasoning_effort":"none"}
```

想让某个阶段重新开启推理（比如让旁白更讲究），编辑
`/opt/akizuki/pipelines/multi.yaml` 的对应阶段：

```yaml
  - id: narrate
    extra_params: {reasoning_effort: "medium"}
```

> 这个参数是 DeepSeek / o 系列 / gpt-5 这类推理模型专有的。
> 换成 gpt-4o 一类的非推理模型时**必须删掉**，否则上游会直接报错。

---

## 2. 文生图 API 放哪

**不配置就整体关闭**——界面用配色占位符，不会报错，也不会请求任何外部服务。

### 服务器预置

```bash
sudo -u ubuntu nano /opt/akizuki/.env
```

```bash
AKIZUKI_IMAGE_ENABLED=true
AKIZUKI_IMAGE_PROVIDER=openai
AKIZUKI_IMAGE_BASE_URL=http://api.xiu.moe/v1   # 当前配置。注意是 http 且带 /v1
AKIZUKI_IMAGE_API_KEY=sk-xxxxxxxx
AKIZUKI_IMAGE_MODEL=gpt-image-2
AKIZUKI_IMAGE_SIZE=1024x1024
AKIZUKI_IMAGE_SFW=true                   # 强烈建议保持 true
AKIZUKI_IMAGE_STYLE=                     # 留空用内置日系动画风
```

### 接非 OpenAI 格式（SD WebUI / ComfyUI / 国内厂商）

```bash
AKIZUKI_IMAGE_PROVIDER=custom
AKIZUKI_IMAGE_BASE_URL=http://127.0.0.1:7860/sdapi/v1/txt2img   # 完整接口地址
AKIZUKI_IMAGE_API_KEY=                                          # 没有就留空
AKIZUKI_IMAGE_REQUEST_TEMPLATE={"prompt": "{prompt}", "steps": 28, "width": 1024, "height": 1024}
AKIZUKI_IMAGE_RESPONSE_PATH=images.0
```

`{prompt}` / `{model}` / `{size}` 会被替换。取图路径支持 `data.0.b64_json`、
`images.0`、`output.0.url` 这种点路径；取到的值是 URL 就下载，是 base64 就解码。

网页「设置 → 立绘」里也能填同样的东西（玩家自带），字段一一对应。

### 四类图与花费

| 类型 | 何时生成 | 数量级 |
|---|---|---|
| `avatar` 头像 | 图鉴里点「生成」，或画师 Agent 建议 | 每个认识的人 1 张 |
| `portrait` 立绘 | 沉浸模式点人物 | 每个重要角色 1 张 |
| `scene` 场景图 | 画师 Agent 建议 | 42 个地点封顶 |
| `cg` 事件 CG | 重大剧情节点 | 每次都是新的，最贵 |

**全部按需生成 + 永久缓存**，同一个 subject 第二次直接读本地文件。
「设置 → 立绘 → 自动出图」关掉后，只有你手动点「生成」才会花钱。

### 尺度

`AKIZUKI_IMAGE_SFW=true`（默认）会给提示词加着装约束、剔除风险词，
并且**客户端无法关闭**。原因很实际：主流图像 API 会直接拒绝尺度内容，
不加约束的结果是大量生成失败还照样计费。**这个开关只影响图像，文字叙事完全不受影响。**

要放开：改服务器 `.env` 为 `AKIZUKI_IMAGE_SFW=false`，此时客户端的开关才生效。
只有接自己的私有 SD 时这么做才有意义。

---

## 3. 日常运维

```bash
# 状态与日志
sudo systemctl status akizuki
sudo journalctl -u akizuki -f              # 实时日志
sudo journalctl -u caddy -n 50             # 证书 / 反代日志
tail -f /opt/akizuki/data/engine.log 2>/dev/null || tail -f /opt/akizuki/state/engine.log

# 重启
sudo systemctl restart akizuki
sudo systemctl reload caddy                # 改完 Caddyfile

# 更新代码（本地 push 之后）
cd /opt/akizuki && git pull
.venv/bin/pip install -r server/requirements.txt   # 依赖有变动时才需要
sudo systemctl restart akizuki

# 备份存档（唯一重要的东西）
tar czf ~/akizuki-backup-$(date +%F).tar.gz -C /opt/akizuki data
```

> **切记：uvicorn 只能开 1 个 worker。** 世界会话缓存和每个世界的锁都在进程内，
> 多 worker 会让同一个存档在两个进程里各有一份，互相覆盖。systemd 单元里已经写死了。

### 前端更新

推到 GitHub main 分支即自动部署：

```bash
git push          # Vercel 自动构建 web/ 并上线
```

手动部署：`vercel deploy --prod --yes`（在仓库根目录执行，Root Directory 已设为 `web`）。

---

## 4. 关键配置速查

### 服务器 `/opt/akizuki/.env`

| 变量 | 现在的值 | 说明 |
|---|---|---|
| `AKIZUKI_DATA_DIR` | `/opt/akizuki/data` | 存档与图片 |
| `AKIZUKI_ACCESS_PASSWORD` | 见上文 | 留空 = 任何人可访问 |
| `AKIZUKI_ALLOW_ORIGINS` | `https://ga.xiu.moe,https://akizuki-gakuin.vercel.app,http://localhost:3000` | CORS 白名单 |
| `AKIZUKI_MAX_CACHED_SESSIONS` | `16` | 2G 内存下的保守值 |
| `AKIZUKI_MAX_WORLDS_PER_USER` | `20` | 每人最多几个存档 |
| `AKIZUKI_MAX_USERS` | `0` | 0 = 不限 |

> Vercel 的 **preview 部署**域名每次都不同，会被 CORS 拦。
> 要么把该域名加进 `AKIZUKI_ALLOW_ORIGINS`，要么在网页「设置 → 连接」
> 切成「经 Vercel 转发」——那条路是服务端转发，不受 CORS 限制。

### Vercel 环境变量

| 变量 | 值 | 作用 |
|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | `https://mo.xiu.moe` | 前端默认后端地址 |
| `BACKEND_URL` | `https://mo.xiu.moe` | `/api/proxy` 转发目标 |
| `LLM_API_KEY` | *（未设）* | 设了才能用「Vercel 边缘」模式 |
| `LLM_BASE_URL` | *（未设）* | 配合上一条 |

改：`vercel env add <名字> production`，然后重新部署。

---

## 5. 出问题先查这里

| 现象 | 原因 | 处理 |
|---|---|---|
| 网页说「连不上后端」 | 口令没填 / 填错 | 设置 → 连接 → 访问口令 |
| 401 | 用户令牌无效 | 清掉令牌重新进入会自动新建（**会丢存档**） |
| 回合报「没有可用的 API key」 | 两边都没配 key | 设置 → 模型，或服务器 `.env` |
| 回合转很久没反应 | 上游超时 | `journalctl -u akizuki -f` 看有没有 502/504 |
| 流式没有逐字效果 | 反代缓冲了 SSE | Caddyfile 里必须有 `flush_interval -1` |
| 立绘一直是色块 | 没配图像 API | 设置 → 立绘，或服务器 `.env` |
| 沉浸模式没有背景图 | 该地点还没画过 | 设置 → 立绘 → 预生成场景背景图；或开「自动出图」，到新地点会后台补 |
| 出图很慢 | `gpt-image-2` 一张约 57 秒 | 正常现象；关掉「自动出图」改成手动点 |
| 图像 API 连不上 | 网关的 443 端口没开 | base_url 用 `http://`（后端是服务端调用，不受混合内容限制） |
| 图片 403/404 | 用户令牌换过 | 图片按用户隔离，换令牌等于换人 |
| 证书过期 | Caddy 自动续期失败 | `sudo journalctl -u caddy | grep -i renew` |
| 内存吃紧（2G 机器） | 缓存世界太多 | 调小 `AKIZUKI_MAX_CACHED_SESSIONS` |

### 健康检查一条龙

```bash
curl -s https://mo.xiu.moe/api/health | python3 -m json.tool
```

`auth_required: true` 说明口令生效；`server_llm_configured` 告诉你服务器有没有预置 key。

---

## 6. 叙事日志

每一回合的正文、台词、判定、成长、推荐都会落盘到
`data/users/<uid>/worlds/<wid>/journal.jsonl`，退出重进后自动恢复。

* 格式是 JSON Lines：追加成本恒定，单行损坏不影响其余记录
* 超过 400 条会自动只保留最近的
* 想清空某个世界的历史：删掉那个文件即可，世界状态不受影响

**世界状态（关系/记忆/时间）和叙事日志是两套东西**——
前者是引擎存档，后者是给人读的故事。回滚存档点不会回滚日志。

---

## 7. 场景背景图

沉浸模式的背景来自 `scene` 类图像，一个地点一张，生成后永久缓存。

* 「设置 → 立绘 → 预生成场景背景图」提供两个范围：
  常用 12 个地点（约 12 分钟）或全部 42 个（约 40 分钟），带进度且可随时停止
* 开启「自动出图」后，走到没画过的地方会在后台补一张，不阻塞任何操作
* 出图不占用世界锁——等图的那几十秒里你照样可以继续玩

---

## 8. 安全边界

* 后端只监听 `127.0.0.1:8000`，公网只能经 Caddy 的 443 进来。
* `.env` 权限 600，只有 ubuntu 用户可读。
* systemd 单元开了 `NoNewPrivileges` / `ProtectSystem=full` / `ProtectHome=read-only`，
  可写目录限定在 `data` / `state` / `saves`。
* GitHub 用的是**只读部署密钥**，服务器推不了代码。
* 图片文件接口有严格的路径校验，跨用户与路径穿越都会被拒。
* **隐藏的关系数值（attraction / romantic_interest / trust）从来不会离开服务器**——
  这不是前端不显示，是接口根本不返回。
