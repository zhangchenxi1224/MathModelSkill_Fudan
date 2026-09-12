# B 题求解程序运行说明

本项目实现“有误差的测向几何 → 主动观测 → 全域发现 → 光学清除 → 完成证书”的闭环，并提供独立本地模拟器、对照实验和官方 HTTP 客户端。它对应 2026 年 B 题《无线电干扰源的快速自动定位与清除》。

第一版完整论文正文在 [docs/paper.md](docs/paper.md)，排版版在 [output/pdf/B题研究论文.pdf](output/pdf/B题研究论文.pdf)。第一版的四次演练与后续数据驱动实验分开保存，六次正式测试尚待用户明确授权，正式成绩没有以本地数据代填。

2026-09-11新增的数据驱动流水线已完成首轮160次官方演练采集（每题60基线＋20标准调查，全部全清）、960次本地同协议检查及1800个不同场景的5800次分阶段策略运行。本轮主要本地实验合计6760次，全清且无外包真值违规。首轮官方53,959条请求、已发布的5400次策略运行共1,555,327条动作均经独立审计，无问题；另400个未用于选择的确认臂保留冻结运行器结果。276项完整测试通过，记录在 `results/round2/test_run_final_delivery.json`。

新研究报告见 [docs/data_driven_experiment_report.md](docs/data_driven_experiment_report.md)，部署命令与恢复规则见 [docs/round1_deployment.md](docs/round1_deployment.md)，推理依据见 [docs/data_driven_principles.md](docs/data_driven_principles.md)，实际执行数据表见 [results/pipeline_summary/evidence_summary.md](results/pipeline_summary/evidence_summary.md)。冻结候选为问题3的L1＋原选点＋joint、问题4的L1＋NoSignal＋joint；局部减少追加测量后仍保留覆盖、外包认证及有限光学兜底。

新官方演练验证120/120全清，每题每臂30局。候选平均整局虚拟时间分别降低12.74%（5043.29→4400.64秒）与6.23%（11371.88→10663.89秒）；候选−基线的独立bootstrap 95%区间分别为［−826.29,−452.42］与［−1117.47,−264.38］秒。收益主要来自移动和测量减少，失败清除并未减少；问题4结论针对组合策略。每臂只有30个不同案例，组成不完全平衡，不能声称所有困难案例都有收益或总体失败风险为零。实际完整指标见 [results/pipeline_summary/official_validation_metrics.csv](results/pipeline_summary/official_validation_metrics.csv)。本轮官方两阶段合计280局，全部属于演练；第一版4局单列，正式仍未执行。

## 1 先运行一个本地案例

核心求解、本地模拟器、HTTP 客户端均只依赖 Python 标准库，要求 Python ≥3.10。不需要 GPU、CUDA、模型 API 或云端算力；当前状态规模很小，额外资源更适合独立案例批量测试。图表脚本使用 matplotlib，pytest 用于测试发现和执行。

在本机 PowerShell 中：

```powershell
Set-Location -LiteralPath 'D:\2026WorkExperience\CUMCM2026Problems\B_solution'
$BPy = 'D:\st_python\python.exe'
& $BPy .\run.py local --problem 3 --seed 1 --count 10 --variant joint_triangular --output results/demo_p3
& $BPy .\run.py local --problem 4 --seed 2 --count 16 --variant joint_triangular --output results/demo_p4
```

在其他电脑将 `$BPy` 改成实际 Python 路径即可。`run.py` 会自动加入 `src`，无需先安装项目；这些命令只运行自建案例，不接触官方模拟器。

如需可编辑安装和完整研究工具：

```powershell
& $BPy -m pip install -e '.[research]'
& $BPy -m pytest -q
```

`research` 额外依赖组包含 numpy、matplotlib、pytest，供绘图和测试工具使用；它们不进入在线策略的核心依赖。只安装求解程序可用 `pip install -e .`，安装后也可使用 `bsolver local ...`。已有环境不必重复安装。

## 2 当前交付与证据

截至2026-09-11，以下内容已实际执行并保留证据。

