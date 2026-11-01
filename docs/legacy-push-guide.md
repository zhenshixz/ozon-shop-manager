# Ozon 十店上新 / 出单 → 微信提醒

为两个人使用的第一版：一个常驻 Python 程序查询十家店铺，通过 pushplus 私密群组通知夫妻双方。没有安卓 App，没有管理网页，没有第三方 Python 依赖，不需要开放任何入站端口。适合现有宝塔 Linux 服务器；2 核 / 2 GB 可作为部署起点，实际余量取决于已有应用。

## 提醒规则

- 默认每 300 秒查询 FBS/rFBS 和 FBO 订单，每 900 秒采集商品；3 个工作线程，店铺错峰调度。每个账户及全局分别控制请求节奏。
- 上新：新发现的 `product_id` 出现在 Ozon 的 `IN_SALE`（在售）筛选结果中时通知。不会只因导入成功就通知。
- 首次成功同步时，已有商品全部作为存量，不补推；**包括当时尚未可售的存量商品**。之后新出现的商品会先记住，待其可售再推送。已知商品下架后再次上架不重复提醒。`ALL` 是否包含店铺的归档商品，应在接入实测中确认；未被初始列表返回的旧归档商品将来重新出现时，可能被当作新商品。
- 订单：FBS 接口覆盖 FBS/rFBS，另外查询 FBO。仅提醒监控首次启动后创建的订单；第一次请求期间产生的订单也纳入。相同店铺按 `order_id` 去重，拆成多个发货单只通知一次；没有 order_id 时按发货单号去重。
- 订单从上次成功时间往前重叠 900 秒增量查询，长时间停机从最后成功进度补查。超过重叠窗口才可见的订单仍可能遗漏；可调整 `order_overlap_seconds`。`order_lookback_hours` 仅用于没有成功进度时的初始查询。
- 保存商品基线、订单事件、通知队列和推送流水；普通重启不会重复补推。**不能删除数据目录，也不要修改已使用店铺的 id。**
- 订单按北京时间每五分钟边界汇总新增记录，有订单才发送。所有订单都计入数量，每店最多展示 10 个订单号，余量提示到卖家后台查看。
- 上新只在北京时间每天 22:00 发送所有店铺汇总，包含零上新的店铺及采集异常提示。统计窗口为上一日 22:00 至当日 22:00；按首次发现且确认可售的时间计入，附带以前未发送的积压记录。已知商品重新上架不重复计数。定时进度与批次持久化，重启后不会重新生成同一日报；停机错过的日报会补发。当天零上新也发送汇总。

五分钟是订单汇总周期，不是端到端送达保证。Ozon 接口可见性、轮询时机、网络、限流、PushPlus 排队与额度、微信通知设置均可能增加延迟。请求按 `Retry-After` 退避，失败重试加入随机错峰；同一账户收到 429 后暂缓其余查询。日志区分网络、超时和非法 JSON，不记录密钥或响应原文。

本版只调用 Ozon 列表查询，不设置 Webhook，不改商品、订单、库存，也不覆盖妙手已有通知配置。只需商品查询和 FBO/FBS 订单查询权限，不需要商品编辑或订单操作权限。

## 1. 准备 pushplus 接收人

已完成服务器部署时，可在宝塔 root 终端直接运行 `bash /opt/ozon-push-monitor/configure.sh`。交互向导会隐藏密钥输入、生成配置、检查接口并发送测试；你确认两部手机都收到后，它会自动启动服务并设置开机自启。无需手工修改 JSON。下文的手动配置说明保留用于其他环境。

1. 你和家人都关注“pushplus 推送加”，在各自账号按官网要求完成接收设置。
2. 由发送方在官网创建一个**私密群组**，两个人都加入该群组（创建者也确认自己已加入）。这不是微信群。
3. 记录发送方的 token 与群组编码 topic。token 留空不能运行；topic 留空则只通知 token 所属用户。
4. 确认以前购买的会员是否仍有效。代码不代购会员，不登录妙手。

普通已实名发送账户默认采用保守的 **13 秒最小发送间隔、每日 180 次本程序请求预算**。pushplus 当前普通额度为每日 200 次、每分钟 5 次；会员为每日 2000 次、每 10 秒 5 次。预算预留部分余量给测试或其他脚本，但**不能知道你的其他程序已经使用的额度**。

若确认会员有效，可将配置改为：

```json
"min_send_interval_seconds": 3,
"daily_request_budget": 1900
```

到达本地预算后保留通知等次日恢复，不会丢弃；每日为晚间汇总预留一次本地请求额度，日报到期优先发送。五分钟都有订单时一天可有 288 个订单批次，超过普通微信额度；此时预算保护会延迟余下通知。平台返回 900 后暂停当天发送，避免反复请求；905 后冷却一小时。应用无法获知其他脚本消耗的额度。

