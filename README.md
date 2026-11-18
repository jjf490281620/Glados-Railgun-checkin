# GLaDOS / Railgun 多账号自动签到

通过 GitHub Actions 每天自动签到多个账号，并按邮箱显示每个账号的签到结果、剩余天数和积分。

脚本先读取 Cookie 对应的真实邮箱，再执行签到。配置了邮箱时会核对是否一致；某个账号 Cookie 过期、邮箱不匹配或签到失败后，仍会继续处理其他账号。支持 `glados.cloud` 和 `railgun.info`。

## 网站验证限制

新版 GLaDOS 签到接口会先下发一次性校验请求，并可能要求 **Cloudflare Turnstile 网页验证**。本仓库已对齐上游 v1.0.1 的接口提交方式：获取 nonce、等待 2.5 秒，再提交 JSON 格式的 nonce 和 proof，由服务端实际响应判定签到结果。校验接口返回公共 Turnstile site key 时仍会尝试正常接口请求，不伪造网页验证令牌或验证状态。

服务端返回设备不匹配时，未配置 `user-agent` 的账号会按服务端返回的登录平台自动调整一次，并重新获取 nonce 后重试。这样可以处理 Cookie 来自 macOS、脚本默认使用 Windows 标识的情况。账号仍不匹配时，填写复制 Cookie 时浏览器的完整 User-Agent。

GitHub Actions 已启用网页签到后备流程：接口返回 Turnstile 拒绝时，为该账号创建独立 Chromium 会话，重新核验邮箱，打开官网签到页，再点击官网的签到按钮。官网自行加载验证组件并提交请求，脚本读取真实响应；每个账号的 Cookie 和浏览器会话互相隔离。运行摘要中的“方式”列显示“接口”或“网页”。

如果官网组件仍要求人工验证，该账号保留“需要网页验证”状态，工作流返回失败并继续处理其他账号。浏览器流程不伪造验证令牌、不破解验证码。GitHub 运行器能否自动通过验证由网站决定；需要人工交互时，可在正常浏览器中检查该账号，或使用上游的[浏览器扩展](https://github.com/Devilstore/Glados-Railgun-checkin/tree/master/extension)。

本仓库已提供适配好的[本机浏览器扩展](extension/README.md)：可导入同一份账号 JSON，签到前严格核验配置邮箱，并移除了兑换功能。GitHub 浏览器环境无法通过验证时，可由本机正常浏览器完成每日签到，再由 GitHub 定时查询各账号的结果。扩展需要浏览器保持运行。

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
| `user-agent` | 可选，复制 Cookie 时浏览器的完整 User-Agent；未填写时支持一次登录平台自动恢复 |

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
- 设备不匹配时最多按登录平台恢复一次，每次获取新的校验信息；填写了 `user-agent` 时始终使用该值。
- 接口返回 Turnstile 拒绝时，自动尝试官网网页；只有服务端确认成功或今日已签到才视为完成。
- 运行时会对 Cookie 以及 `gld:*` / `koa:*` 会话、账户令牌和签名添加日志遮罩。
- 使用 GitHub 自带的 `GITHUB_TOKEN` 重新启用当前工作流，防止因仓库长期没有提交导致定时任务被停用；无需额外 PAT。
- 不自动删除运行日志，便于检查历史结果。

运行摘要包含**账号名称、真实邮箱、域名、签到方式、签到结果、本次积分、剩余天数、总积分、说明**。Cookie 失效或邮箱不匹配时，该行显示配置邮箱并注明失败原因。

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

本地需要启用网页后备流程时，先执行 `python -m playwright install chromium`，再设置 `GLADOS_BROWSER_FALLBACK=true`。默认使用无界面浏览器；需要可见窗口时设置 `GLADOS_BROWSER_HEADLESS=false`。GitHub Actions 使用 Xvfb 运行带界面的 Chromium，配置已包含在工作流中。

需要单独检查 GitHub 浏览器环境时，可手动运行 **Verify GLaDOS browser verification** 工作流，选择复制 Cookie 时的登录设备。该检查为每个账号打开独立会话，核验邮箱并观察官网验证组件是否完成验证，不提交签到或兑换；验证令牌不写入日志或摘要。

测试使用模拟接口，不需要真实 Cookie，不会执行真实签到或推送。

## 排查问题

- **“没有权限” / “No permission”**：Cookie 未通过认证。重新登录对应账号，复制新的 Cookie 并更新 Secret；检查配置域名是否与登录站点一致。
- **“需要网页验证” / “Cloudflare verification failed”**：查看“方式”列。显示“网页”时，官网组件仍未通过验证，需要在正常浏览器中检查该账号；显示“接口”时，检查网页后备流程是否启用。
- **“登录设备仍不匹配”**：在该账号 JSON 对象中补充 `user-agent`，填写获取 Cookie 时浏览器 Network → Request Headers 中的完整 User-Agent。
- **“邮箱不一致”**：Cookie 与配置邮箱属于不同账号；核对该对象的 `email` 和 `cookie`。脚本会在提交签到前拒绝这一账号。
- **“未返回有效邮箱”**：无法确定当前 Cookie 对应的账号，检查 Cookie 是否完整或网站接口是否变更。
- **“未配置账号”**：检查 Secret 名称与拼写，确认配置在自己的仓库的 Repository Secrets 中。
- **JSON 配置错误**：使用双引号，检查逗号；Cookie 内部的分号保留在字符串中。
- **某个账号失败**：查看摘要中的邮箱和说明，更新该账号；其他账号仍会执行。
- **HTTP 429、5xx、非 JSON 响应**：可能是限流、网站维护或访问验证，稍后手动重试。
- **“今日已签到”**：当天无需重复签到，属于正常结果；真正失败时工作流会返回失败状态。

本项目基于 [Devilstore/Glados-Railgun-checkin](https://github.com/Devilstore/Glados-Railgun-checkin) 修改，保留原仓库许可证。
