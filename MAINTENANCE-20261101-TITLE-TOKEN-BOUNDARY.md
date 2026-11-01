# 2026-11-01 混合语言标题规格校验误拦截

- 用户反馈新订单仍显示“译名未通过校验”。只读检查发现本地模型与同步服务正常，当前仅1条空译文为specification-mismatch。
- 原文DUNU S&S Eartips标题末尾包含S/M/L-кодов。模型已经返回包含品牌、1对及S/M/L的中文译文，但旧正则把L-识别为必须保留的型号；合法译文去掉俄语连接符后被拒绝，每5分钟确定性重试仍重复失败。
- 修复required_title_tokens：连接符仅在两侧均有ASCII字母/数字时属于标识符；保留完整S&S、S/M/L与JSQ-050，不把俄语后缀前的悬空连字符当作型号。
- 数字、真实品牌/型号、尺寸及既有类目保护继续生效。新增实际标题回归测试，覆盖正确译文通过及数量、品牌、尺寸、型号被改时仍拒绝。
- 隔离test_product_view共25项通过；只修改profit_titles.py和对应测试，未更换模型或翻译供应商。
- 发布前核对源文件校验值并备份；仅重启ozon-push-monitor加载新规则，将该空译文失败记录重新加入正常翻译队列。不触碰采购成本和订单数据，不发送模拟通知，不覆盖已确认译文。
- 服务器备份与线上恢复结果在部署后补记；同步至统一本地仓库保留。

备份：`/opt/ozon-backups/title-token-boundary-20261101-183137`。

已发布并重启同步服务，将1条空译文重新排队；采购价等业务数据未变更。

Live verification: standard background queue populated Chinese cache; profit API returns non-empty name_zh with Local/HY-MT1.5-1.8B and no translation error for the affected product. No manual reviewed translation was inserted.
