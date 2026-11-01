# OZON 店铺助手

一个仓库维护两项功能：**上新/出单微信推送**与**订单利润核算及数据罗盘**。

- 唯一本地工作目录：`K:\github\ozon-shop-manager`
- 当前服务器部署目录：`/opt/ozon-push-monitor`
- 在线页面：https://voice.xzbest.site/ozon-profit/；数据罗盘使用同页`#compass`入口。

两个功能已共用后台代码与SQLite数据库。现有两个systemd服务继续分工运行，不为产品整合迁移线上目录或改变业务逻辑。GitHub归档和以后的阿里云更新都以本仓库为源。

## 从这里开始

| 你要做什么 | 阅读 |
| --- | --- |
| 了解推送、利润页及罗盘结构 | [项目说明](docs/PROJECT.md) |
| 修改后上传阿里云、恢复部署 | [部署与同步](docs/DEPLOYMENT.md) |
| 交给Gemini或其他AI修改 | [AI工作约定](AGENTS.md)及[连接交接说明](HANDOFF-GEMINI.md) |
| 查历史改动、备份和回退 | [文档索引](docs/INDEX.md) |

## 代码位置

`monitor.py`负责推送调度，`profit_sync.py`负责订单数据补全，`profit_core.py`负责核算，`profit_web.py`提供网页API，`compass_core.py`聚合经营数据，`profit_titles.py`处理标题翻译。网页都在`web/`，使用原生HTML/CSS/JavaScript，无需前端构建。

真实配置、密钥、业务数据库和模型权重不在Git仓库中；样例配置及网页依赖分别见`*.example.json`和`requirements.txt`。原Docker/配置向导等早期入口仍保留供回溯，完整产品部署以当前部署文档为准。

本次以2026-11-01服务器最新版建立统一仓库，已包含Gemini最新前端优化。原有两个本地工作目录归档保留，停止从旧目录发布。关键变更持续记录到同一仓库的维护文档中。
