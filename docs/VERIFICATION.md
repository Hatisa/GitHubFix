# 验证记录

验证日期：2026-11-10（Asia/Shanghai）  
环境：Windows 10.0.26200，Python 3.11.5，Git 2.54.0，PyInstaller 6.16.0

## 1. 单元与回归测试

命令：

    $env:PYTHONPATH = "$PWD\src"
    python -m unittest discover -s tests -v

结果：

- 共 16 个测试，14 个通过，2 个真实网络测试默认跳过；
- 覆盖 hosts 块解析、旧版块替换、损坏/不平衡标记保护、备份、自动回滚、候选合并、PAC 规则、SSH 配置块、镜像 URL 转换。

## 2. 真实网络诊断

使用打包后的 `GitHubFix-CLI.exe diagnose --no-mirrors`：

- 核心域名可用：5/5；
- github.com：140.82.112.3，约 1184 ms，HTTP 200；
- api.github.com：20.27.177.116，约 164 ms，HTTP 200；
- raw.githubusercontent.com：185.199.110.133，约 626 ms，HTTP 200；
- 所有结果均经过 TLS 证书校验，不是仅 TCP 连通；
- 检测到本机旧版 GitHub Fix hosts 块和第三方 Git URL 改写。

样本报告见 [sample-report.md](sample-report.md)。

## 3. hosts 修复流程

在临时 hosts 副本上以模拟管理员权限执行完整 repair：

- 写入 10 条经过验证的记录；
- 替换 1 个旧版配置块；
- 端到端验证 5/5 通过；
- 备份文件生成；
- 恢复操作成功移除配置块，并保留原有用户条目。

未在当前会话中修改真实系统 hosts：当前进程为普通用户（UAC 过滤令牌），而 hosts 文件仅 Administrators/SYSTEM 可写。打包的 GUI 使用 `requireAdministrator` manifest，用户双击时由 Windows 提升后即可执行同样的写入逻辑。

## 4. 本地智能连接器

- 启动 `SmartProxy` 后，通过本地 HTTP CONNECT 访问 `https://github.com/` 返回 `HTTP/1.1 200 OK`；
- 通过 URL 专用 Git 代理执行 `git ls-remote https://github.com/521xueweihan/GitHub520.git` 成功返回 HEAD；
- PAC 规则用 Node 求值验证：
  - `https://github.com` → `PROXY 127.0.0.1:17890; DIRECT`
  - `https://api.github.com`、`raw.githubusercontent.com` → 走代理
  - `https://evilgithub.com`、`http://github.com`、普通网站 → `DIRECT`
- 停止后系统 `AutoConfigURL` 和 Git 专用代理均恢复原值，状态文件删除。

## 5. 打包

- `dist/GitHubFix.exe`：PyInstaller onefile/windowed，manifest 为 `requireAdministrator`；
- `dist/GitHubFix-CLI.exe`：PyInstaller onefile/console，manifest 为 `asInvoker`；
- 两个 EXE 均包含应用图标，GUI 版本在无 UAC 的测试构建中成功启动并显示窗口；
- 最终 EXE 的 SHA-256 见 `dist/SHA256SUMS.txt`。

## 6. 已知限制

- 当前网络对 GitHub 的丢包很严重，单次测速会有波动；程序通过二次抽样和自动回滚降低风险。
- 如果所有 GitHub IP 均不可达，hosts 和本地连接器都无法保证成功；需要使用合法代理或 SSH 443 等替代路径。
- 第三方镜像仅用于公开内容，程序不会自动将其写入全局 Git 配置。
