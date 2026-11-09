# GitHubFix 设计说明

## 目标

在不可靠的跨境网络下，尽量提高 GitHub 的可达性和连接稳定性，同时保证：

1. 不破坏用户原有 hosts / 系统代理 / Git 配置；
2. 不安装 TLS 根证书、不解密用户流量；
3. 不依赖单一 IP、单一 DNS 或单一第三方镜像；
4. 任何自动修改都能恢复。

## 分层策略

### 第一层：DNS 与 hosts

hosts 是最轻量的方案，但只能解决“域名解析到不可达 IP”的问题，不能解决路由丢包或 SNI 封锁。因此：

- 多 DNS 来源投票；
- 每个候选做真实 TLS 证书校验；
- 二次抽样筛选稳定性；
- 写入后验证，失败自动回滚。

### 第二层：本地智能连接器

hosts 固定一个 IP。如果该 IP 在会话中途变差，浏览器只能等超时。本地连接器在每次 CONNECT 时：

1. 从短期缓存中取排序后的候选 IP；
2. 并发连接前 4 个；
3. 第一个 TCP 成功者胜出；
4. 失败则刷新候选再试一次；
5. 透明转发 TLS 字节流。

这不会绕过 TLS，也不会让代理看到明文。它只改善“选哪个 IP、失败多快切换”。

### 第三层：Git 专用路径

- HTTPS：通过 http.https://github.com.proxy 指向本地连接器；
- SSH：github-443 别名指向 ssh.github.com:443，避免 22 端口；
- 若检测到第三方 url.*.insteadOf，显式提示并允许清理，因为 rewrite 会绕过前两层。

### 第四层：公共镜像

仅作为公开内容下载的兜底。第三方镜像可能记录 URL、限速、注入内容，因此：

- 不自动全局 rewrite；
- 只生成单次 clone 命令；
- UI 明确警告不要传输凭据。

## 数据与恢复

- 数据目录：%LOCALAPPDATA%\GitHubFix
  - backups/hosts-*.bak
  - backups/ssh-config-*.bak
  - backups/gitconfig-*.bak
  - reports/*.md
  - system-proxy-backup.json
  - proxy-runtime-state.json

启动时若发现 proxy-runtime-state.json 中 active=true 且本地端口没有监听，则自动恢复系统代理和 Git 代理。PAC 中始终带 DIRECT 兜底，避免程序崩溃后浏览器完全断网。

## 失败模式

| 场景 | 行为 |
|---|---|
| 所有 IP 都失败 | 不写 hosts；建议 SSH 443 或合法代理 |
| 写入后验证失败 | 自动复制备份回 hosts |
| hosts 文件只读/被杀软锁定 | 报错，不尝试绕过 |
| 系统 PAC 设置失败 | 停止本地代理，不改 hosts |
| 程序崩溃 | PAC 的 DIRECT 兜底；下次启动恢复 |
| 第三方镜像证书错误 | 标记不可用，不忽略证书 |

## 非目标

- 不实现任何形式的 TLS 中间人；
- 不提供账号、Cookie、Token；
- 不承诺突破网络封锁；
- 不自动修改用户未确认的第三方 Git 改写。
