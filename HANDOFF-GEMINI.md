# OZON 项目交接给 Gemini：连接服务器与修改前端

更新：2026-11-01。统一产品为OZON店铺助手；唯一本地仓库K:\github\ozon-shop-manager。先阅读README.md、AGENTS.md和docs/INDEX.md，再核对线上是否有较新改动；以后GitHub提交和阿里云发布都从统一仓库进行。服务器现有部署目录继续沿用/opt/ozon-push-monitor。

## 1. 连接服务器并读取文档

服务器：8.138.252.219；用户root；SSH默认22端口。已有本机私钥，不需要新建账号或重新提供API Key。

在当前Windows电脑PowerShell中执行：

```powershell
& 'C:\Windows\System32\OpenSSH\ssh.exe' -i 'K:\github\.server-access\ozon-8.138.252.219-ed25519' -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=K:/github/.server-access/ozon-known-hosts root@8.138.252.219
```

登录后执行：

```bash
cd /opt/ozon-push-monitor
cat HANDOFF-GEMINI.md
ls -1t MAINTENANCE-*.md
```

连接需要能执行SSH并访问本机密钥的终端环境；在其他电脑/纯聊天环境，以上本机路径不能直接使用，应由用户粘贴本文或另行配置已有SSH访问。无需把私钥内容发给模型。

## 2. 项目结构与页面

- 在线订单利润页：https://voice.xzbest.site/ozon-profit/
- 数据罗盘：https://voice.xzbest.site/ozon-profit/#compass
- 服务器项目目录：`/opt/ozon-push-monitor/`
- 本机工作副本：`K:\github\ozon-shop-manager\`。修改前核对服务器是否有其他AI的新变更。
- 前端为原生HTML/CSS/JavaScript，无Vue/React、无npm构建流程。
- `web/index.html`：页面结构、订单核算/采购成本等弹窗、资源版本参数。
- `web/style.css`：首页、订单卡片、通用弹窗和响应式样式。
- `web/app.js`：订单列表、筛选、核算编辑/预览/保存、图片、成本弹窗、首页统计。
- `web/compass.js`、`web/compass.css`：数据罗盘、商品/店铺榜、店铺下商品表现、返回导航和详情。
- `profit_web.py`：Flask路由及API。`profit_core.py`：核算、订单读取。`compass_core.py`：全量经营统计及榜单聚合。纯前端优化通常无需修改这些文件。
- `monitor.py`、`profit_sync.py`：OZON同步和Pushplus通知相关后台；`profit_titles.py`：标题翻译。界面调整无需动这些后台。
- `data/monitor.sqlite3`：真实订单、采购成本、核算输入、缓存和通知状态。不要用旧数据库覆盖线上数据库。

## 3. 当前已有功能与需要保留的口径

- 10家店铺订单同步，Pushplus向夫妻两人推送；商品图片缓存和缺图重试、本地标题翻译。主页已按用户明确要求开放免登录查看和修改，保留现有API会话/CSRF流程。
- 首页核心统计、待填写/已计算数量TAB、店铺/日期/搜索筛选、采购成本明细弹窗及订单核算弹窗。
- 采购成本含取消/退货的已填成本，并单列这部分金额；未填采购成本不作为0成本。成本明细有商品图片、店铺、订单、商品、采购成本、售价、利润。
- 销售额含所有有效订单；利润/利润率只使用同一批完整核算的有效订单，取消/退货不计入利润合计。
- 运营起始日为2026-10-24；展示不写“代运营”。
- 罗盘含商品/店铺两个维度，销量榜/利润榜/亏损榜，店铺→店内商品→商品详情→关联订单的下钻；返回时保留筛选、排序、滚动位置。
- 商品利润榜可切平均单件/累计利润；单件利润分母仅为已核算件数。店铺利润榜按累计利润；从店铺利润进入店内商品时默认累计利润。
- 最近确认的自动核算：售价≤135元且重量＜500g为超轻小件，500g起至25kg为低客单；售价＞135元且重量≤2000g为轻小件，＞2000g至25kg为大件。轻小件默认费率17%。计费重量优先明确填写的自定义重量，否则使用API重量。
- 首屏空间紧凑、移动端优先，不增加大块顶部卡片或把所有说明铺在首页。新增交互参考成熟产品，保持现有下钻和返回逻辑。

## 4. 修改和发布方式

1. 先读取服务器最新源码与对应维护记录，记录原文件校验值，并把要修改的文件备份到`/opt/ozon-backups/`下带时间的目录。
2. 修改现有前端文件。直接替换线上静态文件即可生效；在`web/index.html`里更新相关JS/CSS的`?v=`版本参数，避免浏览器缓存。仅静态资源修改通常不用重启服务。
3. 如果新增静态资源文件，检查`profit_web.py`的assets文件白名单；现有app.js、style.css、compass.js、compass.css已允许访问，index.html由主页路由返回。
4. 只有修改Python网页后端时才需要`systemctl restart ozon-profit-web`。若修改`profit_core.py`等后台和网页共用模块，还需要重启`ozon-push-monitor`；纯前端优化不要重启同步/翻译服务。
5. 做必要检查：JS语法、手机/桌面实际显示、筛选/弹窗/返回/预览功能。测试预览接口不会保存；真实保存接口会写业务数据，不要用真实成本做随意测试。不要主动发送模拟通知。
6. 在服务器创建或补充`MAINTENANCE-日期-主题.md`，记录改了哪些文件、备份路径、检查结果及回退方式。需要回退时恢复对应代码，避免覆盖实时数据库。

网页服务为`ozon-profit-web.service`，Gunicorn监听`127.0.0.1:5310`，Python环境为`/opt/ozon-profit-runtime/bin/python`；域名反向代理到网页服务。无需重新搭建站点或改宝塔配置。

常用API前缀为`/ozon-profit/api/`，可直接查`profit_web.py`了解参数及结构：`bootstrap`、`orders`、`orders/<id>`、`orders/<id>/preview`、`statistics`、`cost-statistics`、`analytics`、`images`。保存沿用app.js现有会话、CSRF和重量确认机制。

## 5. 优先阅读的服务器维护文档

- `MAINTENANCE-20261101-SHOP-DRILL.md`：店铺下商品表现、下钻和返回状态。
- `MAINTENANCE-20261101-SHOP-DIMENSION.md`：罗盘紧凑摘要与店铺维度。
- `MAINTENANCE-20261101-PROFIT-CONTROL.md`、`MAINTENANCE-20261101-UNIT-PROFIT.md`：利润榜交互与单件/累计口径。
- `MAINTENANCE-20261101-COMPASS.md`：罗盘初始实现与统计。
- `MAINTENANCE-20261101-AUTO-TARIFF.md`：最新自动选档、17%费率、已保存档位迁移及备份。

本文为连接与结构交接说明，不含店铺API Key、Pushplus Token或私钥内容。配置文件`config.json`、`profit-web.json`等保留在服务器，不嵌入前端。
