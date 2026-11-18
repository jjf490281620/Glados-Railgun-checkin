# GLaDOS 多账号浏览器签到

本扩展基于上游 v1.0.1 改造，在本机 Chrome / Edge 的官网页面完成签到。适用于 GitHub 运行器被 Cloudflare / Turnstile 拒绝的账号。

本版为 v1.1.0：按邮箱绑定每个账号，支持导入 GitHub Secrets 使用的 JSON 格式，并移除了积分兑换功能。每个账号使用独立临时标签页，Cookie 请求头只对配置的官网域名生效，签到前核验真实邮箱。

## 安装与导入

1. 打开 `chrome://extensions` 或 `edge://extensions`，启用开发者模式。
2. 选择“加载已解压的扩展程序”，选中本仓库的 `extension/` 目录。
3. 打开扩展设置，点击“导入账号 JSON”，选择账号配置文件。结构与 GitHub `GLADOS_COOKIES` Secret 相同：

```json
[
  {
    "email": "account@example.com",
    "domain": "glados.cloud",
    "cookie": "gld:sess=YOUR_SESSION; gld:sess.sig=YOUR_SIGNATURE;"
  }
]
```

新增账号时会保留原有账号；同一邮箱、同一域名的配置会更新 Cookie。配置邮箱不一致时，停止该账号并显示原因，其他账号继续处理。

## 自动运行

默认每天本机时间 12:00–12:30 运行一次，可在设置中调整。北京时间环境下，通常早于仓库 12:31 的 GitHub 定时任务；GitHub 仍会按邮箱汇总“签到成功”“今日已签到”或实际错误。

Chrome 必须保持运行，电脑关机或浏览器退出时扩展不会执行。签到时会短暂打开官网标签页，让官网验证组件在正常浏览器中运行；遇到需要人工操作的验证时，需要在该页面完成验证。

“立即签到全部”可手动验证五个账号。账号卡片和签到日历分别展示真实邮箱、签到结果、当天积分、总积分及剩余天数。

## 数据

账号配置和 Cookie 保存在本机浏览器扩展存储中。真实 Cookie 不包含在仓库或扩展发布包中。默认不启用桌面通知，也未配置远程推送。

## 检查代码

```bash
node extension/test-classify.js
node extension/test-accounts.js
```

测试覆盖签到响应判定、账号导入、严格邮箱核验、Cookie 的域名和标签页隔离，以及兑换接口移除。
