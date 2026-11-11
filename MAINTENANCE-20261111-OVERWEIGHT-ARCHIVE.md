# 2026-11-11 API 单件重量排查及指定 9 款归档

## 授权与结果

用户要求只读查询 10 家店 API 单件重量超过 2000g 的商品，随后明确授权将上轮列出的全部 9 款归档。本次仅处理该固定清单，不把后续新增商品自动归档，也未新增后台规则。

只读扫描共覆盖 3801 条商品记录；全部商品属性成功返回，无缺失或未知重量。按 API `weight` 和 `weight_unit` 换算为克，不乘订单件数。发现 9 款，归档前均未归档，其中 8 款有 FBS 库存；另 1 款无库存、SKU 为 0，但有有效 product_id。

北京时间 2026-11-11 11:43:54 完成操作：9/9 款均在归档后重新查询确认 `is_archived=true`，不只依赖写接口的 `result=true`。

| 店铺 | 本次归档款数 | 逐款复查 |
| --- | ---: | --- |
| Luo001 | 2 | 均已归档 |
| Luo002 | 2 | 均已归档 |
| Luo003 | 1 | 已归档 |
| Luo005 | 1 | 已归档 |
| Luo006 | 1 | 已归档 |
| Luo008 | 1 | 已归档（SKU 为 0，按 product_id 处理） |
| Luo010 | 1 | 已归档 |

Luo004、Luo007、Luo009 未发现满足条件的商品，未发送归档请求。

## 接口与边界

- 查询：`/v3/product/list`（ALL，完整分页）、`/v4/product/info/attributes`。
- 操作前：通过 `/v3/product/info/list` 校验店铺对应的 product_id、offer_id、SKU 一致，并保存原始状态。
- 写入：`POST /v1/product/archive`，`product_id` 数组，按店铺分组；Luo001、Luo002 实际各一次请求处理 2 款，其他相关店各 1 款。不同店铺凭证不混用。
- 复查：`/v3/product/info/list`，确认每个指定 product_id 的 `is_archived=true`。全部一次归档请求成功，无失败、未确认或重试写入。
- 本次仅调用归档写接口；没有调用库存清零、删除、价格/重量修改或订单处理接口。归档请求在现有 FBS 库存状态下已成功，未增加无必要的前置库存修改。
- 本项目业务代码、配置、订单数据库未修改，服务未重启。网页既有商品状态任务仍按原计划刷新缓存。

官方资料：

- https://docs.ozon.ru/api/seller/#operation/ProductAPI_ProductArchive
- https://dev.ozon.ru/start/257-FAQ-spravochnik-oshibok-i-sposoby-ikh-resheniia （FAQ 明确支持卖家通过归档方法将商品从销售中隐藏）
- https://dev.ozon.ru/start/300-Mapping-rolei-i-metodov-Seller-API/ （Product 权限对应 `/v1/product/archive`）

## 私有记录与恢复

- 排查结果（仓库外）：`K:\github\diagnostics\weight-audit-20261111\`，包括脚本、结果 JSON、可搜索 HTML 清单。
- 本地执行记录（仓库外）：`K:\github\diagnostics\overweight-archive-20261111\`，包括固定 targets.json、执行脚本、服务器记录副本。
- 服务器操作前后记录：`/opt/ozon-backups/overweight-archive-20261111-114338/`，包含目标清单及其 SHA-256、各店 before.json、archive-request.json、archive-response.json、after-0.json、result.json 和 summary.json。目录权限 0700，不含配置或凭证。
- 本维护说明及文档索引已同步服务器。真实商品清单/响应留在上述私有目录，不纳入 Git。
- 如需恢复某款，应明确选择本次清单中的商品并在 OZON 后台从归档恢复，恢复后检查售卖状态与库存。不要自动恢复整个清单，不通过恢复本地或服务器数据库撤销 OZON 的归档状态。
