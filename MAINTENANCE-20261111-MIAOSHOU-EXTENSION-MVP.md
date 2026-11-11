# 2026-11-11 妙手浏览器同步MVP：Chrome/Edge、线上小批确认

## 授权与入口

用户授权开发浏览器插件MVP，由用户安装后直接线上小范围试单，并要求兼容Chrome和Edge；补充默认增量、可选全量，以及没有采购价的订单后续仍需补查。此前Excel仅本地模拟的范围由本次明确授权扩展为独立线上小批接口；未把原Excel模拟服务直接上线。

- 核对页：`https://voice.xzbest.site/ozonzusou/purchase-sync/`
- 插件包：`https://voice.xzbest.site/ozonzusou/purchase-sync/download`
- 插件源码：`extensions/miaoshou-sync/`，版本0.1.0，Manifest V3。同一文件夹在Chrome/Edge开发者模式加载，无需填OZON Key。
- 每次手动读取当前页，手动选1—5笔后打开线上预览。保存需网页勾选并再次确认。MVP只支持单款订单（同款多件可用），没有自动翻页或无人值守补查。

## 新增与修改文件

- `purchase_sync.py`：输入白名单、店铺+完整订单+商品+数量匹配、差异/利润预览、增量来源记录、限5笔确认保存、字段审计与安全撤销。
- `web/purchase-sync.html/js/css`：协调现有后台风格的差异表、字段选择、冲突提示、待补清单、更新记录及撤销。
- `extensions/miaoshou-sync/`：reader.js仅从当前页面DOM提取必要字段，popup勾选，background使用浏览器session存储短期传递，bridge只向指定核对页交接。README包含两种浏览器安装步骤及边界。
- `profit_web.py`仅注册新模块；`web/operations.html`仅添加“妙手同步”菜单。
- 原采购/重量/利润计算、同款沿用、订单API、促销、超重扫描逻辑不改；主监控、模型和其他定时任务未重启。网页只HUP加载接口。
- 新增 `test_purchase_sync.py`、`test_purchase_sync_web.py`、插件 `test-reader.mjs`。

## 数据与增量语义

1. 只接收店铺名、完整posting号、货号/SKU、数量、人民币采购行总额、整包备注、识别错误信息。只匹配当前已配置店铺。未知/重复/数量不一致/混款/标识不唯一的记录不保存。
2. 采购金额按商品行总额除件数，保留原精度（如31.07/2=15.535）；空/0采购不写零或覆盖旧成本，记录待补价。重量只接受完整的正数+g/克，代表本单整包，直接保存，不再乘数量。日期等普通备注不猜测。拆包方案或组成待确认时重量跳过。
3. 默认增量以“最后已确认来源值+对应落地值”为基线，而不是仅按订单创建时间。新订单及旧订单新补/更正的值均能识别。相同来源重复读取跳过；来源未变而本地改过时保留本地值；两边都改过显示冲突并默认不勾选。
4. 第一次遇到与本地人工值不同的记录也默认提示冲突，用户可明确勾选覆盖；预览与当前值一致时可只记录核对基线，不修改业务值。未勾选的差异不会被当成已成功同步。
5. “全量核对本批”仅重新比较本次最多5笔，仍保留空值保护、冲突与确认，不扩大至未读取订单或清空缺席数据。
6. 没价格的记录始终留在待补清单；后续再次读取到该订单，正数采购价会重新进入核对。若旧单不在当前页，不会假称已自动查询：页面可复制待补价单号到妙手搜索再读取。自动翻页/自动检索旧单属于后续正式版。
7. 页面显示按勾选字段重算的整单预估利润；事务保存前校验订单字段/数量/状态/拆包及核算输入指纹。预览30分钟有效、每个批次只能保存一次；订单已改变则要求重新预览。

## 保存范围、追溯与保护

- 新表均在现有SQLite中，独立命名：purchase_sync_previews、purchase_sync_fields、purchase_sync_batches。业务字段与保存审计在同一事务提交，避免部分成功。
- 保存只直接更新勾选字段与其来源表，不调用会同款联动的 `save_order()`；来源标记为 `extension`，不把导入值当成现有全局人工沿用的新来源。MVP同步不会暗中改其他订单。
- 重量变化同时追加现有 profit_weight_audit；采购/重量均保留变更前值、来源元数据及批次。撤销仅恢复该批字段和基线，相关订单指纹已变则停止撤销，不恢复整个数据库。
- 原有免登录偏好保留；API在原 `/ozon-profit/api/` 下复用会话、CSRF、同源检查。插件不绕过这套写保护，由核对网页同源提交。没有上传妙手Cookie、密码、收件资料或OZON Key；没有放宽CORS、接入私有妙手接口或后台抓取登录态。
- 插件使用activeTab/scripting/storage与自己域名权限，不使用cookies权限或全站权限。数据只在浏览器session暂存20分钟，不写Chrome/Edge云同步；新窗口只传随机交接标识，不把订单数据放URL。

## 验证结果与实际边界

- 8项隔离数据库测试通过：金额精度、整包重量、字段选择、其他订单不变、空值/0待补及后补、增量/全量/冲突、并发数据改变、批次重放、撤销与隔离。
- 1项Flask集成测试通过：真实路由与ZIP包、会话/CSRF、预览不写业务值、按选择计算、确认保存及撤销，均使用临时库与合成订单。
- reader测试覆盖明确字段、0金额、日期备注、混款、行内数量、币种及不输出收件信息。
- Chrome151与Edge157均实际加载同一MV3插件，通过工具栏动作读取合成妙手DOM，识别3笔（混款被禁选），选2笔传递到线上核对页。使用未配置的合成Luo999店铺验证线上匹配失败拦截，保存按钮禁用；无真实金额/重量保存。
- 两浏览器1440/390核对页截图与扩展面板检查，无页面横向溢出（表格自身允许滚动）。旧利润与促销地址200，相关服务active。
- **没有访问用户真实妙手登录页面。** 当前DOM解析按可见字段与订单边界适配，真实页面仍需用户安装后选择几单确认，不能把合成DOM测试描述成已跑通真实妙手。识别失败或字段不唯一时拦截，不猜值。
- 线上新模块实际保存批次数0、来源字段数0；2条Luo999合成未匹配预览仅存在预览表，7天后随新预览清理，不进入待补价清单或真实订单。

## 备份与回退

- 服务器：`/opt/ozon-backups/purchase-extension-20261111/before/`，包含原 profit_web.py、web/operations.html、校验值与SQLite在线备份（0600）。release/保存发布源码，manifest.json记录哈希。
- 本地：仓库外 `K:\github\diagnostics\purchase-extension-20261111\`，存放部署前副本、发布包、浏览器验证脚本和截图；未保存Cookie或Key。
- 回退恢复两个原文件并HUP网页即可停用新入口/API；保留新增表与审计。不以旧数据库覆盖线上，已确认业务字段如需撤销应使用字段级撤销或逐单核对。

## 官方参考

- https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts
- https://developer.chrome.com/docs/extensions/develop/concepts/activeTab
- https://developer.chrome.com/docs/extensions/reference/api/storage
- https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/port-chrome-extension
