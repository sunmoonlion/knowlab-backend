# 2026-11-02 WP-10 单元 1：作者动态方法契约

基线 70555a6401fa03be4b109bf58c246eff4d71fa4a，独立分支 feat/dynamic-methods；qwen 的发布源与保底专家未动。

工作稿显式 planning + 单元 selection 编译为清单 v3 / knowbench/dynamic-methods/v1；缺省仍生成原 v2，固定样例与基线编译 JSON SHA 完全一致。方法目录无 workflow，输入/验收/回退引用是方法输出槽，执行时必须由后续任务计划绑定实例及成果版本。纯结构检查拒绝未知或循环依赖、预算/次数上限不合格及混用版本。动态导入不能静默丢掉冻结约束。

规划、适用条件、必经及重复上限、依赖/工程权限/失败边界进入现有作者记录。孵化契约允许 planning/selection 建议，经本人接受才改工作稿；规划格的 method_text 是作者规划原文，不放开 AI 改写单元工程正文/advanced。父仓 schema 与 knowbench 消费锁须随提交一起交付。

验证：完整 `python3 /tmp/luna_knowlab_wp10_tests.py -q`，736 passed / 5 skipped，原始输出 /tmp/luna-wp10-lab-full-final.txt；使用本地一次性 PostgreSQL schema，未访问生产数据。覆盖作者接受、出处缺失拒绝、跨会话数据库持久化、编译往返、三方 schema/实际编译样例和固定摘要回归。第一次全量 2 failed / 732 passed：旧测试把 v3 当未知版、孵化嵌入 schema 未更新，均已修复，原输出 /tmp/luna-wp10-lab-full.txt 保留。

改动文件 ruff 与域代码 pyright 通过；全仓既有 pyright 1 / ruff 11，与隔离解包的基线逐条一致，无新增（/tmp/luna-wp10-knowlab-baseline-comparison.json）。不是全仓静态检查绿色，后续总体验收前需处理遗留项。

下一单元：knowbench 计划模型/校验/版本化存储与事务并发；之后接 Attempt、运行视图与 UI。动态运行入口尚未开放。财务专家须另孵化为作者确认的新版本，不能将合成测试样例或平台臆造方法冒充真实专家。Wind 仍 3/5 次，本单元零调用、无发布。
