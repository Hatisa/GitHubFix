# Changelog

## 1.1.0 - 2026-11-11

### Fixed

- 「hosts 未写入（可用IP不足或验证失败）」：写入后的验证改为直连 IP 测试（TCP + TLS 证书校验 + HTTP），不再依赖可能尚未刷新的系统 DNS 缓存。
- 单个非核心域名不可达不再导致整块回滚；不可达条目会被剔除，可用的 github.com 条目保留。
- github.com 直连失败时自动依次尝试其他已验证候选 IP，而不是立即放弃。
- 回滚目标固定为首次写入前的原始备份，避免二次写入后回滚到「已污染的中间状态」。
- 验证失败提示改为说明真实原因（TLS 全失败 / DNS 未生效 / 已剔除的域名）。

### Changed

- 探测并发从 8 降到 5，域名级并发从 5 降到 4，减少集中建连被限流的概率。
- hosts 结果对话框展示逐个域名的直连与 DNS 验证结果。

## 1.0.0 - 2026-11-10

### Added

- 多来源 GitHub 候选 IP 收集：AliDNS / DNSPod / 114DNS UDP、DoH、GitHub520/ineo6 hosts 订阅。
- TCP + TLS 证书校验 + HTTP 响应探测，并对候选进行二次抽样稳定性评估。
- 安全的 hosts 管理：自动备份、旧版块替换、原子写入、端到端验证、失败自动回滚。
- 本地智能连接器：仅处理 GitHub HTTPS 的 HTTP CONNECT 代理，多 IP 并发竞速与失败重试。
- 按域名分流的 PAC，关闭程序自动恢复原系统代理。
- Git SSH 443 配置（github-443 别名）与全局 Git URL 改写检测/清理。
- 公共 GitHub 镜像实时测速与加速 clone 命令生成。
- 中文 Tkinter 桌面界面、CLI、Markdown 诊断报告。
- Windows onefile 打包，GUI 使用 requireAdministrator manifest。

### Security

- 不安装根证书，不进行 TLS 中间人解密。
- 本地代理仅绑定 127.0.0.1，只接受 GitHub 域名的 443 CONNECT。
- 所有 hosts/系统代理/Git 修改都有备份或状态文件。
- 第三方镜像明确标注为公开内容专用，不自动写入全局 Git 配置。
