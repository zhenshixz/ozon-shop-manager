# 2026-11-01 服务器最新版本归档到本地

- 服务器目录：`/opt/ozon-push-monitor/`；服务器导出时间：2026-11-01T08:48:02.887705+00:00。
- 本地目录：`K:\github\ozon-shop-manager`。
- 同步服务器55个源码及文档文件，包括Gemini最新前端优化、自动核算规则、监控/同步/翻译代码、样例配置和服务器维护记录。
- `SERVER-SNAPSHOT.json`保存原始服务器文件大小与SHA256，可用于版本回溯。归档压缩包SHA256：`f849c9d8e6087b7983bbf97e2c237db5faa2f8972a6800e1e0957b9e31eb4ddd`。
- 只归档代码，不含真实config.json、profit-web.json、title-translation.json、业务数据库、通知状态、日志、缓存、SSH密钥或本地模型大文件。
- 本地额外增强.gitignore，避免真实配置、数据库、私钥、虚拟环境进入Git；此文件因此与原始服务器快照存在有意差异。
- 同步前有变化的本地文件已备份：`K:\github\.server-access\local-before-ozon-shop-manager-20261101-165217`。本次只从服务器读取，不修改线上代码或重启服务。
- 当前版本网页还依赖Flask/Gunicorn运行环境；本地标题模型服务在服务器独立目录，模型二进制不纳入源码仓库。原README中的第一版说明请结合最新维护文档阅读。
- 本次仅做归档校验，不重新运行服务器已验证的业务测试；不主动推送GitHub。
