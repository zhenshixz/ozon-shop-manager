# OZON 利润工具维护记录：同货号实测重量自动沿用（2026-11-05）

## 1. 背景与目标
采购单价已在上一版本实现了同货号自动沿用（`采购价沿用`）。本次将**实测计费重量**对齐为完全一致的极简自动沿用体验：当同款商品已有人工核对过的实测重量时，后续及未单独设重量的同货号订单**直接自动带入实测重量（按件数自动换算）并默认勾选「按实际称重修改」**，无需任何额外面板或手动二次确认；如需恢复 API 重量，取消勾选即可。

## 2. 核心改动说明

### 2.1 后端改动（`profit_core.py` & `profit_web.py`）
1. **实测重量来源追踪表（`profit_weight_sources`）**
   - 新增 `profit_weight_sources(order_id, origin, source_order_id, recorded_at, source_weight_g)`，区分人工核对重量（`manual`）与自动沿用重量（`inherited`）。
2. **同货号实测重量自动沿用（`latest_measured_weight` & `inherit_measured_weights`）**
   - 在订单同步（`capture_postings`）、人工保存订单（`save_order`）及服务启动时自动执行。
   - 针对未填自定义重量且未手动退回 API 重量的单品有效订单，自动查找同店同 `offer_id`（无货号回退 `sku`）最近一次人工确认的实测重量，按 `单件实测重量 = 历史实测总重 ÷ 历史件数` 自动乘以当前订单件数写入 `weight_override`，并标记 `weight_origin = 'inherited'` 与来源订单号 `weight_source_posting`。
   - 若用户主动取消勾选「按实际称重修改」恢复 API 重量并保存，系统记录 `origin = 'manual'`，后续同步不会再次覆盖该订单。

### 2.2 前端交互改动（`web/index.html`、`web/app.js`、`web/style.css`）
1. **与「采购价沿用」完全一致的极简交互**
   - 移除了底部冗余的批量同步面板。
   - 打开订单详情时，已沿用（或跨店匹配到）历史实测重量的订单**直接填入换算后的实测克重**，**默认勾选 `☑ 按实际称重修改`**，并显示浅蓝提示标签：`实测重量沿用 · 来源 XXX，取消勾选可恢复 API 重量`（无需重复弹出黄色二次确认框）。
   - 若不想使用沿用的实测重量，直接取消勾选 `☐ 按实际称重修改` 即可恢复 API 重量。
   - 订单列表卡片同步展示安静的 `实测重量沿用` 标记。

## 3. 备份与测试验证
- **服务器修改前备份目录**：`/opt/ozon-backups/batch-cost-weight-20261105/before/`
- **静态资源版本更新**：`style.css?v=20261105-weight-reuse`、`app.js?v=20261105-weight-reuse`
- **自动化测试验证**：`/opt/ozon-profit-runtime/bin/python -m unittest test_purchase_reuse.py test_profit.py -v` 共 31 项测试全部通过。