| 内容 | 已有证据 | 能支持的结论 |
|---|---|---|
| 协议与本地模拟器 | `tests/test_protocol.py`、`tests/test_simulator.py`，38 项已分别通过 pytest 与标准库 unittest | 题面计时、重试、状态、边界及本机临时 HTTP 行为通过这些测试 |
| 几何、知识账本、候选点、覆盖 | 对应 `tests/` 文件及 `docs/geometry_proofs.md`、`q2_proofs.md`、`coverage_proofs.md` | 数学机制与数值边界可独立复核；全套最新状态由 `pytest -q` 给出 |
| Q1/Q2 实验 | `results/q12/` | Q1 反例和安全清除点；90 个合法初始场景、3 种测点策略，共 270 行结果 |
| 冻结调参集 | `results/tune/results.json` | 8案例×6策略=48次，全部全清，0外包真值违反 |
| 独立测试与压力集 | `results/test/`、`results/stress/` | 16×6=96次与8×6=48次，全部全清；全部指标和负收益案例见summary |
| 敏感性 | `results/sensitivity/`、`docs/sensitivity.md` | 42次全部全清，误差界/分箱/权重/调度/网格单因素比较 |
| 官方演练 | `results/official_practice/summary.md` | 问题3两局15/15、11/11；问题4两局16/16、14/14，真实接口与结束界面双重核对 |
| 最终版本 | `docs/version_audit.md`、`results/final_regression/` | 12次回归、4534条动作与原版本精确一致；新日志含前后状态 |
| 论文与正式测试 | `docs/paper.md`、`docs/completion_checklist.md` | 论文已完成；正式两问各三次待用户授权，原表保留“待官方测试” |

开发期双版本tune原样保存在`results/tune_development`，未用于最终消融；同种子冻结重跑的`results/tune`与test/stress共192次使用同一b3cdd5b3版本。最终仅新增前状态日志和拒绝非法角度分箱参数，版本为1b673484；12次回归的计数、路径、时间和全部动作精确一致。两份完整源码ZIP及SHA256均在`results/code_snapshots`，旧实验哈希未被回填改写。第一版当时全套97项测试通过，PDF构建器另有7项独立自测；新增流水线的完整测试以本轮保存的测试记录为准，不把旧数量称为当前测试总数。

Q2 的已运行结果如下，三个方法均未在这组合法全向场景中丢信号：

| 测点方法 | 平均移动米数 | 平均后验最小包围圆半径米数 | 两测后能认证清除的比例 |
|---|---:|---:|---:|
| `fixed` | 806.23 | 30.25 | 35.56% |
| `estimated` | 710.61 | 55.45 | 23.33% |
| `active` | 437.32 | 64.33 | 38.89% |

这反映移动与定位精度之间的取舍，不能写成 active 在所有指标上最优；也不能把两次测量后的指标当成完整任务平均清除时间。这些是明示误差配置的离散合成实验，不是官方随机分布。

## 3 目录与模块

```text
B_solution/
  run.py                         无需安装的命令行入口
  pyproject.toml                 Python 版本、依赖和安装入口
  configs/example.json           SolverConfig 示例，不含账号或凭据
  src/bsolver/
    geometry.py                  楔形交会、分类、直径、包围圆、安全清除
    knowledge.py                 各频道的观测与相容状态账本
    sensing.py                   候选观测点、响应分箱上界、光学兜底
    coverage.py                  全向七点、闭方格/三角格覆盖、开放路径
    strategy.py                  搜索定位清除闭环与完成证书
    protocol.py                  串行 HTTP/直接传输客户端及 JSONL 审计
    simulator.py                 独立自建环境，不是官方程序
    experiments.py               外部案例生成、事后真值审计、批量汇总
    cli.py                       local / batch / official 命令
  scripts/research_q12.py         Q1/Q2 数据与图表复现
  tests/                         协议、几何、知识、感知及覆盖测试
  sources/                       原题与两附件的抽取文本、题面页图
  docs/
    facts_and_api.md             题设—数学—接口—来源表及完整协议说明
    geometry_proofs.md           Q1 和安全清除的数值/几何论证
    q2_proofs.md                 Q2 候选区域与分箱证书、实验解释
    coverage_proofs.md           Q3/Q4 发现和有限清除兜底证明
    literature.md               与方法相关的一手资料
    completion_checklist.md      验证、官方结果及正文的完成核对表
  results/                       自建实验、图表、请求与决策日志
```

