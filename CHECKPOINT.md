# 2026-11-02 Wind 通用查询与年报证据（开发候选，未发布）

基线 `256e726b33cd003c3151d7aefc4c79cccb0765d7`。本轮本人要求第二轨作为主演示、巨潮保底，财务与年报都纳入、不限制为财务专线。

对齐可选 source_query 契约，作者可显式选 discovery；历史 v1 编译 DEFAULT_TOOLS 保持，不把新能力偷偷写进旧版本/摘要。
全量 806 passed / 5 skipped / 1 failed，唯一失败为默认工具等于完整新词表的旧断言；修正为固定历史默认与新 discovery 显式选择后，编译/锁定回归 54 passed（/tmp/luna-wind-lab-corrected.log）。ruff/pyright 通过。agy 资产未合并，未声明真实孵化/业务验收通过。

发布由 qwen/Jenkins；不代点用户采单、不修改线上任务。以下为此前历史：

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

## 2026-11-02 WP-10 单元 3 第六段：动态测试期望与产出契约

基线 d1f151556f6dc6e696dd307f44758531c0d57cf1，接续开发分支 feat/method-execution-view。
方法主档和精确引用实现仍沿用既有代码，作者方法内容/页面归 agy；本段仅补运行测试接口。

- 动态 RunRequest 的 expectation.artifact 必须是作者声明的 final_units 别名。任务实例由
  模型之后选择，作者无需猜编号；服务在建测试与签数据授权前将不合法请求返回明确 400。
  不新增判据或测试接口版本，仍用原 equals/approx/contains。v3 运行准入仍未开放。
- case-outputs 可选 plan_version/final_outputs/步骤 current；每份最终成果核引用、内容
  与文件归属，未验成果不能附内容，重复身份拒绝。旧固定模型序列化不增加这些字段。
  父仓生成文件与 knowbench 消费锁仅改变产出 schema；request/result/answers 字节不变。
- 新增 10 项合成契约测试；与现有产出/作者服务目标测试 31 passed。全量隔离 PostgreSQL
  791 passed / 5 skipped，112.59 秒（/tmp/luna-method-test-exports-lab-full.txt）。全仓
  ruff、pyright 0 errors（/tmp/luna-method-exports-lab-{ruff,pyright}.txt）。无迁移、无真实
  模型/Wind 调用、无生产/发布操作。作者 UI 需按父仓 contracts/README.md 接多成果展示。

## 2026-11-02 WP-10 作者页面接线：列表元信息与跨端夹具

作者方法列表在同一查询中读取确切最新版本的 title/summary（概要最多 500 字），回包
仍为 items 列表；latest_version 只作提示，不改任何引用。这样页面可列本人实际方法，
不需要写死财务方法表或逐项额外请求才能显示名字。原 owner 隔离/分页/精确读版保留。
新增前端合成夹具的 provider 比较，核 WorkdraftBody 的 planning/selection、实际
MethodDefinition 文档及摘要，避免前端自己编一份看似合法的契约。

数据库定向 11 passed（/tmp/luna-author-listing-tests.txt），全量 792 passed / 5 skipped，
107.69 秒（/tmp/luna-author-ui-lab-full.txt）；全仓 ruff/pyright 0 errors，日志同前缀
lab-ruff / lab-pyright。无新迁移、无模型或 Wind 调用。冻结/绑定仍用现有 API 和作者出处
核验，没有导入 agy 财务夹具为正式方法。作者前端正在相邻子仓完成真实装配及冻结入口。


## 2026-11-02 WP-10：编译期覆盖规则兼容检查

新工作稿编译在固定/动态专家中检查 covers：源必须声明列表，source_field 必须存在，
所有目标（包括 also）必须含覆盖字段且与源类型兼容。使用实际 output_schema，错误
定位到步骤检查项。没有改运行器语义或自动补业务字段；无新契约字段、迁移。
历史引用还原使用未改变的 v1 翻译，保持冻结快照字节/摘要及篡改校验；重新保存、绑定、
冻结、测试走公开编译入口，必须满足新作者检查。真实 DB 验证错误定义不能入库，作者
修正原工作稿后可以首次冻结。非财务跨端夹具显式声明对象行，不冒充财务业务验收。

最终全量隔离 PostgreSQL：807 passed / 5 skipped（160.67 秒），日志
/tmp/luna-coverage-knowlab-full-final.log；ruff/pyright 通过，日志
/tmp/luna-coverage-{ruff,pyright}-final.log。定向编译/契约 108 passed，作者 DB 29 passed。
首次定向 5 failed / 89 passed 暴露旧夹具隐式 string 行和 golden 不匹配，已显式纠正
对象字段并通过开发生成步骤更新跨端快照，普通测试只读。首次全量 806 passed /
5 skipped / 1 failed 为前端 golden 仍旧；更新真实编译产物后最终全量通过。原始日志
保留 /tmp/luna-coverage-initial.log、/tmp/luna-coverage-knowlab-full.log。

只在独立开发分支，不涉及发布、agy 应用树、线上任务或 Wind。历史数据无需迁移；
回退应用会失去新的作者检查，但不应回写旧快照或篡改已冻结版本。
