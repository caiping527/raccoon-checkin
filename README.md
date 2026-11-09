# 商汤小浣熊 · 云端每日登录动作

GitHub Actions 每天替每个账号模拟一次「桌面端登录动作」，确认当日 300 积分入账。**不开电脑、不装客户端常驻、不用点任何按钮**。

本地路径：`D:\小浣熊签到更新`　私有仓库：`caiping527/raccoon-checkin`

**根目录入口**：

- `密码登录.bat` — **★ 推荐**：输入手机号 + 密码，直接向官方换独立令牌，完全不依赖桌面客户端
- `一键更新.bat` — 备用：读取客户端当前登录态里的令牌
- `安全换号.bat` — 备用：保存 / 替换本机登录态实现免登出切换

> ⚠️ **服务端按登录会话 `sid` 鉴权**：在客户端点「退出登录」或重新登录，会让该账号已登记到云端的令牌立刻失效（HTTP 401 `authorization_verify_error`，`code=200003`）。实测 refresh 接口、更换 `X-Client-Platform` 请求头均无法复活。
>
> 而且客户端把登录态存在本机 `auth.json`，**关掉软件再打开会自动恢复成原来那个号**，不会停在登录页；想登新号只能点「退出登录」，而那一下正好会撤销会话 —— 这是个死循环。
>
> **解法：不要在客户端登录任何一个号。** `密码登录.bat` 走官方 `POST /api/iam/v1/auth/login/password/phone`（官网同款 AES-128-CFB 加密），拿到的是**独立会话**，桌面端切号 / 退出登录都影响不到它。三个号依次跑三次即可。若选择把密码一并存入云端（工具里直接回车即选 `Y`，默认推荐），云端每天自动重新登录取新令牌，连 30 天过期都不用管。详细步骤见 `使用说明.txt`。

架构 1:1 复刻 `caiping527/workbuddy-checkin-public`：

| 层 | 参考系统 | 本项目 |
|---|---|---|
| 调度 | Actions `cron 0 1 * * *` | Actions `cron 17 1 * * *`（北京 09:17，避开整点） |
| 多账号 | `WB_ACCOUNTS` + `acct-<尾号>` Environment 动态 matrix | `RACCOON_ACCOUNTS` + `acct-<手机号尾号>` Environment 动态 matrix |
| 凭据 | 仓库级 / 环境级 Secret，日志脱敏 | 环境级 Secret，令牌只走 stdin，结果文件白名单输出 |
| 通知 | 企微自建应用 / 群机器人，仅失败推送 | 同配置格式，失败 + 令牌临期提前 7 天提醒，**只显示手机号尾号** |
| 轮换回写 | 本机一键更新 | 云端 `GH_PAT` 回写 + 本机一键更新（双保险） |
| 本机工具 | `D:\WORKBUDDY签到更新\一键更新.bat` | `D:\小浣熊签到更新\一键更新.bat`（根目录） |

零第三方依赖（Python 标准库），Python 3.9+。

---

## 账号标识：推送里只有手机号尾号

登录令牌 JWT 的 `sub` 是**内部用户 ID 尾号**（当前账号为 `7080`），不是手机号尾号（客户端显示 `+86 134****4060`）。

本机工具每次双击会调只读接口 `GET https://xiaohuanxiong.com/api/iam/v1/account`，返回 `data.user.phone = "134****4060"`，**自动取尾 4 位 `4060`** 作为显示标签：

- Environment 名 `acct-4060`、`RACCOON_ACCOUNTS=4060`、企微消息里的 `账号：手机号尾号4060` —— 全是手机号尾号。
- 内部 ID 尾号 `7080` 只写进该环境的 Secret `RACCOON_ACCOUNT_ID`，专做**防串号**校验；失败文案也不包含它，绝不进推送。
- `账号标签.txt` 由工具**自动维护**（`内部ID尾号=手机号尾号`），仅作离线缓存与兜底，平时不用看、不用改。

