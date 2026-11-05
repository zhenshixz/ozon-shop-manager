# 商品下架与归档标记、初次统一更新及半小时同步

日期：2026-11-05。用户授权：已有数据统一更新一次，后续每30分钟同步；订单、罗盘列表保持简洁，只标记异常商品。

## 实现

- 新增 `product_status.py`：从现有订单数据库只读提取所有历史商品，按店铺及SKU去重（无SKU则用货号），串行批量调用现有 `/v3/product/info/list`，仅查询，不调用商品/价格/库存修改接口。
- 独立缓存 `data/product-status.sqlite3`，不改订单、采购价和利润数据；保留成功快照及失败尝试，查询失败不清空旧状态。文件锁防止重叠执行，未知枚举不强判下架。
- 归档优先按 is_archived/is_autoarchived 判断；非归档按对应SKU的availabilities判断隐藏与可售，多来源存在AVAILABLE不误标全商品下架。下架原因读取 reasons，已核实的6和81显示“侵犯版权”“缺少品牌文件”，其他原因保留平台原文。归档不附会历史建卡错误为归档原因。
- `profit_web.py`：订单列表/详情及罗盘接口从本地缓存附加同一份状态；新增只读 `/ozon-profit/api/product-status`，沿用现有访问机制，仅返回配置店铺状态。
- `web/app.js`、`web/compass.js`、`web/style.css`：红色“已下架”、灰色“已归档”，正常商品不加标签。列表悬停可看原因/时间，详情直接显示；旧状态超65分钟提示等待同步。页面可见时每分钟读取缓存，原位更新标记，不重建成本表单、不触发Ozon请求。`web/index.html`更新资源版本。
- `deploy/ozon-product-status.service/.timer`：服务器北京时间每整点及半点运行，开机补执行，MemoryMax128M、CPUQuota20%、Nice10、串行请求间隔1.5秒，单次超时30分钟。浏览器无需常开，用户电脑无需开机。

范围是两处页面已有历史订单所涉及的商品（包括取消订单中的商品），不是扫描全店所有从未出现在这些列表中的商品。新订单商品将在下一轮纳入。

## 首次执行与检查

- 2026-11-05 12:07（北京时间）完成首次同步：60个店铺商品，成功60，失败0；52个可售、4个下架、4个归档。统计按店铺商品计数，同SKU跨店不合并。
- 定时器已启用，初次显示下一次北京时间12:30运行。
- 3项无外部调用测试通过：真实隐藏原因与归档优先级、SKU来源匹配及未知状态、店铺缓存隔离/空缓存回退。
- 桌面与390px手机实际页面检查通过：真实下架样本6161843658在订单列表与罗盘详情显示标签及两条原因。缓存响应统计与初次执行一致，无JS错误。
- 验证页面轮询不会覆盖正在编辑的采购价；仅修改浏览器输入做检查并关闭弹窗，没有提交，浏览器检查期间POST请求为0。
- 本次只HUP网页Gunicorn加载接口，未重启推送、模型、促销服务，未手动触发促销清理。已有历史利润口径不变。

## 文件、备份及回退

服务器备份：`/opt/ozon-backups/product-status-20261105/before/`，发布校验值在同目录manifest.json，发布前检查与现网一致。首次部署以及页面缓存原位更新补充均从同一份原始备份回退。

源码文件：product_status.py、profit_web.py、web/app.js、web/compass.js、web/style.css、web/index.html、test_product_status.py、deploy/ozon-product-status.service、deploy/ozon-product-status.timer。

回退时先 `systemctl disable --now ozon-product-status.timer`，确认状态任务已退出，再恢复上述有备份的网页文件并HUP网页进程。新增状态缓存可保留；不要回滚或覆盖monitor.sqlite3。新单元原先不存在，按需要移除后daemon-reload。服务器和本地文档索引均添加本记录，未上传任何真实密钥或数据库。
