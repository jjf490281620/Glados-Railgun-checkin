# GLaDOS / Railgun 多账号自动签到

通过 GitHub Actions 每天自动签到多个账号，并按邮箱显示每个账号的签到结果、剩余天数和积分。

脚本先读取 Cookie 对应的真实邮箱，再执行签到。配置了邮箱时会核对是否一致；某个账号 Cookie 过期、邮箱不匹配或签到失败后，仍会继续处理其他账号。支持 `glados.cloud` 和 `railgun.info`。

## 网站验证限制

新版 GLaDOS 签到接口会先下发一次性校验请求，并可能要求 **Cloudflare Turnstile 网页验证**。Cookie 能读取账号信息，不代表网站允许当前请求直接签到。

脚本支持无需网页验证的新版请求，以及没有校验接口的旧版请求。网站要求 Turnstile 或登录设备验证时，该账号显示“需要网页验证”，保留已核对的邮箱、剩余天数和积分，工作流返回失败状态并继续处理其他账号。GitHub 的纯接口任务无法完成网页验证码。

是否要求网页验证由网站决定；重新登录或更新 Cookie 后仍可能需要验证。因此，在受此限制的账号上，不能保证仅靠 GitHub Actions 和 Cookie 实现无人值守签到。

## 部署

1. Fork 本仓库，在自己的仓库中启用 **Actions**。
2. 打开 **Settings → Secrets and variables → Actions**，创建或更新 **`GLADOS_COOKIES`** Repository Secret。
3. 按下方示例填写 JSON，绑定每个账号的邮箱、域名和 Cookie。
4. 在 **Actions → GLaDOS multi-account check-in → Run workflow** 手动运行一次。
5. 打开运行的 **Summary**，确认各账号邮箱正确，结果显示“签到成功”或“今日已签到”。

### 按邮箱绑定多个账号

`GLADOS_COOKIES` 的内容是一个 JSON 数组，每个对象对应一个账号：

```json
[
  {
    "email": "account1@example.com",
    "name": "主账号",
    "domain": "glados.cloud",
    "cookie": "gld:accts=ACCOUNT_1_TOKEN; gld:accts.sig=ACCOUNT_1_ACCOUNTS_SIGNATURE; gld:sess=ACCOUNT_1_TOKEN; gld:sess.sig=ACCOUNT_1_SESSION_SIGNATURE;"
  },
  {
    "email": "account2@example.com",
    "name": "备用账号",
    "domain": "railgun.info",
    "cookie": "gld:accts=ACCOUNT_2_TOKEN; gld:accts.sig=ACCOUNT_2_ACCOUNTS_SIGNATURE; gld:sess=ACCOUNT_2_TOKEN; gld:sess.sig=ACCOUNT_2_SESSION_SIGNATURE;"
  }
]
```

将示例邮箱和 Cookie 替换为真实信息。新增账号时，在数组中增加一个对象；更新某个账号的 Cookie 时，替换该对象的 `cookie` 字段。

| 字段 | 说明 |
| --- | --- |
| `cookie` | 必填，该账号的完整 Cookie 请求头值 |
| `email` | 建议填写，用于绑定账号；签到前必须与接口返回的邮箱一致 |
| `name` | 可选别名；默认使用配置邮箱，没有配置邮箱时按顺序编号 |
| `domain` | `glados.cloud`、`railgun.info` 或 `auto`；默认 `auto` |

未配置 `email` 时，脚本仍会读取并显示该 Cookie 对应的真实邮箱。如果接口未返回有效邮箱，则停止处理该账号并报错。

JSON 也可以存放在 **`GLADOS_ACCOUNTS`** Secret 中；同时设置两个 Secret 时，优先使用 `GLADOS_ACCOUNTS`。

### 兼容旧的 Cookie 配置

已有的 `GLADOS_COOKIES` 可以继续使用每行一个 Cookie 的格式，或原来的 `&` 分隔格式：

```text
gld:accts=ACCOUNT_1_TOKEN; gld:accts.sig=ACCOUNT_1_ACCOUNTS_SIGNATURE; gld:sess=ACCOUNT_1_TOKEN; gld:sess.sig=ACCOUNT_1_SESSION_SIGNATURE;
gld:accts=ACCOUNT_2_TOKEN; gld:accts.sig=ACCOUNT_2_ACCOUNTS_SIGNATURE; gld:sess=ACCOUNT_2_TOKEN; gld:sess.sig=ACCOUNT_2_SESSION_SIGNATURE;
```

这两种格式会自动查询真实邮箱。需要固定邮箱绑定、指定域名，或 Cookie 的值中包含 `&` 时，使用 JSON 格式。

### 获取 Cookie

分别登录每个账号，在实际使用的 GLaDOS / Railgun 页面打开浏览器开发者工具：