**多账号零冲突**：`密码登录.bat` 里输入哪个号的手机号+密码，就登记哪个号，工具自动识别手机号尾号、自动追加到账号列表，旧号不受影响。三个号无需在客户端来回切换。只有 IAM 接口真的不可用且缓存里也没这个号时，才会退回用输入的手机号尾 4 位。

## 关于「300 积分会不会过期」

**以客户端实测为准，本项目不预设结论。** 目前实测到的事实：

- `/api/web/points/v1/balance`：`available_points=9300`、`daily_points=300`、`reward_points=9000`，积分分「每日积分」与「奖励积分」两类。
- `/api/web/points/v1/bills` 的 `daily_grant` 记录带 `expire_at` 字段（本次为 7 天后），但账本中**没有任何过期扣减记录**。
- 该账号 2026-11-09 当天才注册，缺少跨天观测数据，无法据此证明 300 分会在 7 天后消失。

因此成功判据是「**今日 daily_grant 已入账且积分 > 0**」，`expire_at - grant_at` 的档位只写进 `result.json` 作观测，**不参与成败判定**。这样积分过期或不过期都不会误报失败、误发企微告警。

想自己得到结论：每天看运行摘要里的 `balance.daily_points`。若逐日累积（300 → 600 → 900）就是不过期；若每天回落到 300，说明旧额度确实失效——届时云端每天触发登录依然有价值。

## 云端签到会不会打断桌面端任务

**不会。** 云端只发只读 GET：`setting_info`、`bills`、`balance`，不写入任何会话状态，不轮换令牌。本机 `auth.json` 只有 `access_token`、没有 `refresh_token`，所以 `refresh` 接口在当前配置下**永远不会被调用**。本机一键更新工具额外调的 IAM 账号接口也是纯只读、不触发登录动作。

实测佐证：2026-11-09 12:48 云端完整签到后，桌面客户端 14:12 仍正常显示、9300 积分可见，账本无重新登录记录。

「会话共享」风险方向相反：**是客户端退出/重新登录可能让云端令牌失效（云端出现 401），不是云端打断客户端正在跑的任务**。真出现 401 时，重新双击一次 `一键更新.bat` 即可。

## 链路

```
[可选] POST /api/web/auth/v1/refresh               refresh_token → access_token（当前配置不触发）
       GET  /api/web/office/v3/setting_info        桌面客户端启动动作（主触发，只读）
[兜底] POST /api/web/desktop/v1/login/points/grant 幂等发放（仅账本未见当日记录时）
       GET  /api/web/points/v1/bills               核对当日 daily_grant 入账
       GET  /api/web/points/v1/balance             观测积分分项
--- 本机工具额外用（只读，不触发签到）
       GET  /api/iam/v1/account                    取掩码手机号 → 显示标签
```

