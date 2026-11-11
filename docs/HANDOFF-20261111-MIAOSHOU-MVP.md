# 2026-11-11 接手记录：妙手同步 MVP 与按钮优化待收尾

## 本次范围与信息来源

用户要求“记录理解但是不改任何代码”。本次仅记录交接信息并增加文档索引，不修改代码、不连接服务器、不发布、不重启服务、不修改业务数据、不创建 Git 提交或推送。下述线上状态、历史发布和验证结果来自用户交接及已有维护文档，本次未重新验证线上。

已阅读 `AGENTS.md`、`README.md`、`docs/INDEX.md`、`docs/PROJECT.md`、`docs/DEPLOYMENT.md` 和 `MAINTENANCE-20261111-MIAOSHOU-EXTENSION-MVP.md`；只读查看 Git 状态，确认存在大量既有未提交改动，保持原样。

## 项目与维护约定

- 产品：OZON 店铺助手；唯一工作仓库 `K:\github\ozon-shop-manager\`。
- 服务器 `8.138.252.219`，SSH 用户 `root`，线上目录 `/opt/ozon-push-monitor/`。连接命令、密钥位置、发布方法见仓库 `HANDOFF-GEMINI.md`；不在交接记录中复制密钥。
- Python、Flask、SQLite；前端原生 HTML/CSS/JS。快速交付，不过度验证；UI 美观统一，不明确业务先问。
- 关键改动记入本地文档；涉及服务器的改动同步服务器维护文档。实际修改前核对线上是否有其他维护者的新改动。
- 网页免登录查看、修改已获明确授权，不重新加密码。
- 真实配置、密钥、数据库、订单快照、妙手登录状态不提交 Git；不用旧数据库覆盖线上回滚。
- 不批量提交或覆盖既有未提交修改，不主动推送 GitHub，不启动冗余 Agent。

## 入口与运行约束

- 运营后台：https://voice.xzbest.site/ozonzusou/
- 订单利润：https://voice.xzbest.site/ozon-profit/
- 数据罗盘：https://voice.xzbest.site/ozon-profit/#compass
- 促销清理：https://voice.xzbest.site/ozonzusou/promotions/
- 超重排查：https://voice.xzbest.site/ozonzusou/overweight/
- 订单推送汇总间隔 20 分钟，北京时间 00:00—08:00 静默。
- 超重默认严格大于 2000g，阈值可配置，归档必须用户审核确认。
- 服务器内存 2GB；翻译模型限制 1280MiB、1 核，不随意增加资源或重启模型。

## 当前主任务：妙手采购价与重量同步浏览器插件

MVP 已开发并部署，兼容 Chrome、Edge；目标是减少采购成本与实称重量的导入导出、重复填写工作。用户安装后直接线上小批试单。

- 下载 ZIP：https://voice.xzbest.site/ozonzusou/purchase-sync/download
- 线上核对页：https://voice.xzbest.site/ozonzusou/purchase-sync/
- 源码 `extensions/miaoshou-sync/`；后端 `purchase_sync.py`，在 `profit_web.py` 注册；前端 `web/purchase-sync.html/js/css`。运营后台已有“妙手同步”菜单。
- 安装：解压 ZIP，在 Chrome/Edge 扩展管理页打开开发者模式，加载包含 `manifest.json` 的文件夹。

### 已确定的业务规则

1. 用户主动读取妙手当前页，每批选 1—5 笔。仅支持单款订单，同款多件可用；混款、数量不符、标识不明确会拦截。
2. 按店铺＋完整订单号＋商品＋数量匹配。人民币采购商品行总额除以件数得到单价，保留精度。
3. `312g`、`854克` 等明确备注代表本单整包重量，直接保存，不再乘数量；日期等普通备注不猜成重量。
4. 核对表显示当前值、新值与利润变化，采购价和重量分别勾选，确认后保存。与已有人工值不同提示冲突，默认不勾选覆盖。
5. 默认增量：相同来源值跳过，保护后续人工修改。可选“全量核对本批”，仍保留空值和冲突保护。
6. **采购价为空或 0，不覆盖旧成本，不标为同步完成，进入待补清单。再次读取到旧单时，会重新核对新补的采购价。**
7. 没有自动翻页、跨页检索或无人值守补查。旧单不在当前页时，复制待补单号到妙手搜索，再用插件读取。
8. 只保存本批选中的字段，不联动覆盖其他订单；支持带并发检查的批次撤销。
9. 插件不读取 Cookie、密码、收件资料，不需要填写 OZON Key。

### 验证边界与后续优先事项

- 已有记录：8 项隔离数据库测试、1 项 Flask 集成测试及 DOM 解析测试通过；Chrome、Edge 均实际加载扩展，完成合成妙手页面读取及向线上核对页交接数据。
- **尚未读取用户真实登录的妙手页面，不能宣称真实妙手已完全跑通。没有保存真实订单的采购价或重量。**
- 下一步优先协助用户安装，用真实 1—3 单核对读取结果；识别有问题先调整 `extensions/miaoshou-sync/reader.js`，不猜测业务字段。

### 已有记录与备份

- 本地及服务器维护文档：`MAINTENANCE-20261111-MIAOSHOU-EXTENSION-MVP.md`。
- MVP 本地提交：`a48143a`，未推送。
- 服务器备份：`/opt/ozon-backups/purchase-extension-20261111/`。
- 本地测试资料：`K:\github\diagnostics\purchase-extension-20261111\`。

## 利润页“运营后台”按钮：已发布，收尾待办

以下是用户交接的已完成事实，本次未修改或重新发布：

- 原因：入口为 `<a class="secondary">`，原样式仅作用于 `button.secondary`，导致默认紫色下划线链接。
- 已改为白底描边、统一圆角、图标和悬停效果，与“同步订单”统一为 44px 高度。
- 涉及 `web/index.html`、`web/style.css`；CSS 版本 `20261111-workspace-button`。
- 已查看桌面 1440px、手机 390px，显示正常；纯静态发布，未重启服务或修改业务数据。
- 服务器备份 `/opt/ozon-backups/workspace-button-20261111/`；本地备份及截图 `K:\github\diagnostics\workspace-button-20261111\`。

待正式继续工作时优先收尾：补专门维护文档和本地、服务器记录，创建只包含本次按钮改动及对应记录的独立本地 Git 提交，不夹带遗留修改。本文件仅保存交接理解，不代表按钮专项维护、服务器记录或提交已完成；本次不执行这些后续动作。

## 本次记录的回退

本次仅新增此交接文档，并在 `docs/INDEX.md` 增加入口。需要撤回时，只删除该文档及对应索引条目，保留索引和仓库中所有既有修改；不涉及代码或数据库回滚。