`experiments.py` 是外部评价器：它可以生成并保存 `case.json`，在策略结束后用真值核对清除率及外包络。`Solver` 只持有客户端和合法观测，不读取源真值。不要把 `case.json`、环境汇总或真实目标数量作为策略输入。

## 4 方法与保证对应关系

1. **位置外包络。** 以目标圆的外接多边形为初始集合，用前向 ±1.0051° 楔形及保守距离外包更新。维护包含真源的外包络，而不是把中心线交点当真值。
2. **安全清除。** 区域直径小于 40 米并不充分；计算最小包围圆，再在所有顶点的半径 19.999 米圆交集中选移动最近的安全点。发送前重新检查对整个外包络的 20 米覆盖证书。
3. **主动测点。** Q3 使用 1000 米距离保证及首个可见测点的凸扩张生成候选；对连续可能读数分箱，并扩大每个角度区间得到 direction 分支的后验半径上界。选择分数是带时间量纲的启发式，**不是已证明的总剩余时间上界**。Q4 的无信号分支仍然可能发生。
4. **全向发现。** 当前实现采用原点与半径 `900√3≈1558.85` 米的六点环，共 7 点；目标圆内任意源到至少一个检测点不超过 900 米。它与前期分析中半径 1500 米的可行七点示例有所不同，应以当前代码及证明为准。
5. **定向发现。** 保留所有与目标闭圆相交的闭网格胞元的全部顶点；完整邻格覆盖处理边界、网格线和顶点。默认三角网格边长 950 米，共 31 个顶点；方格边长 700 米，共 45 个顶点。其保证针对任意源发射方向，机器狗必须能够出目标圆域。
6. **有限清除兜底。** 局部主动观测达上限后，在首次方向形成的窄条带上使用实际 110 个光学覆盖点；其清除不依赖源朝向。失败尝试也记录排除信息。不能用仅有发现的覆盖证明冒充全清证明。
7. **正确终止。** 已成功清除 16 个不同频道，或所有已发现源已清除且每个剩余频道完成全部必检测点，才生成完成证书。清除 10 个或“目前发现的都清了”均不足够。

上述保证依赖题设误差界、合法参数、外包络保持及数值保护；有限完成构造并不保证网络和程序一定在每局现实窗口内完成。策略会检查时间预算，耗尽时报告 incomplete，而不是改变指标掩盖未完成。

## 5 策略变体与配置

`--variant` 是 CLI 当前支持的策略选择方式：

| 名称 | 测点 | 调度 | 清除点 | Q4 覆盖 |
|---|---|---|---|---|
| `baseline` | fixed | 全扫后清除 | 包围圆圆心 | 方格 |
| `active` | active | 全扫后清除 | 包围圆圆心 | 方格 |
| `nearest_safe` | active | 全扫后清除 | 最近安全点 | 方格 |
| `immediate` | active | 发现后立即服务 | 最近安全点 | 方格 |
| `joint_square` | active | 联合调度 | 最近安全点 | 方格 |
| `joint_triangular` | active | 联合调度 | 最近安全点 | 三角格 |

Q3 始终使用同一个七点覆盖，所以 `joint_square` 与 `joint_triangular` 在 Q3 的策略逻辑相同。比较两者在 Q3 的细微墙钟差异没有几何意义。Q2 独立脚本另比较 `estimated` 名义位置启发式；它不是 `--variant` 的一个完整任务选项。

`configs/example.json` 包含真实 `SolverConfig` 字段。目前 CLI **没有 `--config` 参数**；自定义配置用 Python 接口加载：

```powershell
@'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path('src').resolve()))
from bsolver.strategy import SolverConfig
from bsolver.experiments import generate_case, run_case
config = SolverConfig(**json.loads(Path('configs/example.json').read_text(encoding='utf-8')))
case = generate_case(seed=42, problem=config.problem, n=10)
result = run_case(case, config, 'results/config_demo')
print(result['status'], result['clear_successes'], result['total_virtual_time_s'])
'@ | & $BPy -
```

