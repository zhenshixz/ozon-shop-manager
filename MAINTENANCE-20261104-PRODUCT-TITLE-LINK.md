# 利润详情商品标题链接

日期：2026-11-04。

- 原因：核算详情中需要快速查阅对应 OZON 商品。
- 修改 `web/app.js`：仅在采购成本与利润详情中，将有有效数字 SKU 的商品标题链接到 `https://www.ozon.ru/product/<SKU>/`，新标签页打开，设置 noopener/noreferrer。缺失或无效 SKU 保持普通标题；中文/原文切换和多商品展示沿用原逻辑。
- 修改 `web/style.css`：保持标题原样式，增加外链箭头、悬停和键盘焦点效果。
- 修改 `web/index.html`：更新 app.js/style.css 缓存版本。
- 验证：JS 语法及桌面/手机标题展示、链接地址、新标签页属性、无 SKU 回退。未保存订单、未触发促销清理。
- 发布：仅替换静态文件，不重启服务。服务器备份 `/opt/ozon-backups/product-title-link-20261104/before/`，发布前核对源文件 SHA256。
- 回退：从上述备份恢复对应静态文件和文档索引，不覆盖任何数据库或运行配置。
