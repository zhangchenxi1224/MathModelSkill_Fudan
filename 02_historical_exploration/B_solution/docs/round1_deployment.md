# 官方数据驱动实验：部署、实际执行与恢复

本轮先完整发现与清除，再比较整局虚拟耗时。标准调查用于采集机制证据，与正常求解分别报告。六次正式测试没有执行。

## 已完成范围与当前状态

|部分|独立场景／案例|实际运行|状态与边界|
|---|---:|---:|---|
|首轮官方演练|160|160|每题60基线+20调查，全部全清；53959条唯一accepted请求独立审计通过|
|本地同协议机制检查|按各机制预分配|960|800主检查+160宽组成参考；全部全清，0外包真值违规|
|Stage1 追加测量上限|1000|4000|800开发世界×4臂；200确认世界×4臂。发布分析只用3200开发臂+冻结L1/L5的400确认臂|
|Stage2 无信号选点|400|760|640开发臂+120确认臂。P3确认仅原版本，P4确认两臂|
|Stage3 处理时机|400|1040|960开发臂+80确认臂。两题保留joint，确认只作原方案可靠性检查|
|第二轮新官方演练|120|120|每题30基线+30冻结候选，全部全清；34861条唯一accepted请求独立审计通过|

三阶段策略实验共1800个不同世界、5800次物理执行，全部全清且0外包真值违规。独立逐动作审计覆盖其中5400次：Stage1已发布3600次，加Stage2的760次、Stage3的1040次。其余400次是未用于选择的Stage1 L2/L3确认臂，原始结果保留，但未扩大审计或将其性能用于选择。960次机制检查另计；两类主要本地实验合计6760次，不能称作6760个独立世界。

第二轮120个冻结分配案例已全部完成，采集器正常退出；`collector_status.json` 为 `stopped_normally`。预分配首8局的操作检查通过后，沿原序列执行余下112局，未按效果换参或重分配。独立审计 `results/round2/integrity_final/overall.json` 核对120局、34861条唯一accepted请求和1542次成功清除，0问题；最大累计计时差约0.000006988秒。首轮与新验证合计280次官方演练，和旧论文四次演练仍分开计数。

5000次独立两臂bootstrap的实际结果位于 `results/round2/validation_summary.json`；每题两臂各30局、全部全清。P3基线/候选完成均值5043.29/4400.64秒，P4为11371.88/10663.89秒。区间与失败惩罚口径分别保留，不能与本地配对区间混用。单臂30局零失败的双侧Wilson95%上界仍约11.35%，未据此宣称稀有失败率已验证。

最新完整测试为276项通过，33.17秒，原始终端记录在 `results/round2/test_run_final_delivery.json`；先前273项通过的历史记录仍保留在 `test_run_delivery.json`。最终独立报告PDF已由冻结Markdown正文使用ReportLab生成，共15页，逐页实际渲染检查通过；不是LaTeX编译。新ZIP仍待最后打包与逐文件核验。

## 冻结输入与最终候选

原10个核心文件的合并SHA256为 `1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6`。基线为主动测点、三角覆盖、joint处理、L5。经验分布及NoSignal权重只影响效率选择，不改变连续域覆盖、保守位置外包、20米清除认证和停止证据。

|冻结输入／结果|路径|
|---|---|
|首轮官方分配与原基线证书|`results/round1/plan.json`、`baseline_freeze.json`、`baseline_source.zip`|
|标准调查证书|`results/round1/survey_freeze.json`|
|组成、机制选择|`results/calibration/round1/model_frozen.json`、`results/round1/mechanisms_selected.json`|
|Stage1 场景与选择|`results/round1/local/manifest.json`、`selection.json`、`confirmation/gate_decision.json`|
|Stage2 场景与选择|`results/round1/stage2/manifest.json`、`selection.json`、`confirmation/gate_decision.json`|
|Stage3 场景与选择|`results/round1/stage3/manifest.json`、`selection.json`、`confirmation/gate_decision.json`|
|新官方精度决定|`results/round1/validation_precision_decision.json`|
|最终两题候选|`results/round2/candidate.json`|
|新官方分配与冻结验证|`results/round2/plan.json`、`validation_freeze.json`、`baseline_freeze.json`|

Stage1 两题均保留L1。Stage2 P3保留normal，P4保留NoSignal；Stage3两题均保留joint，不宣称调度改进。实际Stage2、Stage3主种子分别是 **2026091207、2026091307**，原工程测试种子不复用；不得用脚本的旧默认种子重新生成本轮输入。每阶段的选择文件先于其确认执行冻结，确认只接受或拒绝已选方案。

最终候选文件保存完整每题规格、依赖SHA和选择链文件SHA。最终P3为L1+normal+joint，P4为L1+NoSignal+joint。候选由 `run_official_validation.normalize_spec` 统一解析，原基线仍从原L5配置重建。新官方案例不能复位成同一世界，因此两臂独立分析，不能套本地配对区间。

## 首轮采集与机制证据

每题、每协议以整局哈希排名分75% fit与25% development，共120/40局。标准调查固定扫全部20频道：P3 7站、P4 31站，分别140/620次；完整扫描后才按预注册种子选择两个频道，固定追加调查点，最后清除。最初四个先导局仍计入160，异步界面等待与零向量名义角字段修复均保留源码证据，未据耗时改动基线或调查坐标。