| 字段 | 示例值 | 含义 |
|---|---:|---|
| `problem` | 4 | 全向问题 3 或混合定向问题 4 |
| `epsilon_deg` | 1.0051 | 测向误差、两位量化与数值保护 |
| `clear_radius` | 19.999 | 工作清除半径，为真实 20 米留余量 |
| `local_measure_limit` | 5 | 单源局部观测上限，之后转入有限光学兜底 |
| `joint_detour_m` | 800 | 联合服务的名义绕行距离阈值 |
| `bin_width_deg` | 4 | 连续方向响应分箱宽度；越小通常计算更贵 |
| `radius_weight` | 2 | 后验半径在时间分数中的权重 |
| `reserve_real_s` | 5 | 保留给终止与异常处理的现实时间 |
| `coverage` / `spacing` | triangular / 950 | Q4 的覆盖胞元与间距 |

任意改大误差、网格间距或清除半径都可能破坏现有证明；不要仅为了得到更短时间修改这些参数。参数调优以单独 tune 种子进行，确认后冻结，在未使用过的 test/stress 案例上报告结果。

## 6 批量实验与 Q1/Q2 复现

开发调参，可重现首次 8 案例 ×6 策略规模：

```powershell
& $BPy .\run.py batch --split tune --cases-per-problem 4 --variants baseline,active,nearest_safe,immediate,joint_square,joint_triangular --output results/tune_frozen
```

固定策略后做独立随机测试与构造压力测试：

```powershell
& $BPy .\run.py batch --split test --cases-per-problem 8 --variants baseline,active,nearest_safe,immediate,joint_square,joint_triangular --output results/test_reproduced
& $BPy .\run.py batch --split stress --cases-per-problem 4 --variants baseline,active,nearest_safe,immediate,joint_square,joint_triangular --output results/stress_reproduced
```

`--cases-per-problem 8` 是每问 8 案例，合计 16 案例；若两策略，则运行 32 次。当前批处理是串行执行，不含自动多进程。种子基数 tune/test/stress 分别为 11000/71000/91000，再按问题编号和序号偏移。测试中交替使用 10/16 个源，轮换最小/最大/混合半径以及 deterministic/correlated/extreme 误差。stress 使用 boundary、center_and_boundary、clustered、hidden_outward_last 等构型。

自建案例可以保持同一真值和固定误差场做配对比较；官方随机案例不可假设能够重放。冻结实验时应先停止源文件编辑，为每次批处理使用新的输出目录。当前请求日志采用追加写入，而案例结果文件可能覆盖；重复使用旧目录会混合多次请求，不利于复核。

复现 Q1/Q2 数值及图表：

```powershell
& $BPy .\scripts\research_q12.py --output results/q12_reproduced --bin-width 4
```

仅计算数据、不安装绘图依赖：

```powershell
& $BPy .\scripts\research_q12.py --output results/q12_data_only --bin-width 4 --skip-plots
```

输出包含 `q1_examples.json`、`q2_cases.csv`、`q2_summary.json`、`q2_decisions.json`；有绘图依赖时再输出 `q1_geometry.png` 与 `q2_strategy_comparison.png`。第一问检查直径反例、最近安全清除点和空/无界/有界交会；第二问控制合法首次观测，策略不读取真实距离。

## 7 官方演练与正式测试

官方操作必须在模拟器界面完成登录及相应模块启动。**CLI 的 `--mode practice` 只记录运行标签，不能检查或切换官方界面当前模块。** 操作前必须确认界面确为对应问题的演练测试；若误开正式模块，即使命令写 practice，也可能消耗正式机会。`--wait-ready` 只检查 TCP 监听，不证明已经选择正确模块。

在界面准备好所需演练模块后，用实际队号替换占位符；不要将真实队号写入公开样例：

```powershell
$BTeam = '<实际参赛队号>'
& $BPy .\run.py official --problem 3 --mode practice --robot-id $BTeam --variant joint_triangular --wait-ready 90 --output results/official_practice/p3_new
```

问题 4 演练需在界面另开问题 4 演练局，再运行：

```powershell
& $BPy .\run.py official --problem 4 --mode practice --robot-id $BTeam --variant joint_triangular --wait-ready 90 --output results/official_practice/p4_new
```

