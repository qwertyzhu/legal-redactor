# 安全说明

英文见 [SECURITY.en.md](SECURITY.en.md)。

## 支持的版本

早期预览阶段，仅最新打标签的 Release 接受安全修复。

## 报告漏洞

仓库公开后，请使用 [GitHub 私密漏洞报告](https://github.com/qwertyzhu/legal-redactor/security/advisories/new)。

**禁止**在公开 Issue 中附上真实合同、卷宗、客户身份或脱敏 ledger。

## 数据处理边界

- legal-redactor 按**本地**处理设计。
- `*.ledger.json` 是去标识密钥，按机密对待。
- 仓库样例均为虚构。不要换成在办案件材料。
- 模型供应商政策、备份和访问控制，仍由使用者自行负责。

## 意外泄露

若误推了真实 ledger 或客户文件：

1. 轮换任何已暴露的口令或密钥；
2. 仅在理解影响后，才用 `git filter-repo` 改写历史并强制推送；
3. 必要时联系 GitHub Support 清除缓存提交；
4. 按律所事件流程处理客户数据。