同协议机制比较的24组原统计状态全部为证据不足。特定1000/1500米端点模型由公开外包反例排除；精确哈希实现由标准1米协议失配从校准池移出。两个保留误差场没有被证明为真实机制；均匀半径、面积均匀位置和均匀朝向仍是声明假设。原始比较、确定性见证与筛选层分开保留。

## 日志、登录及恢复语义

每局保存assignment、session、请求与决策JSONL、result、公开结束统计、事后audit，以及原文件名原字节加密行为日志。N/Ndir只从可见结束界面进入事后审计，策略侧未知值保持null；不解密日志或读取隐藏数据。失败退出前的短时间不是完成时间。

第二轮首次预检发现官方界面登录已过期，**在任何案例启动、任何机器人请求之前**恢复。`results/round2/startup_incident.json` 记录当时0案例、0请求，归类为局前操作中断，没有消耗、替换或删除计划槽。候选与分配保持不变。项目配置和事件记录不存账号密码，恢复操作仅使用此前提供的凭据通过可见登录界面完成。

网络重试复用原请求ID和请求体。采集器持有操作系统锁，同一机器人不能并发运行两个采集进程；新验证同时锁住基线轮与本轮。已有完整审计的案例跳过；有result但缺audit时，只恢复同一案例的退出审计。发现悬而未决的session、请求或launch_intent时保留文件并停止，不自动用新案例替换计划槽。

## 复现与恢复命令

命令在项目根目录执行；本机使用 `D:\st_python\python.exe`，不需要GPU。以下命令只消费已有冻结输入。不要重新运行prepare、generate、freeze-selection去覆盖既有计划或选择；新的复现实验输出使用独立目录。

仅本地复现最终候选的最小入口：

```powershell
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 4 --seed 202609119901 --count 3 --output results/local_reproduction/p4_seed202609119901
```

其环境是明确声明的合成参考，不是新增官方验收或重新调参。详见 `docs/frozen_candidate_local_run.md`。

Stage1已完成。若恢复其原中断执行，使用冻结编排入口，它只生成development比较，不自动公开未选确认臂：

```powershell
& 'D:\st_python\python.exe' results/round1/local/run_frozen_stage1.py --resume
```

Stage2与Stage3如需恢复，沿用原manifest与selection，不能更改参数或将development改名为confirmation：

```powershell
& 'D:\st_python\python.exe' scripts/run_candidate_stages.py run --manifest results/round1/stage2/manifest.json --output results/round1/stage2 --partition development --workers 8 --resume
& 'D:\st_python\python.exe' scripts/run_candidate_stages.py run --manifest results/round1/stage2/manifest.json --output results/round1/stage2 --partition confirmation --selection results/round1/stage2/selection.json --workers 8 --resume
& 'D:\st_python\python.exe' scripts/run_candidate_stages.py run --manifest results/round1/stage3/manifest.json --output results/round1/stage3 --partition development --workers 8 --resume
& 'D:\st_python\python.exe' scripts/run_candidate_stages.py run --manifest results/round1/stage3/manifest.json --output results/round1/stage3 --partition confirmation --selection results/round1/stage3/selection.json --workers 8 --resume
```

独立复现可将上述 `--output` 改为新的目录，同时保留原manifest/selection。`--resume` 只对匹配当前冻结分配的已存局次生效。复现出来的时间戳和墙钟不要求逐字节相同，固定场景、动作与虚拟时间才是策略重放的依据。

第二轮官方演练预检及恢复命令如下。只有原采集进程已结束、界面处于已登录演练状态且没有悬而未决案例时才启动；现有OS锁会阻止并发。队号由操作员填入，命令不含密码：

```powershell
& 'D:\st_python\python.exe' scripts/run_official_validation.py preflight --round results/round2 --candidate results/round2/candidate.json --baseline-freeze results/round1/baseline_freeze.json
& 'D:\st_python\python.exe' scripts/run_official_validation.py run --round results/round2 --candidate results/round2/candidate.json --baseline-freeze results/round1/baseline_freeze.json --robot-id <参赛队号> --max-new 120
Get-Content results/round2/collector_status.json
```

暂停标志为 `results/round2/STOP_AFTER_CASE`，它让当前局完成后停止开新局。恢复前仅移除这一已知标志，保持全部案例文件及候选不变。`--max-new` 是本次最多执行多少尚未审计的预分配案例，不会增加总样本量。不要直接杀死正在发送动作的程序。

新官方120局及独立审计、实际两臂总结已完成，11张论文窄图保留独立PDF渲染QA。最终报告为 `output/pdf/B题数据驱动实验报告.pdf`，15页均已实际目视检查；构建记录和同名 `.qa.json` 与PDF并存。完整最终页图和逐页事实位于 `results/pipeline_summary/report_visual_qa`，含源文/PDF散列和包内相对路径。正文SHA256为 `30dcb208b95a6e21e8d94fc21e228f4f3b8d39fea6bee3b7136a7c73cd3599d5`，PDF为 `db57d78ee087ddd79099c53ae9bdd3df42aa3d565ea49a2703f232c02a3a22ed`。旧论文、旧PDF与旧ZIP保持不变。正式六次测试始终不由这些命令执行。

此部署说明与包内完成清单是打包前快照；新ZIP是否实际构建并通过逐文件核验，以ZIP同名外置 `.manifest.json` 为准，不能由此处的PDF完成状态推断。
