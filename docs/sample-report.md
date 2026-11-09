# GitHubFix 网络诊断报告

- 生成时间: 2026-11-10T00:19:12+08:00
- 管理员权限: 否
- hosts 文件: C:\WINDOWS\System32\drivers\etc\hosts
- GitHubFix 配置: 未安装
- 检测到旧版 GitHub Fix 配置

## 结论
- 核心域名可用 5/5
- github.com 最佳: 140.82.112.3 (1184.0 ms)
- API 最佳: 20.27.177.116 (163.7 ms)
- Raw 最佳: 185.199.110.133 (626.3 ms)

## 核心域名

| 域名 | 最佳 IP | TCP+握手+HTTP | HTTP 状态 | 来源 |
|---|---|---:|---:|---|
| github.com | 140.82.112.3 | 1184.0 ms | 200 | 内置候选 |
| api.github.com | 20.27.177.116 | 163.7 ms | 200 | 内置候选 |
| codeload.github.com | 20.205.243.165 | 592.0 ms | 301 | 114DNS-UDP |
| raw.githubusercontent.com | 185.199.110.133 | 626.3 ms | 200 | 114DNS-UDP |
| github.githubassets.com | 185.199.109.154 | 633.1 ms | 404 | 内置候选 |
| avatars.githubusercontent.com | 185.199.109.133 | 601.6 ms | 200 | 114DNS-UDP |
| objects.githubusercontent.com | 185.199.108.133 | 579.5 ms | 404 | 114DNS-UDP |
| release-assets.githubusercontent.com | 185.199.110.133 | 647.2 ms | 404 | 114DNS-UDP |
| gist.githubusercontent.com | 185.199.109.133 | 628.5 ms | 301 | 114DNS-UDP |
| ssh.github.com | 20.205.243.160 | 90.6 ms | - | 114DNS-UDP |

## 建议
- 直连测速正常，可执行“一键修复”写入最优 hosts。
- 检测到旧版 GitHub Fix 配置，将在修复时自动备份并替换。
- 检测到第三方 Git URL 改写，Git 可能绕过 hosts/智能代理；请在“Git 加速”页清理并备份。

## 环境

- hostname: "demo-pc"
- platform: "Windows-10-10.0.26200-SP0"
- python: "3.11.5"
- dns_errors: {}
- feeds_ok: ["GitHub520", "ineo6", "ineo6-gitlab", "GitHub520-ghfast", "ineo6-ghfast"]
- feeds_errors: {"jaywcjlove": "HTTP Error 404: Not Found"}

---
GitHubFix 只修改自己标记的 hosts 配置块；原始文件会备份到本地数据目录。