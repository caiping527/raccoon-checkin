# 商汤小浣熊 · 云端每日登录延效

GitHub Actions 每天替每个账号模拟一次「桌面端登录动作」，把当日 300 积分的有效期从当日 24:00 延长到 7 天。**不开电脑、不装客户端常驻、不用点任何按钮**。

架构 1:1 复刻 `caiping527/workbuddy-checkin-public`：

| 层 | 参考系统 | 本项目 |
|---|---|---|
| 调度 | Actions `cron 0 1 * * *` | Actions `cron 17 1 * * *`（北京 09:17，避开整点） |
| 多账号 | `WB_ACCOUNTS` + `acct-<尾号>` Environment 动态 matrix | `RACCOON_ACCOUNTS` + `acct-<尾号>` Environment 动态 matrix |
| 凭据 | 仓库级 / 环境级 Secret，日志脱敏 | 环境级 Secret，令牌只走 stdin，结果文件白名单输出 |
| 通知 | 企微自建应用 / 群机器人，仅失败推送 | 同配置格式，失败 + 令牌临期提前 7 天提醒 |
| 轮换回写 | 本机一键更新 | 云端 `GH_PAT` 回写 + 本机一键更新（双保险） |
| 本机工具 | `D:\WORKBUDDY签到更新\一键更新.bat` | `tools\一键更新.bat` |

零第三方依赖（Python 标准库），Python 3.9+。

---

## 为什么不是「点签到」

小浣熊没有签到按钮：每日 00:00 无条件发 300 积分，**当天有登录动作才延长有效期**（未延效 `expire_at - grant_at ≈ 86399s`，已延效 `≈ 604799s`）。所以成功判据是积分账本里的延效档位，不是「今天有没有入账」。

链路：

```
[可选] POST /api/web/auth/v1/refresh              refresh_token → access_token
       GET  /api/web/office/v3/setting_info        桌面客户端启动动作（主触发，带桌面客户端头）
[兜底] POST /api/web/desktop/v1/login/points/grant 幂等发放（仅账本未见当日记录时补发）
       GET  /api/web/points/v1/bills               核对 daily_grant 的延效档位
```

接口形态参考了 [clzwqoii/aipunch](https://github.com/clzwqoii/aipunch) 的 `providers/raccoon.ts` 与 [dsh-multi-agent-gateway](https://github.com/a1141171521/dsh-multi-agent-gateway) 的 `docs/RACCOON-API.md`（两个独立来源交叉确认）。

---

## 部署（约 5 分钟）

### 1. 建私有仓库并推送

```bash
gh repo create caiping527/raccoon-checkin --private --source=. --push
```

> 建议 **Private**：公开仓库 60 天无活动会被 GitHub 停用定时任务；私有仓库也不暴露账号数量。
> 若仓库名不同，给本机工具加 `--repo <owner>/<repo>` 或设 `RACCOON_REPO` 环境变量。

### 2. 配置仓库级 Secret（Settings → Secrets and variables → Actions）

| 名称 | 必填 | 说明 |
|---|:---:|---|
| `WECOM_APP` | 二选一 | 企业微信自建应用：`corpid,corpsecret,touser,agentid` |
| `WECOM_WEBHOOK` | 二选一 | 企业微信群机器人完整 webhook（或仅 key） |
| `GH_PAT` | 推荐 | 仅授权本仓库 **Secrets 读写** 的细粒度令牌，用于云端回写轮换后的令牌。不配也能跑，轮换后需手动跑一键更新 |
| `RACCOON_ACCESS_TOKEN` | ❌ | **不要**在仓库级创建，每个账号放自己的 Environment 里 |

仓库变量（Variables，非 Secret）：

| 名称 | 说明 |
|---|---|
| `RACCOON_ACCOUNTS` | 尾号列表，由 `一键更新.bat` 自动维护，**无需手动建** |
| `SUCCESS_NOTIFY` | 可选，设 `1` 则签到成功也推送；默认成功不打扰 |

### 3. 本机设置 GitHub 令牌

```powershell
# 永久写入用户环境变量后重开终端
setx GH_TOKEN "你的GitHub令牌"
```

你现有的通用长期令牌可用；更安全的做法是新建一枚 **Fine-grained token**：Repository access 只勾本仓库，Permissions 只把 **Secrets** 设为 Read and write、**Actions** 设为 Read and write。

### 4. 登录小浣熊 → 双击 `tools\一键更新.bat`

工具自动识别账号尾号 → 建 Environment → 写 Secret → 登记账号 → 触发只读自检。
再加账号：退出 → 登录另一个号 → 再双击一次，无需改任何代码。

### 5. 启用 Actions 并手动验证

Actions 页启用 workflow → `Run workflow`（可勾「仅自检」先做只读验证）→ 看到 ✅ 即部署完成。

---

## 日常运行

- 每天北京时间 **09:17** 自动执行（GitHub 定时为 best-effort，可能延迟数分钟到数十分钟）。
- 每个账号一个独立 job，`fail-fast: false`，一个账号失败不影响其他账号。
- **成功不打扰**；只有签到失败、或令牌剩余 ≤7 天时推企业微信，消息带账号尾号与处理建议。
- 令牌实测有效期 **30 天**（JWT `exp`）。收到「令牌临期」提醒后，登录该账号双击一次 `一键更新.bat` 即可。
- 若配置了 `RACCOON_REFRESH_TOKEN`（可选），云端会先用它换 access_token，并把轮换后的新值经 `GH_PAT` 自动回写，可进一步延长免维护周期。

## 目录

```
.github/workflows/checkin.yml   云端定时 + 动态多账号 + 企微通知 + 令牌回写
scripts/raccoon_checkin.py      签到核心（零依赖）
scripts/wecom_notify.py         企业微信推送策略
scripts/summarize_result.py     脱敏运行摘要
tools/update.py                 本机一键更新
tools/一键更新.bat              双击入口
tools/使用说明.txt              用户手册
tests/                          标准库单元测试
```

## 验证

```bash
python -m unittest discover -s tests -v
python scripts/raccoon_checkin.py --diagnose   # 需配置 RACCOON_ACCESS_TOKEN，只读自检
```

## 已知限制与风险

1. **会话共享**：云端令牌与桌面客户端共享同一条会话；客户端重新登录 / 刷新令牌会让云端令牌失效，出现 401 时重新双击一键更新即可。想要完全隔离，需走官方授权码流程为云端单独签发会话。
2. **接口为逆向 Web API**：上游随时可能变更或加风控；签到接口本身有设备指纹识别的报道，主号请谨慎。
3. **合规**：平台服务协议通常限制自动化脚本调用，仅用于自己的账号，风险自负。
4. **GitHub Actions**：公开仓库 60 天无活动会停用 schedule（偶尔手动触发保活）；私有仓库免费额度足够每天 1 次 × 多账号。
5. 本机 `auth.json` 只有 `access_token`（无 `refresh_token`），因此默认路径是「30 天登录一次 + 提前 7 天提醒」，不是永久免维护。