微信普通模板可能需要点击查看内容；会员模板可直接显示部分内容。手机需要允许微信通知、声音和锁屏提醒。务必在两部手机上分别确认测试消息，平台受理成功不等于微信已展示。

## 2. 配置十家店铺

复制 `config.example.json` 为 `config.json`，用宝塔文件编辑器或文本编辑器填写：

```json
{
  "poll_seconds": 300,
  "product_poll_seconds": 900,
  "order_push_seconds": 300,
  "product_digest_hour": 22,
  "order_overlap_seconds": 900,
  "workers": 3,
  "http_timeout_seconds": 20,
  "order_lookback_hours": 48,
  "data_dir": "data",
  "pushplus": {
    "token": "发送方的pushplus令牌",
    "topic": "两人加入的私密群组编码",
    "secret_key": "",
    "min_send_interval_seconds": 13,
    "daily_request_budget": 180
  },
  "shops": [
    {
      "id": "shop01",
      "name": "你熟悉的店铺名字",
      "enabled": true,
      "client_id": "123456",
      "api_key": "这家店对应的Ozon API Key"
    }
  ]
}
```

示例文件已留好十家位置。未填写的店铺保持 `enabled: false`；填好后改为 `true`。建议先启用一家跑通，再启用剩余九家。`client_id` 和其他字段用双引号括起来。

配置文件含密钥，请放在 `/opt/ozon-push-monitor` 这种**非网站公开目录**，不要上传到 `/www/wwwroot` 可下载的位置，也不需要发到聊天中。程序日志不会输出 API Key 或 token。SQLite 只保存商品 ID、货号、订单号和发送记录，不请求客户姓名、地址或电话。

字符串字段也支持写成 `"${PUSHPLUS_TOKEN}"` 等环境变量引用；不自动加载 `.env`。

## 3. 宝塔 Linux 部署（推荐直接运行 Python）

在宝塔文件管理中建立 `/opt/ozon-push-monitor`，上传解压所有项目文件，填好 config.json。通过宝塔终端执行：

```bash
cd /opt/ozon-push-monitor
python3 --version
python3 monitor.py check
python3 monitor.py probe
python3 monitor.py test-push
```

需要 Python **3.9 或以上**。不需要 pip 安装任何包。宝塔版本号不是 Python 版本号；若系统 Python 较旧，使用宝塔安装新版 Python 并在下面 service 中填其绝对路径，或选择 Docker 方案。

- `check`：仅检查配置格式，不联网、不发消息。
- `probe`：只读检查启用店铺的商品、在售商品、FBO/FBS 接口；不写基线、不发消息。任何接口报错先解决相应权限或版本问题。
- `test-push`：发一条真实测试消息给配置的接收人。你和家人都收到后再运行。
- `run`：正式监控；第一次成功商品同步建立基线，旧商品不补推。
- `status`：显示最近成功同步时间、错误、队列和推送受理状态。

**先执行 probe 和 test-push，再正式运行。** 没有真实账号时只能做离线验证，不能提前确认接口权限和端到端延迟。

### 使用 systemd 常驻（Linux 有 systemd 时）

确认上面的检查通过后，以管理员在终端执行：

```bash
cd /opt/ozon-push-monitor
id ozon-monitor >/dev/null 2>&1 || useradd --system --home-dir /opt/ozon-push-monitor --shell /usr/sbin/nologin ozon-monitor
mkdir -p /opt/ozon-push-monitor/data
chown -R ozon-monitor:ozon-monitor /opt/ozon-push-monitor
chmod 600 /opt/ozon-push-monitor/config.json
cp deploy/ozon-push-monitor.service /etc/systemd/system/ozon-push-monitor.service
systemctl daemon-reload
systemctl enable --now ozon-push-monitor
```

service 默认使用当前目标服务器已有的 `/usr/bin/python3.11`。如在其他机器上使用不同的 Python 路径，先编辑 service 的 `ExecStart`。不要同时用宝塔守护进程、systemd、Docker 启动多份。

查看运行和消息记录：

```bash
systemctl status ozon-push-monitor --no-pager
journalctl -u ozon-push-monitor -n 60 --no-pager
cd /opt/ozon-push-monitor
python3 monitor.py status
```

更新配置后：

```bash
systemctl restart ozon-push-monitor
```

不必创建网站、设置域名、证书或开放端口。不会与其他应用争用网页端口。启动时打印本机和服务器网卡地址，只用于定位机器；它们不是网页访问链接，云服务器网卡通常显示私网地址。

### 如果服务器已经有 Docker Compose

选择此方案时，不再启用上面的 systemd 服务。填好 config.json 后：

