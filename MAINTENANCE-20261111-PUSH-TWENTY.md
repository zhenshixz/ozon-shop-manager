# 2026-11-11 订单推送汇总改为20分钟

用户要求订单推送由10分钟改为20分钟，同时咨询API Key长期授权方式。

## 配置修改

- 实际线上 `config.json` 的 `order_push_seconds` 从600改为1200。程序原有可配置汇总逻辑直接支持，未修改业务代码或默认兼容回退值。
- 本地脱敏配置样例 `config.example.json` 相应设为1200，已同步服务器同一字段。
- 完整比较修改前后配置，唯一变化为上述字段；订单采集300秒、FBS状态独立任务默认30秒、静默00:00—08:00、22:00上新汇总及其他计划均保留。
- 备份：`/opt/ozon-backups/push-twenty-20261111-143905/config-before.json`，上级目录0700、备份文件0600；manifest记录修改前后校验值，不含密钥。
- 原配置文件保持root所属、应用组可读及0640权限，临时文件原子替换。只重启 `ozon-push-monitor` 使配置生效，利润网页、促销、超重任务及模型未重启。
- 待推送队列与汇总游标保留；按照既有整20分钟边界汇总，有待发送订单才产生订单推送。轮询、发送间隔、限流及预算可影响实际送达时刻。未发送测试微信。

## 验证与回退

校验配置只变更一个字段，服务重启后active，并通过启动日志确认每1200秒汇总。回退时将当前配置的 `order_push_seconds` 改回600并重启主监控，其他字段不要用旧配置覆盖；保留实时数据库。

## Key长期维护结论（未开发）

现有普通Seller API Key受平台有效期约束，不能把当前Key配置成永久。官方提供私有应用OAuth授权：首次店铺授权后，服务器可用refresh_token自动更新access_token与refresh_token；撤销授权或刷新凭据失效时需要重新授权。当前项目仍使用Client-Id/Api-Key，并未实现OAuth刷新或自动更换Key。本轮未修改任何Key、权限或授权。

官方资料：

- https://dev.ozon.ru/news/784-Izmeneniia-v-srokakh-deistviia-kliuchei-Seller-API/
- https://dev.ozon.ru/start/450-Protsess-OAuth-avtorizatsii-dlia-dostupa-k-Seller-API-Ozon/
