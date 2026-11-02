# 2026-11-02 WP-10 单元 1：作者动态方法契约

基线 70555a6401fa03be4b109bf58c246eff4d71fa4a，独立分支 feat/dynamic-methods；qwen 的发布源与保底专家未动。

工作稿显式 planning + 单元 selection 编译为清单 v3 / knowbench/dynamic-methods/v1；缺省仍生成原 v2，固定样例与基线编译 JSON SHA 完全一致。方法目录无 workflow，输入/验收/回退引用是方法输出槽，执行时必须由后续任务计划绑定实例及成果版本。纯结构检查拒绝未知或循环依赖、预算/次数上限不合格及混用版本。动态导入不能静默丢掉冻结约束。

规划、适用条件、必经及重复上限、依赖/工程权限/失败边界进入现有作者记录。孵化契约允许 planning/selection 建议，经本人接受才改工作稿；规划格的 method_text 是作者规划原文，不放开 AI 改写单元工程正文/advanced。父仓 schema 与 knowbench 消费锁须随提交一起交付。

验证：完整 `python3 /tmp/luna_knowlab_wp10_tests.py -q`，736 passed / 5 skipped，原始输出 /tmp/luna-wp10-lab-full-final.txt；使用本地一次性 PostgreSQL schema，未访问生产数据。覆盖作者接受、出处缺失拒绝、跨会话数据库持久化、编译往返、三方 schema/实际编译样例和固定摘要回归。第一次全量 2 failed / 732 passed：旧测试把 v3 当未知版、孵化嵌入 schema 未更新，均已修复，原输出 /tmp/luna-wp10-lab-full.txt 保留。

改动文件 ruff 与域代码 pyright 通过；全仓既有 pyright 1 / ruff 11，与隔离解包的基线逐条一致，无新增（/tmp/luna-wp10-knowlab-baseline-comparison.json）。不是全仓静态检查绿色，后续总体验收前需处理遗留项。

下一单元：knowbench 计划模型/校验/版本化存储与事务并发；之后接 Attempt、运行视图与 UI。动态运行入口尚未开放。财务专家须另孵化为作者确认的新版本，不能将合成测试样例或平台臆造方法冒充真实专家。Wind 仍 3/5 次，本单元零调用、无发布。


## 2026-11-02 WP-10 单元 1a 第一段：独立方法主档

基线 dad9b0e20a8270747cfd999bacd7dafc3b2df6b0，feat/dynamic-methods。依据所有者确认的三层关系（sunmoonun b01e7f18）：方法独立版本，专家精确引用，任务只动态组合。此段只完成作者主档与引用预览，不把预览当作专家引用落盘或跨应用执行完成。

- domain/knowbots/methods.py：从已过作者出处/编译检查的动态工作稿冻结 MethodDefinition。结构 ID 归一为 method，命名输入槽取代来源专家别名，覆盖来源/回退目标一起映射；必经是专家组合属性，定义中 required=false，正文/工具/验收/调用上限不改。输入结构来自原上游作者定义；自由文本中的上下文依赖须作者另行整理，不用模型自动重写。
- 同一 KnowlabRepository/事务保存 knowbot_method + knowbot_method_version，方法锁串行首建与升版，版本只增；请求摘要用于同版本幂等与冲突核对。定义摘要、来源工作稿/修订、作者与原单元/输入证据快照随版保存。读取精确版核摘要；数据库触发器拒绝 UPDATE/DELETE 版本。
- web端新增本人方法列表、精确读版、PUT 冻结、POST 引用预览；遵守既有身份/CSRF/owner 隔离，无管理员代确认入口。预览检查输入类型、别名、调用次数及完整候选图，只回 preview_only=true；既不改目标工作稿也不出专家版本。接口与 provider schema 说明见父仓 contracts/author-method/v1/README.md；schema 由本模型生成，尚无运行消费者。
- 迁移 20261102_0025（父版 20261101_0024），无旧数据回填；空库可退/升，存在方法身份即拒绝破坏性降级。应用回退保留扩展 schema；未迁移生产。
- 全量 PostgreSQL（独立临时 schema）：760 passed / 5 skipped，117.99 秒，/tmp/luna-method-library-full.txt。全量之后只加 3 个故障/并发测试，最终四文件 32 passed，/tmp/luna-method-library-tests-final.txt。首次新测试 26 项、接口加入后 28 项均过。未调用模型、Wind 或生产接口。
- 最终 uv run --locked ruff check app tests alembic/versions/20261102_0025_author_methods.py、uv run --locked pyright 均通过（从后端 app/ 运行；/tmp/luna-method-library-{ruff-final,pyright-final}.txt）。清理了基线的 lint 11 项（格式/导入）与 pyright 1 项：CategoryCode 改为明确 Literal、列表由 get_args 派生；与改前提交的类目取值及 DeliveryReviewIn schema 逐字比较相同，/tmp/luna-method-library-category-schema.txt。没有新增忽略规则或关闭检查。
- 初期开发命令一次目录多写 app/ 导致补丁脚本未运行，随后纠正；首次静态检查发现 Query 导入、version_view 名称碰撞、Response 返回标注与排版错误，已修，原始输出 /tmp/luna-method-library-{ruff-first,pyright-first}.txt 保留。不要把这些中间错误说成最终遗留。

