# 验证记录

验证日期：2026-11-11（Asia/Shanghai）  
环境：Windows 10.0.26200，Python 3.11.5，Git 2.54.0，PyInstaller 6.16.0

## 1. 单元与回归测试

命令：

    $env:PYTHONPATH = "$PWD\src"
    python -m unittest discover -s tests -v

结果：共 18 个测试，16 个通过，2 个真实网络测试默认跳过。

覆盖范围：

- hosts 块解析、旧版块替换、损坏/不平衡标记保护、备份与原子写入；
- 候选 IP 合并、PAC 规则、SSH 配置块往返、镜像 URL 转换；
- **1.1.0 新增**：非核心域名不可达时只剔除该条目而不回滚整块配置；
- **1.1.0 新增**：github.com 直连失败时自动切换到备选候选 IP；
- **1.1.0 新增**：所有候选都失败时回滚到**原始**备份（而不是二次写入后的中间状态）。

## 2. 真实网络诊断

使用打包后的 `GitHubFix-CLI.exe diagnose --no-mirrors`：

- 核心域名可用：5/5；
- github.com：20.27.177.113，约 159 ms，HTTP 200；
- api.github.com：20.27.177.116，约 180 ms，HTTP 200；
- raw.githubusercontent.com：185.199.110.133，约 811 ms，HTTP 200；
- 所有结果均经过 TLS 证书校验，不是仅 TCP 连通。

样本报告见 [sample-report.md](sample-report.md)。

## 3. hosts 修复流程

在临时 hosts 副本上以模拟管理员权限执行完整 repair：

- 写入 10 条记录；
- **直连验证 9/9 通过**（绕过 DNS，直接对写入的 IP 做 TCP + TLS + HTTP）；
- **DNS 验证 5/5 通过**；
- 备份文件生成，恢复操作可完整还原。

### 1.1.0 修复的真实故障

1.0.0 在实际网络上会反复出现「hosts 未写入（可用 IP 不足或验证失败）」。复现结果：

    verification {'api': ok, 'codeload': ok, 'assets': ok, 'github.com': {'ok': False, 'error': 'timed out'}}
    → 3/4 通过，但 github.com 失败 → 整块回滚

原因有三层：

1. 写入后使用**系统 DNS 解析**做验证，而 Windows DNS 缓存可能仍返回旧 IP（旧 IP 往往已不可达），导致验证必然失败；
2. 任何一个域名（哪怕是 avatars 这类非核心域名）失败都会触发**整块回滚**；
3. 回滚目标是二次写入后生成的备份，而不是原始文件。

1.1.0 的改动：

- 验证改为直连 IP（TCP + TLS SNI + HTTP），与 DNS 缓存解耦；
- github.com 是唯一硬性条件，失败时先依次换备选 IP；
- 其余不可达域名只从 hosts 中剔除，不影响已可用部分；
- 回滚固定使用首次写入前的原始备份。

同一网络下再跑一次（1.1.0）：

    applied True rolled_back False
    已写入 10 条记录，直连验证 9/9 通过，DNS 验证 5/5 通过。

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
- 两个 EXE 均包含应用图标与版本信息（1.1.0）；
- 最终 EXE 的 SHA-256 见 `dist/SHA256SUMS.txt`。

## 6. 已知限制

- 当前网络对 GitHub 的丢包很严重，同一 IP 可能几十秒内从 200 ms 变成超时；程序通过二次抽样、直连验证和备选 IP 切换降低风险，但无法消除线路本身的波动。
- 如果所有 GitHub IP 均不可达，hosts 和本地连接器都无法保证成功；需要使用合法代理或 SSH 443 等替代路径。
- 第三方镜像仅用于公开内容，程序不会自动将其写入全局 Git 配置。