```bash
cd /opt/ozon-push-monitor
docker compose build
docker compose run --rm ozon-monitor python monitor.py check
docker compose run --rm ozon-monitor python monitor.py probe
docker compose run --rm ozon-monitor python monitor.py test-push
docker compose up -d
docker compose logs --tail=60
```

镜像首次构建需要拉取 `python:3.12-slim`，受服务器镜像源网络影响。容器以非 root 用户运行，只读挂载配置，不映射端口。数据保存在 Compose 命名卷 `monitor-data`；不要执行会删除卷的 `docker compose down -v`。如主机配置文件 chmod 600，需保证容器 UID 10001 有读取权限，例如把文件所有者设为该 UID；它不需要向容器传入 root 权限。

Docker 方式的 data_dir 保持默认 `data`。更新配置后执行 `docker compose restart`。查看状态：

```bash
docker compose exec ozon-monitor python monitor.py status
```

## 4. Windows 本地运行

安装 Python 3.9+ 并勾选 Add to PATH，填好 config.json，双击 `start.bat`。启动前会检查 Python 版本、语法和配置；报错或退出后保留窗口，避免闪退。程序显示本机及局域网网卡地址，不提供网页服务。

也可以在项目目录的终端运行：

```powershell
.\start.bat probe
.\start.bat test-push
.\start.bat status
```

本地电脑休眠、关机或断网后不能继续监控；正式使用建议只在现有云服务器运行。

## 投递状态与重试

- `pending`：等待发送或失败后等待重试。
- `sending`：请求进行中；进程意外终止后延迟 60 秒重试。
- `submitted`：pushplus 返回受理成功和消息流水号，**不代表微信已收到**。默认可在 pushplus 官网发送记录中核对实际结果。
- `sent`：配置下述可选开放接口后，pushplus 回报发送成功；仍不代表两部手机均已展示或已读。

本地事件和通知队列一起持久化；明确失败会按退避重试。网络超时、提交成功但保存回执前进程崩溃，以及群组部分成员成功部分失败时，重试仍有小概率重复。外部平台未提供幂等发送键，无法同时承诺绝不丢失和绝不重复。

### 可选：自动核实平台发送结果

为保持第一版配置简单，`secret_key` 默认留空。如果希望程序自动查询已受理消息的发送结果、对平台报告的失败重试：

1. 在 pushplus 官网开发设置中启用开放接口，设置 secretKey，并把服务器**公网出口 IP**加入安全 IP。
2. `pushplus.token` 使用自己的**用户 token**（开放接口不支持消息 token），填写 `pushplus.secret_key`。
3. 重启程序。程序会获取并刷新 AccessKey，每 15 秒查询部分待确认消息，成功后标记 sent。

若不启用此项，程序可以自动重试请求失败，但无法发现“先受理、后投递失败”。开放接口权限较高，可自行决定是否开通。已受理且仍在排队的消息不会因为排队久而被重复提交。

## 接口与验证范围

Ozon 查询：`/v3/product/list`（ALL、IN_SALE）、`/v4/posting/fbs/list`、`/v3/posting/fbo/list`。两个订单接口使用每页 100 条的 cursor 分页，解析根节点 postings / has_next / cursor；旧版 FBS v3 和 FBO v2 已停用，不能继续调用。商品分页兼容 `total_items` 和旧版 `total`，不会依赖即将废弃的 total。限流/网络错误按接口独立退避，并保留原进度。

商品数据量大时完整采集可能超过轮询间隔；日志会提示，需根据实际容量调整商品采集间隔。程序不更改妙手的通知订阅。

离线检查：

```bash
python3 -m unittest -v test_monitor.py
```

测试覆盖商品基线和可售转换、订单拆单去重、分页/响应异常、通知持久化、限流合并、失败重试和平台异步受理状态；不调用真实 API，不发真实消息。Docker 和 Linux service 需在目标服务器确认，不能用 Windows 的离线测试替代。

参考官方资料（接入时以平台现行规则为准）：

- [Ozon Seller API](https://docs.ozon.ru/api/seller/)
- [Ozon 关于 total → total_items 的公告](https://dev.ozon.ru/news/801-Otkaz-ot-polia-total-v-metodakh-Seller-API/)
- [pushplus 消息接口](https://www.pushplus.plus/doc/guide/api.html)
- [pushplus 私密群组与一对多](https://pushplus.plus/doc/function/more.html)
- [pushplus 额度](https://pushplus.plus/doc/guide/use.html)
- [pushplus 队列延迟说明](https://www.pushplus.plus/doc/help/delay.html)
- [pushplus 开放接口（可选投递核实）](https://pushplus.plus/doc/guide/openApi.html)