接口形态参考了 [clzwqoii/aipunch](https://github.com/clzwqoii/aipunch) 的 `providers/raccoon.ts` 与 [dsh-multi-agent-gateway](https://github.com/a1141171521/dsh-multi-agent-gateway) 的 `docs/RACCOON-API.md`；IAM 账号接口由本机客户端缓存响应反查并实跑确认。

---

## 部署

### 1. 建私有仓库并推送

```bash
cd /d/小浣熊签到更新
gh repo create caiping527/raccoon-checkin --private --source=. --push
```

> 建议 **Private**：公开仓库 60 天无活动会被 GitHub 停用定时任务；私有仓库也不暴露账号数量。
> 若仓库名不同，给本机工具加 `--repo <owner>/<repo>` 或设 `RACCOON_REPO` 环境变量。

### 2. 配置仓库级 Secret（Settings → Secrets and variables → Actions）

| 名称 | 必填 | 说明 |
|---|:---:|---|
| `WECOM_APP` | 二选一 | 企业微信自建应用：`corpid,corpsecret,touser,agentid` |
| `WECOM_WEBHOOK` | 二选一 | 企业微信群机器人完整 webhook（或仅 key） |
| `GH_PAT` | 推荐 | 仅授权本仓库 **Secrets 读写** 的细粒度令牌，用于云端回写轮换后的令牌。不配也能跑 |
| `RACCOON_ACCESS_TOKEN` | ❌ | **不要**在仓库级创建，每个账号放自己的 Environment 里 |

仓库变量（Variables，非 Secret）：

| 名称 | 说明 |
|---|---|
| `RACCOON_ACCOUNTS` | 手机号尾号列表，由 `一键更新.bat` 自动维护，**无需手动建** |
| `SUCCESS_NOTIFY` | 可选，设 `1` 则签到成功也推送；默认成功不打扰 |

### 3. 本机设置 GitHub 令牌

```powershell
setx GH_TOKEN "你的GitHub令牌"
```

你现有的通用长期令牌可用；更安全的做法是新建 **Fine-grained token**：Repository access 只勾本仓库，Permissions 只把 **Secrets** 设为 Read and write、**Actions** 设为 Read and write。

### 4. 登录小浣熊 → 双击根目录 `一键更新.bat`

工具自动识别手机号尾号 → 建 Environment → 写两个 Secret → 登记账号 → 触发只读自检。
再加账号：客户端退出 → 登录另一个号 → 再双击一次，**无需改任何代码或文件**。

### 5. 启用 Actions 并手动验证

Actions 页启用 workflow → `Run workflow`（可勾「仅自检」先做只读验证）→ 看到 ✅ 即部署完成。

---

## 日常运行

- 每天北京时间 **09:17** 自动执行（GitHub 定时为 best-effort，可能延迟数分钟到数十分钟）。
- 每个账号一个独立 job，`fail-fast: false`，一个账号失败不影响其他账号。
- **成功不打扰**；只有签到失败、或令牌剩余 ≤7 天时推企业微信，消息写 `账号：手机号尾号4060`。
- 令牌实测有效期 **30 天**（JWT `exp`）。收到「令牌临期」提醒后，登录该账号双击一次 `一键更新.bat` 即可。

## 目录

```
.github/workflows/checkin.yml   云端定时 + 动态多账号 + 企微通知 + 令牌回写
scripts/raccoon_checkin.py      签到核心（零依赖）
scripts/wecom_notify.py         企业微信推送策略
scripts/summarize_result.py     脱敏运行摘要
update.py                       本机一键更新主程序
一键更新.bat                    ★ 双击入口（根目录）
账号标签.txt                    自动维护的 内部ID→手机号尾号 映射（无需手改）
使用说明.txt                    用户手册
更新记录.txt                    本机运行历史
tests/                          标准库单元测试
```

## 验证

```bash
python -m unittest discover -s tests -v
python scripts/raccoon_checkin.py --diagnose   # 需配置 RACCOON_ACCESS_TOKEN，只读自检
```

## 已知限制与风险

1. **客户端重登会让云端令牌失效**：方向是客户端 → 云端，出现 401 时重新双击一键更新。**云端不会打断客户端任务**。
2. **接口为逆向 Web API**：上游随时可能变更或加风控；主号请谨慎。
3. **合规**：平台服务协议通常限制自动化脚本调用，仅用于自己的账号，风险自负。
4. **GitHub Actions**：公开仓库 60 天无活动会停用 schedule（偶尔手动触发保活）；私有仓库免费额度足够每天 1 次 × 多账号。
5. 本机 `auth.json` 只有 `access_token`（无 `refresh_token`），因此默认路径是「30 天登录一次 + 提前 7 天提醒」，不是永久免维护。
6. `expire_at` 的实际业务含义未经跨天验证，本项目仅作观测，不据此告警。
7. 手机号来自 IAM 接口的掩码号码（`134****4060`），只取后 4 位；接口不可用时回退到 `账号标签.txt` 缓存或手动输入。
