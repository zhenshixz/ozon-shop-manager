# 2026-11-01 整合为OZON店铺助手统一仓库

## 目的与统一约定

- 用户希望把微信推送、利润核算及罗盘整合为同一产品，统一GitHub保存、阿里云部署和不同AI的维护入口。
- 产品名：OZON店铺助手；仓库及本机唯一工作目录：`K:\github\ozon-shop-manager`。
- 服务器沿用`/opt/ozon-push-monitor`，两个现有服务继续共用代码和业务数据库。不迁移运行目录、不改变服务名、不合并进程、不修改业务逻辑。

## 来源与整理

- 从服务器导出55个最新源码/文档文件，包括Gemini最新前端优化；原始文件校验值保存在SERVER-SNAPSHOT.json。
- 导出时排除真实配置、API Key/Token、SSH私钥、业务数据库、通知状态、日志、缓存和模型权重，并检查源码不包含当前配置中的密钥。
- 原始源码全部保留。仅README.md、HANDOFF-GEMINI.md及.gitignore为整合而有意更新，原README另存docs/legacy-push-guide.md；运行代码和五个前端资源仍与快照一致。
- 新增AGENTS.md、docs/PROJECT.md、docs/DEPLOYMENT.md、docs/INDEX.md作为通用AI入口与维护索引。历史维护文档保留原路径。
- 新增requirements.txt（从现网读取Flask3.1.3、Gunicorn23.0.0）、脱敏profit-web.example.json及网页服务参考模板；不覆盖现有运行配置，不安装模板到systemd。
- 根目录Dockerfile/compose.yaml等早期入口仍保留供回溯；当前完整产品按systemd部署说明维护。
- 初始化独立Git仓库和main分支，建立初始源码保存点；未绑定远程仓库，不自动向GitHub发布。

## 旧目录与回退

- 原`K:\github\ozon-push-monitor`及`K:\github\ozon-profit-update`完整归档到`K:\github\.server-access\legacy-workspaces-20261101`；其中本地QA截图/脚本等不纳入新产品Git仓库。旧文件不删除，但停止从旧目录发布。
- 本次服务器仅同步统一说明、维护文档、依赖/配置样例及服务参考文件；备份位于/opt/ozon-backups/unified-product-日期时间，路径由服务器部署时追加。没有修改运行代码、真实配置或数据库，没有重启服务。
- 如需回退文档，按上述服务器备份恢复对应文档即可；本地旧工作副本保留在归档目录。

## 检查

- 归档压缩包SHA256和55个文件的源校验值均通过；核对线上最新前端资源与快照一致。
- 本次没有功能实现变更，不重复跑业务测试。检查Git忽略真实配置、数据库、私钥及缓存；检查本地与服务器的统一文档链接及文件。
- 后续维护以新仓库为源；临时直接改服务器后，应先同步回新仓库再提交Git，保持双方一致。

服务器文档同步完成：2026-11-01T16:55:28；备份：`/opt/ozon-backups/unified-product-20261101-165528`。本次未重启服务。