1. 切换到 **Network**，刷新签到页面。
2. 选择发往 `/api/user/status` 或 `/api/user/checkin` 的请求。
3. 在 **Request Headers** 中复制 **Cookie** 的完整值。
4. 将 Cookie 填入对应邮箱的 JSON 对象，`domain` 填这个请求所属的域名。

新版 Cookie 包含 `gld:accts`、`gld:accts.sig`、`gld:sess`、`gld:sess.sig` 等字段，旧版 `koa:sess` / `koa:sess.sig` 格式也兼容。请保留完整 Cookie 中的账户和会话签名；带上 `Cookie:` 前缀也可识别。

真实 Cookie 仅保存在 GitHub Secrets 中，不要写进代码、README、Issue 或提交记录。GitHub 保存 Secret 后无法读取原值；更新时需重新填写完整内容。

## 定时和运行结果

- 定时计划：**北京时间每天 12:31、15:31**（UTC `31 4,7 * * *`）。GitHub 调度可能延迟。
- 手动运行和向 `master` 推送代码会触发签到；仅修改 README 或图片不会触发。
- Pull Request 只运行测试，不读取账号 Secret，也不调用签到接口。
- 同一分支的签到任务排队执行，避免并发处理同一批账号。
- 默认 `auto` 模式逐个尝试域名；某个域名签到成功或确认今日已签到后立即停止，一个账号只产生一条汇总结果。
- 临时网络错误、HTTP 429 / 5xx 会有限重试；HTTP 跳转不会自动携带 Cookie 跟随。
- 新版请求会先获取一次性校验信息；签名请求只提交一次，网络超时后可重新运行工作流获取新的校验信息。
- 运行时会对 Cookie 以及 `gld:*` / `koa:*` 会话、账户令牌和签名添加日志遮罩。
- 使用 GitHub 自带的 `GITHUB_TOKEN` 重新启用当前工作流，防止因仓库长期没有提交导致定时任务被停用；无需额外 PAT。
- 不自动删除运行日志，便于检查历史结果。

运行摘要包含**账号名称、真实邮箱、域名、签到结果、本次积分、剩余天数、总积分、说明**。Cookie 失效或邮箱不匹配时，该行显示配置邮箱并注明失败原因。

| 脚本退出码 | 含义 |
| --- | --- |
| `0` | 所有账号签到成功，或已完成当天签到 |
| `1` | 至少一个账号签到失败或需要网页验证，其他账号已继续处理 |
| `2` | 账号 Secret 缺失或配置格式错误 |

确认签到成功后的余额查询或可选推送失败，不会改变已确认的签到结果。

## 可选配置

| Repository Secret | 默认值 | 说明 |
| --- | --- | --- |
| `GLADOS_VERBOSE` | `false` | 设置为 `true` 输出接口的签到消息，仍对凭据脱敏 |
| `PUSHDEER_SENDKEY` | 空 | PushDeer 推送密钥；不配置也可正常签到 |

## 本地运行和验证

需要 Python 3.10 或以上；GitHub Actions 使用 Python 3.13。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

在本地安全地设置 `GLADOS_COOKIES` 或 `GLADOS_ACCOUNTS` 环境变量后，运行：

```bash
python checkin.py
```

测试使用模拟接口，不需要真实 Cookie，不会执行真实签到或推送。

## 排查问题

- **“没有权限” / “No permission”**：Cookie 未通过认证。重新登录对应账号，复制新的 Cookie 并更新 Secret；检查配置域名是否与登录站点一致。
- **“需要网页验证” / “Automated check-in detected”**：网站要求 Turnstile 或登录设备验证。在对应账号的官网签到页检查；纯 GitHub 接口任务无法代替网页验证。
- **“邮箱不一致”**：Cookie 与配置邮箱属于不同账号；核对该对象的 `email` 和 `cookie`。脚本会在提交签到前拒绝这一账号。
- **“未返回有效邮箱”**：无法确定当前 Cookie 对应的账号，检查 Cookie 是否完整或网站接口是否变更。
- **“未配置账号”**：检查 Secret 名称与拼写，确认配置在自己的仓库的 Repository Secrets 中。
- **JSON 配置错误**：使用双引号，检查逗号；Cookie 内部的分号保留在字符串中。
- **某个账号失败**：查看摘要中的邮箱和说明，更新该账号；其他账号仍会执行。
- **HTTP 429、5xx、非 JSON 响应**：可能是限流、网站维护或访问验证，稍后手动重试。
- **“今日已签到”**：当天无需重复签到，属于正常结果；真正失败时工作流会返回失败状态。

本项目基于 [Devilstore/Glados-Railgun-checkin](https://github.com/Devilstore/Glados-Railgun-checkin) 修改，保留原仓库许可证。
