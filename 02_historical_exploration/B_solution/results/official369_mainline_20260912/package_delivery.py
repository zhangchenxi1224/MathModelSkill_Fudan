from pathlib import Path
import json, statistics, zipfile

ROOT=Path(__file__).resolve().parent

def read(rel):
    return json.loads((ROOT/rel).read_text(encoding='utf-8-sig'))

def main():
    release=read('release.json')
    fidelity=read('report/fidelity_summary.json')
    conf=read('runs/confirmation/results.json')
    stages={name:read(f'runs/{name}/results.json') for name in ['protocol_checks','development','confirmation']}
    lines=['# 369 → 960 → 240：本轮交付','',
        '本轮使用已有369局官方演练记录更新模型判断，完成320次同协议校准对照、960个场景的3460次开发运行，以及240个全新场景的480次确认运行。三个阶段全部运行均完整清除。未新增官方演练或正式测试。','',
        '## 新场景确认结果','',
        '下表每行60个新场景，基线与候选共享同一隐藏环境及固定误差场；各耗时为整局总虚拟耗时的平均值。H1作为主要拟真参考，H2作为敏感性对照，不能将两者混合均值称为官方预期成绩。','',
        '| 题目 | 模型 | 候选 | 场景全清 | 基线秒/局 | 候选秒/局 | 降低比例 | 节省秒/局及配对95%区间 | 变慢场景 | 最大变慢秒 |',
        '|---|---|---|---|---|---|---|---|---|---|']
    for p in [3,4]:
        v=release['recommended'][str(p)]
        for h in ['H1','H2']:
            rows=fidelity['confirmation']
            b=next(x for x in rows if x['problem']==p and x['model']==h and x['variant']=='main')
            c=next(x for x in rows if x['problem']==p and x['model']==h and x['variant']==v)
            lo,hi=c['ci95_delta_s']
            gain=-c['mean_delta_s'];pct=gain/b['mean_total_s']*100
            lines.append(f"| P{p} | {h} | {v} | {c['complete']}/{c['n']} | {b['mean_total_s']:.2f} | {c['mean_total_s']:.2f} | {pct:.2f}% | {gain:.2f} [{-hi:.2f}, {-lo:.2f}] | {c['regressions']}/{c['n']} | {c['worst_delta_s']:.2f} |")
    lines+=['','## 模型更新的实际结论','',
        '- 369局包含P3的172局、P4的197局；按官方案例编码去重。全部369局的经验平滑组成模型已保存。精确目标位置、半径、朝向及误差场没有从官方数据中恢复。',
        '- 旧120局始终用于开发；从其余249局中整局保留49局检验组成模型。更新平滑模型优于旧模型，但P4的宽联合模型在这49局中的27局P4记录上更好：负对数似然4.6022，对比更新平滑5.5333，差值95%区间[0.1469,1.7621]。因此新240采用数据比较后选中的宽联合组成（N均匀10—16，P4的Nd在给定N后均匀0—N）。P3两组成模型差异尚不明确。此选择在候选性能选择及确认集生成之前作出。',
        '- 320次同协议对照中，H2在两题、两协议上的定位区域收缩偏快；H1更接近观测，因此H1作为主要参考，H2保留敏感性。未通过调半径、误差幅度来硬匹配平均耗时。',
        '- 原960场景保持不变。旧P4场景只覆盖更新组成概率约56%，重加权有效样本量仅约20—22，故在查看候选性能之前改用H1普通配对均值排名，并要求H2不退步。旧P4加权值只用于描述。',
        '- 49局已用于模型开发判断，不能再称作最终独立官方验收集。新的240是合成确认集，不能代替新的官方案例。','',
        '## 本轮保留方案','',
        '- P3：compact1150，1150米搜索环，紧致光学覆盖、基于位置外包的处理调度与未访问路线重排。',
        '- P4：merged25_l1，保留主线25点覆盖，合入紧致光学覆盖和处理/路线调度，追加测量上限1次。该组合的总收益不能全部归因于单一模块。',
        '- 开发集中的单次原地补测平均节省49.36秒，可保留为后续小改进线索；本轮未与胜出组合叠加。31点L2整套方案平均反而增加530.36秒，未入选。',
        '- 全部新确认场景中，P3有8/120局变慢，最大406.29秒；P4有34/120局变慢，最大1599.78秒。退步案例及其动作日志完整保留。','',
        '## 文件和运行','',
        '- [完整逐局表](results.html)：开发与确认的所有运行，含N/Nd、清除数、虚拟时间、平均时间/源、移动、检测、换频、清除尝试及程序时间。',
        '- [按模型汇总表](mainline_results.html)；[原始统计JSON](fidelity_summary.json)。',
        '- [校准结论](../calibration/REPORT.md)；[官方369记录索引](../calibration/official369.json)。',
        '- [旧960开发场景](../data/development960.json)；[新240确认场景](../data/confirmation240.json)。',
        '- [README](../README.md)；[部署入口](../solve.py)；[已冻结推荐配置](../release.json)。',
        '- 原始请求与决策日志位于 ../runs/，均按实际执行保存。本轮单局部署检查P3/P4各一次均成功；只修复了新入口的参数绑定，未改变批量试验中冻结的策略。',
        '', '压缩包包含代码、冻结模型/场景、汇总及逐局结果；约579 MiB的全量本地原始日志保留在原实验目录runs/，未重复装入轻量交付包。']
    (ROOT/'report/DELIVERY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    counts={k:{'runs':len(v),'complete':sum(bool(x['complete']) for x in v),'hull_violations':sum(len(x.get('hull_invariant_violations',[])) for x in v)} for k,v in stages.items()}
    completion={'official_unique_input_cases':369,'official_new_executions':0,'development_unique_worlds':960,'confirmation_unique_worlds':240,'executed_stages':counts,'recommended':release['recommended'],'official_transfer':'待新官方案例验证','deployment_checks':{'P3':'user_runs/package_smoke_fixed/result.json','P4':'user_runs/package_smoke_p4/result.json'},'initial_deployment_check':'Failed before actions due to unsupported arena_id constructor argument; wrapper fixed; frozen policy unchanged.'}
    (ROOT/'completion.json').write_text(json.dumps(completion,ensure_ascii=False,indent=2),encoding='utf-8')
    p=ROOT/'README.md';s=p.read_text(encoding='utf-8-sig')
    s=s.replace('详见calibration/generation_model.json（本目录下为generation_model.json）。','详见 calibration/generation_model.json。')
    if '## 已部署入口' not in s:
        s+='''\n\n## 已部署入口\n\n最终结论见 `report/DELIVERY.md`，逐局明细见 `report/results.html`。推荐方案由 release.json 指定。\n\n```powershell\nSet-Location -LiteralPath 'D:/2026WorkExperience/CUMCM2026Problems/B_solution/results/official369_mainline_20260912'\nD:/st_python/python.exe -X utf8 -u solve.py --local-case data/confirmation240.json --case-index 0\nD:/st_python/python.exe -X utf8 -u solve.py --local-case data/confirmation240.json --case-index 120\n```\n\n上面分别为P3/P4的本地演示。原始请求、决策与结果保存到自动创建的user_runs子目录，策略只能通过客户端读取观测。\n\nHTTP入口已接入已有适配器，本轮没有调用；只有用户决定开启相应官方演练后才执行：\n\n```powershell\nD:/st_python/python.exe -X utf8 -u solve.py --http --problem 4 --robot-id '<用户配置的队号>'\n```\n\n默认地址http://127.0.0.1:2026，arena_id固定default。不会保存登录密码或自动启动正式测试。\n\n### 数据包复现口径\n\n压缩包保留冻结场景和模型判断的产物，因此解压后可直接复跑本地开发与确认，不必重新抓取官方记录。`run_all.ps1`会跳过已完成且与代码/场景匹配的任务；要从零复跑，可将轻量包解压至新的目录后执行（包内没有逐案例缓存，因此会实际重跑）。Python路径可在run_all.ps1中按本机位置调整。\n\n从官方原始文件重新执行prepare需要原项目的绝对路径；本轮数据驱动的模型选择与规则修订分别在calibration/和selection_amendment_preselection.json中保存。已冻结流程应使用select_fidelity.py的select/finalize，不能改回run_pipeline.py内旧的select。\n\n轻量包不重复收录runs/cases中的全量压缩动作日志，日志在本目录runs/。可运行 `D:/st_python/python.exe -X utf8 package_delivery.py` 重新生成交付摘要和轻量包。\n'''
    p.write_text(s,encoding='utf-8')
    files=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in {'.py','.json','.md','.ps1'}]
    for folder in ['vendor','side_methods','calibration','data','report']:
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
    for stage in ['smoke','protocol_checks','development','confirmation']:
        folder=ROOT/'runs'/stage
        files += [p for p in folder.glob('*.json') if p.is_file()]
    files += [ROOT/'user_runs'/x/'result.json' for x in ['package_smoke_fixed','package_smoke_p4']]
    archive=ROOT/'mainline_369_960_240_bundle.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(set(files)):z.write(p,p.relative_to(ROOT).as_posix())
    print(json.dumps({'completion':completion,'package':str(archive),'package_MiB':round(archive.stat().st_size/1024**2,2),'files':len(set(files))},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
