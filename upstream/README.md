# Grok Register Win（本地版）

Windows 双击运行的 Grok 注册机面板：

- **代理**：使用你本机已安装的 Clash（不内置 mihomo、不内置订阅）
- **自动探测代理端口**（常见 `7897` / `7890` 等，Clash Verge 多为 7897）
- **注册**：浏览器自动化注册
- **邮箱**：面板可选本地域名 Worker / Tempmailer / 自定义自建 API
- **自动转 CPA**：注册成功后后台把 web SSO 换成 CLIProxyAPI 可用的 OAuth JSON
- **下载**：
  - SSO TXT：`email----password----sso`
  - CPA ZIP：`xai-*.json`（`auth_kind=oauth`）
- **账号文件**：可勾选删除，避免重复下载

> 仅供学习研究。自动化注册可能违反平台条款，风险自负。

---

## 环境要求

1. **Windows 10/11**
2. **Python 3.10+**（安装时勾选 Add python.exe to PATH）
3. **本机 Clash**（Clash Verge / CFW / mihomo 客户端均可）
   - Clash Verge 默认 mixed 端口常见为 **7897**（不一定是 7890）
   - 订阅、节点切换请在 **Clash 客户端** 里完成
4. **Chrome 或 Edge**

---

## 快速开始

1. 下载本仓库 ZIP 并解压  
2. **先打开 Clash**，更新订阅，选一个能用的节点  
3. 双击 **`start.bat`**（推荐；`启动.bat` 也会转调它）  
   - 首次会自动创建 `.venv` 并安装依赖，窗口会保留，不要关  
   - 若失败，看 `data\logs\start.log`  
4. 浏览器自动打开：http://127.0.0.1:8787（**无需登录密码**，直接进面板）  
5. 在「邮箱设置」里选择本地域名邮箱、Tempmailer 或自定义 API
6. 点 **开始注册**  
7. 下载 SSO / CPA；不需要的账号文件可勾选 **删除选中**

注册批次中的浏览器任务全部结束后，面板会自动将本批次成功账号转换为 CPA JSON，并等待转换完成后再把任务标记为结束。「重新扫描 CPA」只用于恢复历史遗漏或重试失败记录，正常注册不需要手动点击。

> 若双击窗口一闪就没：请用 `start.bat`，并确认已装 Python 3.10+。

---

## 配置

首次运行会从 `config.example.json` 生成 `config.json`。

常用字段：

```json
{
  "proxy": "http://127.0.0.1:7897",
  "allow_proxy_fallback": false,
  "email_provider": "local_worker",
  "email_failover": true,
  "register_count": 1
}
```

- `allow_proxy_fallback`：代理失败是否回退直连（默认 `false`，建议保持关闭）  
- 启动时若配置的端口不通，会自动探测本机常见 Clash 端口并写回 `config.json`

### 邮箱

| 选项 | 说明 |
|------|------|
| 本地域名邮箱 | 对接 `/api/mailboxes`、`/admin/all`、`/admin/msg` Worker API |
| Tempmailer | 内置免 key（默认 `bluenode.cc`） |
| 自定义 | 自建临时邮 API（兼容 cloudflare_temp_email）：API 根地址 / Key / 域名 / 路径 |

> **InboxKitten 已移除**：`inboxkitten.com` 域名会被 xAI 直接拒绝注册，请使用 Tempmailer 或自有域名。

自定义需服务支持「创建地址」和「收信读验证码」。

可选环境变量（高级）：

| 变量 | 含义 | 默认 |
|------|------|------|
| `PANEL_AUTH` | 是否开启登录（`1` 开启） | `0`（关闭，免密） |
| `PANEL_PASSWORD` | 登录密码（仅 `PANEL_AUTH=1` 时有效） | `admin` |
| `PANEL_PORT` | 面板端口 | `8787` |
| `GROK_PROXY` | 覆盖代理 | 读 config.json |

面板默认只监听 `127.0.0.1`，且**默认不需要密码**。若要加密码：

```powershell
$env:PANEL_AUTH="1"
$env:PANEL_PASSWORD="你的密码"
.\start.bat
```

面板会为 Flask session 自动生成 `data/.panel_secret`，敏感邮箱 Token 只写入被 Git 忽略的 `config.json`，不会返回到浏览器。非本机监听地址必须开启 `PANEL_AUTH=1`。

---

## 目录结构

```
grok-register-win/
  start.bat / 启动.bat  # 双击启动
  setup.bat             # 安装运行依赖；加 --dev 安装测试依赖
  setup-dev.bat         # 开发环境快捷安装
  test.bat              # 运行 Python 测试
  launcher.py           # 启动器（代理探测）
  grok_register_ttk.py  # 浏览器注册流程与 GUI/CLI 适配层
  config.example.json
  panel/app.py          # Flask 路由与依赖装配
  panel/account_store.py
  panel/cpa_service.py
  panel/email_settings.py
  panel/job_manager.py
  panel/panel_log.py
  lib/registration_runner.py
  lib/browser_proxy.py
  lib/process_control.py
  lib/config_store.py
  lib/grok2api_pool.py
  lib/sso2cpa_core.py   # SSO → CPA
  tools/                # 批处理与诊断命令
  scripts/              # Windows 自动启动维护脚本
  data/accounts/        # 注册账号产出
  data/logs/            # 运行日志
  data/cpa/             # 已转换 CPA JSON
```

注册流程、CPA 队列、面板任务、浏览器代理、配置持久化和账号文件已经拆分为独立模块。模块职责与调用关系见 [`docs/architecture.md`](docs/architecture.md)，目录放置规则见 [`docs/directory-layout.md`](docs/directory-layout.md)。

---

## 开发与测试

安装开发依赖：

```bat
setup-dev.bat
```

运行测试：

```bat
test.bat
```

也可以直接执行：

```powershell
.venv\Scripts\python.exe -m pytest -q
node --test workers/temp-email-worker/test/otp.test.js
```

需要真实 CPA 凭证的手动诊断命令位于 `tools/diagnostics/`，不会被 pytest 收集。脚本默认读取 `data/cpa` 中最新的 `xai-*.json`，也可显式传入：

```powershell
.venv\Scripts\python.exe tools\diagnostics\cpa_token_probe.py --cpa-file data\cpa\xai-example.json
.venv\Scripts\python.exe tools\diagnostics\cpa_endpoint_probe.py --cpa-file data\cpa\xai-example.json
```

批量转换历史账号：

```powershell
.venv\Scripts\python.exe tools\convert_batch.py
```

诊断输出会隐藏 OAuth Token、SSO、Cookie 和密码。更多开发约定见 [`docs/development.md`](docs/development.md)。

---

## 开机自动启动

以当前 Windows 用户登录时自动启动面板，并立即启动一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1 -StartNow
```

自动启动使用 Windows 任务计划程序，任务名为 `GrokRegisterWin`。进程在后台运行，不弹出控制台，也不会自动打开浏览器；面板仍可通过 http://127.0.0.1:8787 访问。日志位于 `data\logs\autostart.log` 和 `data\logs\panel_boot.log`。

取消自动启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-autostart.ps1
```

---

## 常见问题

### 代理端口不通 / WinError 10061
- 先开 Clash  
- Clash Verge 常见端口 **7897**，不是 7890  
- 或改 `config.json` 的 `proxy`，重启 `start.bat`（也会自动探测）

### 卡在 Cookie / 拿不到 SSO
- 已会尝试自动点「接受所有 Cookie」  
- 仍失败时换节点重试  

### 依赖安装失败
```bat
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## License

MIT（若上游组件另有协议，以对应文件为准）
