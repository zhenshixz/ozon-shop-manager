# 维护、同步和部署

## 唯一源码入口

本地仓库：`K:\github\ozon-shop-manager`。以后GitHub提交、上传阿里云和AI编辑都从此目录开始。服务器现有部署：`/opt/ozon-push-monitor`。两个路径名称不同但属于同一产品，不迁移现有服务或数据库。

使用具有SSH能力的终端时，连接方式见根目录 `HANDOFF-GEMINI.md`。该文档里的密钥路径在本机仓库外；换电脑需自行准备已有SSH权限。

## 当前线上环境

| 项目 | 当前位置/服务 |
| --- | --- |
| 订单利润页 | https://voice.xzbest.site/ozon-profit/ |
| 数据罗盘 | https://voice.xzbest.site/ozon-profit/#compass |
| 推送及同步 | ozon-push-monitor.service；系统/usr/bin/python3.11 |
| 网页 | ozon-profit-web.service；/opt/ozon-profit-runtime/bin/gunicorn；127.0.0.1:5310 |
| 共用数据 | /opt/ozon-push-monitor/data/monitor.sqlite3 |
| 标题模型 | ozon-title-engine.service；127.0.0.1:5311 |
| 备份 | /opt/ozon-backups/下带时间的主题目录 |

## 日常修改流程

1. 先查看本地Git状态、最新维护记录，并核对服务器文件是否被其他AI修改。如果线上较新，先把源码变更拉回此仓库；不覆盖本地未保存改动。
2. 在此仓库编辑，先做与修改相关的检查。前端以实际手机和桌面效果为准；没有改计算时，不重复运行整套业务测试。
3. 发布前在服务器备份将要替换的文件，记录文件校验值和备份路径；只上传改动的源码或静态文件。真实配置、data目录、密钥、日志和模型权重留在运行环境，不使用旧数据库回退代码。
4. JS/CSS变化时更新web/index.html对应的?v=资源版本。原有四个JS/CSS文件在profit_web.py白名单中；新增资源需要检查对应路由。
5. 纯静态资源替换通常立即生效，无需重启。仅修改网页Python时重启ozon-profit-web；修改profit_core.py等两个进程共用的模块时，还需重启ozon-push-monitor。不要为UI更新重启模型服务。
6. 检查改动涉及的页面/接口，补充MAINTENANCE-日期-主题.md和docs/INDEX.md。本地提交Git；如果服务器做过临时修改，要先同步回本仓库再提交，避免分叉。

## 在新服务器恢复

完整恢复需要代码、运行配置、业务数据、依赖与域名反向代理，单独clone代码不包含真实运行状态。

- Python建议沿用现网3.11以上。推送程序主要用标准库；网页依赖见requirements.txt，版本取自本次现网环境。
- 推送配置参考config.example.json，网页配置参考profit-web.example.json。真实配置只在目标环境生成，不能上传GitHub。
- 部署服务模板位于deploy/。现有服务器已经安装服务，无需重新复制模板或重新运行configure.sh。
- 若迁移服务器，应独立、安全地迁移真实业务数据库和必要配置，保留通知去重状态。不要在生产对接前运行会产生模拟消息的脚本。
- title-translation.example.json提供配置示例；当前本地模型引擎/权重需单独恢复。详见MAINTENANCE-20261031-LOCAL-TITLES.md。
- 根目录Dockerfile/compose.yaml保留用于回溯早期推送版，不作为完整产品的部署入口。

## GitHub保存

此目录是独立Git仓库。绑定目标GitHub远程仓库后，提交并推送main分支即可；不上传整个K:\github，也不上传旧目录和.server-access。仓库的.gitignore已排除真实配置、数据库、缓存、密钥和环境目录。
