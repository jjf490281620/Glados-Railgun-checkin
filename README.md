# GLaDOS / Railgun 多账号自动签到

通过 GitHub Actions 每天自动签到多个账号。每个账号独立运行，某个 Cookie 失效后仍会继续处理其他账号；运行摘要会列出每个账号的结果、剩余天数和积分。

支持 `glados.cloud` 和 `railgun.info`。默认逐个尝试域名，某个域名签到成功或确认今日已签到后立即停止，一个账号只产生一条汇总结果。

## 部署

1. Fork 本仓库，在自己的仓库中启用 **Actions**。
2. 打开 **Settings → Secrets and variables → Actions**，配置下面任意一种账号 Secret。
3. 在 **Actions → GLaDOS multi-account check-in → Run workflow** 手动运行一次。
4. 打开运行的 **Summary**，确认每个账号显示“签到成功”或“今日已签到”。

已有的 `GLADOS_COOKIES` Secret 可以继续使用，无需为了升级改名。

### 方式一：每行一个账号

创建或更新 Repository Secret **`GLADOS_COOKIES`**。每行填写一个账号的完整 Cookie：

```text
koa:sess=ACCOUNT_1_SESSION; koa:sess.sig=ACCOUNT_1_SIGNATURE;
koa:sess=ACCOUNT_2_SESSION; koa:sess.sig=ACCOUNT_2_SIGNATURE;
koa:sess=ACCOUNT_3_SESSION; koa:sess.sig=ACCOUNT_3_SIGNATURE;
```

将示例内容替换为真实 Cookie。账号按顺序显示为“账号 1”“账号 2”“账号 3”。原来用 `&` 连接多个 Cookie 的格式也兼容。

如果 Cookie 的值本身包含 `&`，请使用下面的 JSON 格式，避免被当成多个账号拆开。

### 方式二：给每个账号设置名称和域名

创建 Repository Secret **`GLADOS_ACCOUNTS`**，填写 JSON 数组：

```json
[
  {
    "name": "主账号",
    "domain": "glados.cloud",
    "cookie": "koa:sess=ACCOUNT_1_SESSION; koa:sess.sig=ACCOUNT_1_SIGNATURE;"
  },
  {
    "name": "备用账号",
    "domain": "railgun.info",
    "cookie": "koa:sess=ACCOUNT_2_SESSION; koa:sess.sig=ACCOUNT_2_SIGNATURE;"
  },
  {
    "name": "第三个账号",
    "domain": "auto",
    "cookie": "koa:sess=ACCOUNT_3_SESSION; koa:sess.sig=ACCOUNT_3_SIGNATURE;",
    "exchange_plan": "none"
  }
]
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `cookie` | 是 | 该账号的完整 Cookie 请求头值 |
| `name` | 否 | 用于日志和摘要的名称，建议使用别名；默认按顺序编号 |
| `domain` | 否 | `glados.cloud`、`railgun.info` 或 `auto`；默认 `auto` |
| `exchange_plan` | 否 | `none`、`plan100`、`plan200`、`plan500`；默认使用全局配置 |

**同时配置两个 Secret 时，优先使用 `GLADOS_ACCOUNTS`。** JSON 数组也可以直接放在 `GLADOS_COOKIES` 中；数组元素可以是 Cookie 字符串或上述账号对象。

### 获取 Cookie

分别登录每个账号，在实际使用的 GLaDOS / Railgun 页面打开浏览器开发者工具：

1. 切换到 **Network**，刷新签到页面。
2. 选择发往 `/api/user/status` 或 `/api/user/checkin` 的请求。
3. 在 **Request Headers** 中复制 **Cookie** 的完整值。
4. 将每个账号的 Cookie 填入 GitHub Secret。使用 JSON 时，`domain` 填这个请求所属的域名。

Cookie 应包含有效的 `koa:sess` 和 `koa:sess.sig` 等登录字段。复制时带上 `Cookie:` 前缀也可识别。不要将真实 Cookie 写进代码、README、Issue 或提交记录。

GitHub 保存 Secret 后无法再读取原值；更新账号时需要重新填写对应 Secret 的完整内容。

## 定时和运行结果

- 定时计划：**北京时间每天 12:31、15:31**（UTC `31 4,7 * * *`）。GitHub 调度可能延迟。
- 手动运行和向 `master` 推送代码也会触发签到；仅修改 README 或图片不会触发。
- Pull Request 只运行测试，不读取账号 Secret，也不调用签到接口。
- 同一分支的签到任务排队执行，避免并发处理同一批账号。
- 临时网络错误、HTTP 429 / 5xx 会有限重试；HTTP 跳转不会自动携带 Cookie 跟随。
- 运行时会对每个 Cookie 和会话值添加日志遮罩，摘要不输出请求头或完整接口响应。
- 使用 GitHub 自带的 `GITHUB_TOKEN` 重新启用当前工作流，防止因仓库长期没有提交导致定时任务被停用；无需额外 PAT。
- 不自动删除运行日志，便于检查历史签到结果。

| 脚本退出码 | 含义 |
| --- | --- |
| `0` | 所有账号签到成功，或已完成当天签到 |
| `1` | 至少一个账号签到失败，其他账号已继续处理 |
| `2` | 账号 Secret 缺失或配置格式错误 |

查询余额或可选推送失败不会改变已确认的签到结果。可选兑换结果单独展示在摘要中。

## 可选配置

| Repository Secret | 默认值 | 说明 |
| --- | --- | --- |
| `GLADOS_EXCHANGE_PLAN` | `none` | 全局积分兑换策略；可由账号的 `exchange_plan` 覆盖 |
| `GLADOS_VERBOSE` | `false` | 设置为 `true` 输出接口的签到消息，仍对凭据脱敏 |
| `PUSHDEER_SENDKEY` | 空 | PushDeer 推送密钥；不配置也可正常签到 |

| 兑换计划 | 所需积分 | 对应天数 |
| --- | --- | --- |
| `plan100` | 100 | 10 天 |
| `plan200` | 200 | 30 天 |
| `plan500` | 500 | 100 天 |

自动兑换默认关闭。只有**本次签到成功且已确认积分足够**才会兑换；今日已经签到时跳过兑换。兑换请求不自动重试，避免网络超时后重复消耗积分。兑换规则以网站实际设置为准。

## 本地运行和验证

需要 Python 3.10 或以上；GitHub Actions 使用 Python 3.13。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

在本地安全地设置 `GLADOS_ACCOUNTS` 或 `GLADOS_COOKIES` 环境变量后，运行：

```bash
python checkin.py
```

测试使用模拟接口，不需要真实 Cookie，不会执行真实签到或推送。

## 排查问题

- **“没有权限” / “No permission”**：Cookie 未通过认证。重新登录对应账号，复制新的 Cookie 并更新 Secret；同时检查 JSON 中的域名是否与登录站点一致。
- **“未配置账号”**：检查 Secret 名称与拼写，确认配置在自己的仓库的 Repository Secrets 中。
- **JSON 配置错误**：使用双引号，检查逗号；Cookie 内部的分号保留在字符串中。
- **某个账号失败**：查看运行摘要的账号名称和说明，更新该账号；其他账号仍会执行。
- **HTTP 429、5xx、非 JSON 响应**：可能是限流、网站维护或访问验证，稍后手动重试。
- **Actions 绿色但账号没有新增积分**：先看摘要，“今日已签到”是正常结果；新的脚本会在真正签到失败时返回失败状态。

本项目基于 [Devilstore/Glados-Railgun-checkin](https://github.com/Devilstore/Glados-Railgun-checkin) 修改，保留原仓库许可证。