必须等官方界面明确显示接口就绪再执行命令；TCP端口在空闲时也可能被保留，`--wait-ready`不构成业务就绪证明，过早调用会被重置。执行期间不要手工发第二套机器人指令、重复启动脚本或切换账号。同一账号不允许两台设备同时使用，新动作必须逐次等待响应。

**正式测试只有用户明确确认后才能运行。** 正式各问仅 3 次，开始成功即占用，中止不退回。以下命令只是操作模板，不应作为普通快速开始命令执行；须先确认要消耗哪一问的哪一次正式机会，并在官方界面选择相应正式模块：

```powershell
& $BPy .\run.py official --problem 3 --mode formal --formal-authorized --robot-id $BTeam --variant joint_triangular --wait-ready 90 --output results/official_formal/p3_run1
```

问题 4 相同但改 `--problem 4` 和独立目录。`--formal-authorized` 是程序防误触标志，不能代替用户对这次正式测试的实际确认。程序不自动点选正式模块，也不自动导出官方加密日志。

每局结束后，人工核对官方案例编码、界面完成状态和虚拟耗时；演练可核对源总数，正式不显示总数。当前官方 `result.json` 中 `case_code`、`source_total`、`clear_fraction` 保持 null，不能猜值。正式须在论文表中填写题面要求的案例编码、清除数、平均定位清除时间和程序运行时间，并原样导出相应加密日志。

按本地题面，2026-09-13 17:30 后不能新开演练/正式测试，建议 15:30 前完成正式测试；同时以官方后续通知为准。官方生成的加密日志与本项目 JSONL 是不同材料，后者不能替代前者。

## 8 如何解读结果与日志

| 文件或字段 | 含义与复核方法 |
|---|---|
| `case.json` | 仅自建案例的真值，供外部评价器复查，不应提供给策略 |
| `requests.jsonl` | 每次传输尝试的原请求、ID、响应/异常与确认状态；包含重试 |
| `decisions.jsonl` | 策略选择、可行域更新、安全证书、失败与终止理由 |
| `result.json` | 单局配置、状态、时间、清除数、完成证据；自建结果另含真值审计 |
| `results.csv` / `results.json` | 批量运行汇总，逐局保留，不能仅摘最快结果 |
| `status=complete` | 策略生成完成证书且退出已确认；自建场景还应核对真实清除比例 |
| `complete_exit_unconfirmed` | 策略完成但未确认退出；不能简单等同官方成功结束 |
| `incomplete` | 有预算、几何、协议或其他错误；必须保留并解释 |
| `stop_evidence` | 利用 16 源上界，或按频道覆盖排除的具体证据 |
| `hull_invariant_violations` | 自建事后检查真源是否始终在外包络内；任何非空结果均需排查 |
| `timing_residual_s` | 服务累计虚拟时间减独立分项计时，应只有合理微秒累计差 |

总虚拟时间包含移动、换频道、所有检测和失败/成功清除；平均定位清除时间为总虚拟时间除以成功清除数。程序现实耗时是运行墙钟，不应与虚拟时间混为一谈。平均指标必须与清除率、完成状态同时报告，避免奖励漏清困难目标。

新动作新 ID；断线重试必须保持原路径、原内容和原 ID。若 `pending_request` 非空，结果仍可能已经在服务端执行，不能换 ID 重做或盲目发 `/exit`。`accepted=false` 的零虚拟时钟也不能覆盖已确认时间。详情见 `docs/facts_and_api.md`。

`deadline` 按首次进入请求开始时刻加服务返回剩余时间计算，响应延迟与重试不会刷新预算。代码不会为了 5 秒虚拟检测主动等待 5 秒。官方端点关闭后，程序不能靠再次调用 `/exit` 查询关闭原因。

## 9 最终使用前

先按照 `docs/completion_checklist.md` 核对：固定代码和配置；完整测试通过；独立 test/stress 结果齐全；失败案例没有被过滤；官方演练与界面一致；正式机会已获授权；六份加密日志与论文表一一对应；正文确实回答全部四问。

最值得投入的改进是：在保留上述覆盖与清除条件的前提下减少总虚拟时间，并用冻结版本的独立实验说明收益。增加策略复杂度或更多计算资源，本身都不是性能证据。