下一段：保存专家 method 引用与输入绑定，作者明确选版本/确认升级，编译清单钉方法 ID/版本/摘要与编译器版；跨应用 schema/消费者锁和只读快照校验一起接。应使用此主档，不再复制一个方法库，也不能复制 preview 的 step 丢掉引用。随后才接动态运行与 UI。旧固定专家、knowmart/knowbench 代码及部署源本段未变；Wind 仍 3/5，发布归 qwen。

## 2026-11-02 agy：作者方法库14项资产入库、生命周期测试与前端装配选版接入

基线 `d1f151556f6dc6e696dd307f44758531c0d57cf1`，在本地 `~/worktrees/agy` 独立工位，子仓分支 `feat/agy-method-authoring`（父仓 `knowlab-app` 分支 `agy` 提交 `74503d3`）。全面对齐 Luna 交接文档要求，纠偏独立离线函数库与自写运行器的偏差，完全遵照 knowlab 作者主档、不可变版本与专家装配引用。

### 1. 交付成果与范围
- **生命周期与不可变性闭环（阶段 A）**：`app/tests/test_agy_author_lifecycle_closure.py`。验证作者录入并冻结 Method V1，两个独立专家（Expert A 和 Expert B）以不同本地别名同时引用绑定该 V1；方法升级冻结为 V2（digest 变化），Expert A 显式升级至 V2，Expert B 仍严格锁定在 V1；越权篡改 digest、输入映射缺漏或超出方法 `max_calls` 上限均被系统严格抛出异常拒绝。
- **全量 14 个财务与估值方法资产入库（阶段 B）**：`app/tests/financial_author_method_fixtures.py`。涵盖第一批 8 个核心财务会计方法（M01、M02、M04、M05、M06、M07、M12、M20）与第二批 6 个估值与商业洞察方法（M03、M08、M09、M14、M15、M19）。每项具备独立中立命名插槽、明确权威出处依据（CAS 30、CAS 35、审计准则1501号、Sloan应计异象、Damodaran投资估价、晨星护城河）、输入输出契约与 Checks 规则。通过 Draft 2020-12 校验器与官方 provider schema。
- **新财务专家装配与编译快照导出（阶段 C）**：`app/tests/test_financial_methods_assembly.py`。通过官方 `bind()` 将 14 个方法完整装配入财务专家，`compile_manifest` 编译输出 0 错误；生成 178KB 的 `financial-expert-manifest.json` 快照，严格对齐 `knowbench/dynamic-methods/v2` 契约，供 Luna 在 knowbench 运行侧直接加载消费。
- **前端专家装配与方法选版组件接入**：`knowlab-web-frontend`（分支 `feat/agy-method-authoring`，提交 `8cb9cfd`）。在 `app/contracts/workdraft.ts` 补齐 `planningSchema` 与 `methodReferenceSchema`；在 `app/lib/knowbots/methods.ts` 提供作者方法管理与绑定 API 客户端；在 `app/components/knowbots/workdraft-method-picker.tsx` 提供可视化分类筛选、版本与 SHA-256 digest 预览、输入槽映射绑定的装配弹窗；并在 `workdraft-step.tsx` 完成装配入口接入。通过 `pnpm typecheck`（0 错误）与全量 166 项单测。

### 2. 测试与验证数据
- **knowlab-backend 单测**：21 passed in 0.31s（包含 `test_author_methods.py`、`test_method_composition.py`、`test_agy_author_lifecycle_closure.py`、`test_financial_methods_assembly.py`）。未设临时 `DELIVERY_TEST_DATABASE_URL` 时数据库测试主动 skip，如实报告。
- **knowlab-web-frontend 单测与类型检查**：166 passed in 4.93s（26 test files），`tsc --noEmit` 零类型错误。
- **未动项**：未修改任何发布源或 Jenkins 参数；未调用任何外部付费或真实 Wind 接口（Wind 仍维持 3/5 次）；代码与提交完全隔离在本地 agy 工位，未推远程。
