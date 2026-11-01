# 2026-10-30 复核官方状态与妙手流程

## 计划
逐单读取官方 FBS 详情，对照十家店近 7 日 FBS 列表，再查官方主/子状态及妙手帮助文档；核实后修正有歧义的展示，记录证据。

## 查询结果（北京时间 2026-10-30 11:37）
- `/v3/posting/fbs/get`：12 个包裹查询成功；11 个 `awaiting_deliver / posting_transferring_to_delivery`，1 个 `awaiting_packaging / posting_created`。
- 唯一待备货包裹为 Luo003，`37646537-0121-1`。此前仍为待备货的 `68429999-0514-1`、`46830408-0174-1`、`73904782-0096-1` 已变为待交运；说明同步确实推进了新状态。
- 12 个包裹缓存与实时详情一致；详情缓存距查询约 20–39 秒。十家店 `/v4/posting/fbs/list` 近 7 日结果与详情一致，未发现列表漏录包裹。
- 本轮未查到 `delivering`（配送中）或 `delivered`（已送达）。12 单均有平台运单号；运单号存在本身不能证明已揽收。
- 本轮不读取妙手私有订单明细，也不把截图分类数量强行映射到某些订单。

## 两种状态体系
- OZON `status` 是平台主状态：常见为待备货 `awaiting_packaging` → 待交运 `awaiting_deliver` → 配送中 `delivering` → 已送达 `delivered`；另有取消、验收、争议等分支，并非每单走完全部阶段。
- `substatus` 是更细的平台阶段。例如 `posting_created` 为已创建，`posting_transferring_to_delivery` 为正在移交配送。仅显示子状态，会丢失主状态“仍在待交运”的信息；也不能据此认定包裹已进入实际配送。
- 妙手“待处理”“待打单发货”“交运平台”属于 ERP 操作流程，涉及采购/库存、报关、物流选择、运单、打印、提交发货。不能与 OZON 主状态逐项等同。
- 截图“未出货 / 已出货”在“待处理”页内，是妙手出货信息分类。妙手帮助明确：一单多品需要全部产品关联出货信息才流入“已出货”；这个“已出货”并非 OZON 的 `delivering`。
- 妙手国内采购单、打印或货代处理的细分状态，不能从本项目当前调用的 OZON `status/substatus` 还原。若将来需要同步这些操作进度，应另接妙手或对应采购/货代的数据，不能伪造官方物流阶段。

## 本次修正
- 列表及核算详情同时显示主状态与子状态，例如“待备货 · 订单已创建”“待交运 · 正在交接配送”，减少误读。
- 修改前端展示及 JS 资源版本；不修改后端 API 状态、订单记录和财务字段，不触发订单或发货操作。
- 保持日间模式和手机单行四格统计。JS 语法检查及隔离浏览器预览通过；覆盖主/子状态组合、大图、中文切换、既有重量保护和手机布局。
- 已部署。静态资源及原文件备份：`/opt/ozon-backups/status-labels-20261030-114113`。未重启服务；回滚可恢复其中的 `web/app.js` 和 `web/index.html`。
- 线上桌面及 390px 手机只读验证通过：12 个包裹、12 张图、12 个中文标题；11 个待交运和 1 个待备货的组合标签与接口一致。图片放大、标题切换及统计单行正常；未保存真实业务表单。
- 本记录及脱敏查询证据同步至 `/opt/ozon-push-monitor/` 项目目录。

## 资料
- OZON 官方 FBS 主状态及流程：https://dev.ozon.ru/start/257-FAQ-spravochnik-oshibok-i-sposoby-ikh-resheniia
- OZON 官方子状态说明：https://dev.ozon.ru/start/448-Spravochnik-metodov-Seller-API-dlia-Ozon-logistiki/
- 妙手订单状态介绍：https://erp.91miaoshou.com/help_center/article_13372.html
- 妙手“已出货”条件：https://erp.91miaoshou.com/help_center/article_7616.html
- 妙手 Ozon 代发流程：https://erp.91miaoshou.com/help_center/article_2263.html

脱敏查询证据保存在同目录 `status-audit-20261030.json`，不包含 API 密钥、客户资料或登录会话。