## 10 汇总、论文和打包

```powershell
& $BPy .\scripts\summarize_results.py
& $BPy .\scripts\sensitivity.py --output results/sensitivity_reproduced
& $BPy .\scripts\audit_official.py
```

`summarize_results.py`读取现有tune/test/stress，输出summary与11张图；若复现用了新目录，先查看脚本`--help`指定相应根目录。`sensitivity.py`使用已有tune的四个案例，输出默认results/sensitivity；重跑请用新目录。`audit_official.py`只离线核对本次已有公开响应与结束后元数据，不发送HTTP请求，不解密日志，也不会触发新的演练。

PDF构建命令如下，中文字体默认从Windows字体目录读取，也可用`--font-dir`指定。主流程无需这些排版依赖。

```powershell
& $BPy -m pip install reportlab Pillow pypdf
& $BPy .\scripts\build_paper_pdf.py --input docs/paper.md --output output/pdf/B题研究论文_复现.pdf
```

本机已经用Codex bundled Python完成PDF构建与核验，开发用的D:\st_python环境未预装reportlab，若使用后者须先执行上述依赖安装。已生成的PDF不是LaTeX编译结果，论文可编辑源为Markdown。

自有官方请求JSONL及界面截图含实际robot_id。第一版支撑包的脚本会排除这些私有明文文件；本轮数据驱动证据包用于本机复核，保留完整原始请求、界面证据和未改名的官方加密日志，没有上传。两种包的范围不同。最终竞赛支撑包还需补入六次正式日志并由参赛队核对格式要求。

## 11 本轮冻结候选与流水线复现

上述 `run.py --variant joint_triangular` 是原L5基线。复现本轮已经冻结的候选，使用下面的本地专用入口：

```powershell
& $BPy .\scripts\run_frozen_candidate.py --candidate results/round2/candidate.json --problem 4 --seed 202609119901 --count 3 --output results/local_reproduction/p4_seed202609119901
```

`--count` 表示局数。改为问题3可运行该题冻结候选；它不会调用官方HTTP。该入口会核验核心代码及候选依赖散列，并拒绝复用已有输出目录。其参考场景不等于全部校准池，详细说明在 [docs/frozen_candidate_local_run.md](docs/frozen_candidate_local_run.md)。候选CLI另有两题N=10/16的四次实际冒烟运行，全部全清，保存在 `results/round2/candidate_local_smoke`，不与6760次主要本地实验合并。

三阶段场景清单、独立误差场、分区与确认选择顺序见 [docs/local_iteration_results.md](docs/local_iteration_results.md)。离线重建全流程汇总，不触发官方动作：

```powershell
& $BPy .\scripts\summarize_round_pipeline.py --round results/round1 --local-stage results/round1/local/development --local-stage results/round1/local/confirmation --local-stage results/round1/stage2/development --local-stage results/round1/stage2/confirmation --local-stage results/round1/stage3/development --local-stage results/round1/stage3/confirmation --validation results/round2 --output results/pipeline_summary
```

新官方验证使用冻结的独立案例分配，不能复位同一官方案例来构造配对比较。`run_official_validation.py summarize` 仅离线汇总；`run` 才操作官方演练。完整部署、续跑及中止恢复规则集中在 [docs/round1_deployment.md](docs/round1_deployment.md)。不要并行启动第二个官方采集器。

本轮15页报告在 [output/pdf/B题数据驱动实验报告.pdf](output/pdf/B题数据驱动实验报告.pdf)，可编辑源是前述Markdown；图表、页面渲染与逐页检查记录位于 `results/pipeline_summary`。官方结果的[整理展示版](results/round2/validation_summary_reviewed.md)同时展示正确表头与Wilson失败率区间；原始生成器输出保留，统计数字以JSON为准。

本轮完整材料包使用独立文件名 `output/B题数据驱动实验部署与证据.zip`。其实际生成时间、大小、SHA256和逐文件验证结果由同名 `.manifest.json` 提供；包内清单是打包前文件快照，是否完成打包以外置验证记录为准。旧 `output/B题研究与复现材料.zip` 保持不变。本轮包包含完整私有原始证据，仅在本机制作，没有上传。
