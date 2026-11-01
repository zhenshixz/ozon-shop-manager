# 2026-10-30 补充：硅基流动与智谱免费模型

计划：核对官方现行价格、模型可用性、俄语支持及阿里云连通性；先给出适合标题翻译的候选，不在没有凭证时声称已比较翻译质量或已经切换。

## 核实结论
- Gemini 截图提出“通过官方免费模型 API 翻译标题”的方向可行，应纳入免费方案；此前仅推荐百度的调研范围不够全面。
- 优先候选为硅基流动的 `tencent/Hunyuan-MT-7B`。当前官方价格页输入、输出均标免费，腾讯官方模型说明明确支持俄语、中文，模型专注机器翻译。服务通过 API 托管，不在 2 GB 阿里云服务器部署模型。
- 智谱官方 2026-01-19 公告说明 `glm-4.7-flash` 免费开放，可作为备选；不照搬截图中的旧 `glm-4-flash` 型号。
- 硅基流动公告说明旧 `THUDM/glm-4-9b-chat` 已于 2026-03-17 下线。不能把所有旧模型名称和“永久免费”说明直接复制到生产配置。
- 当前标免费不等于承诺未来永久免费，也不等于不限流。硅基流动自 2026-05-15 起要求账户完成实名认证后才可使用平台功能。
- “不会乱吞型号”不是可保证的模型性质。准备接入时应携带商品类目、要求保留型号/数量/尺寸、验证数字与型号、缓存通过检查的结果；不通过则回退原文，保留人工复核译名。
- 这是候选方案评估，尚未取得 API Key，因此没有真实翻译质量、限额、账户权限的测试结果。原有服务不在本轮研究中切换。百度适配代码保留，当前运行配置保持原样。

## 官方依据
- 硅基流动现价：https://www.siliconflow.cn/pricing
- 腾讯模型语言支持：https://github.com/Tencent-Hunyuan/Hunyuan-MT
- 硅基流动下线/实名公告：https://docs.siliconflow.cn/docs/release-notes/overview
- 硅基流动接入：https://docs.siliconflow.cn/docs/userguide/quickstart
- 智谱免费发布公告：https://www.zhipuai.cn/zh/news/148
- 智谱现行模型文档：https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash

下一步依赖：用户注册硅基流动并完成所需认证、创建 API Key；随后使用积木、Moon 公仔、1/10 RC 轮架和手机电池等实际标题测试，再决定是否激活。此前已人工核实的中文标题继续保留。

## 阿里云未认证连通性探测

[
  {
    "url": "https://api.siliconflow.cn/v1/models",
    "http": 401,
    "seconds": 0.11
  },
  {
    "url": "https://open.bigmodel.cn/api/paas/v4/models",
    "http": 401,
    "seconds": 0.13
  }
]

以上只说明 HTTP 端点有响应，不等同于认证成功、模型可用或翻译效果验证。
