"""Build a six-case formal results table and preserve all raw artifacts."""
from pathlib import Path
import argparse
from collections import Counter
import hashlib
import html
import json
import shutil
import statistics
import zipfile

ROOT=Path(__file__).resolve().parent

def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,value): p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def records(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def f(x): return f'{x:.2f}' if isinstance(x,(int,float)) else str(x)
def cells(values): return '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>'
def table(headers,rows):
    return '<div class="scroll"><table><thead>'+cells(headers)+'</thead><tbody>'+''.join(cells(r) for r in rows)+'</tbody></table></div>'

def finish(out):
    out=Path(out).resolve()
    report=out/'report';report.mkdir(exist_ok=True)
    rows=[]
    for case in read(out/'plan.json')['cases']:
        folder=out/'cases'/case['case_id']
        r=read(folder/'result.json');a=read(folder/'post_exit_audit.json')
        req=records(folder/'requests.jsonl');dec=records(folder/'decisions.jsonl')
        outcomes=Counter(e.get('response',{}).get('measure_result') for e in req if e.get('path')=='/measure' and e.get('outcome')=='accepted')
        successes=[e['response']['virtual_time_s'] for e in req if e.get('path')=='/clear' and e.get('response',{}).get('clear_result')=='success']
        stop=r.get('stop_evidence') or {}
        cost=dict(move=r['walk_distance_m']/5,measure=5*r['measures'],switch=r['switches'],clear=3*r['clear_attempts']+2*r['clear_successes'])
        diag=dict(measurement_outcomes=dict(outcomes),costs_s=cost,
            time_after_last_clear_s=r['total_virtual_time_s']-max(successes) if successes else None,
            request_records=len(req),decision_records=len(dec),
            count_route_changes=r.get('count_route_changes',0),
            station_information_measures=r.get('station_information_measures',0),
            opportunity_search_measures=r.get('opportunity_search_measures',0),
            opportunity_search_discoveries=r.get('opportunity_search_discoveries',0),
            stop_evidence_type=stop.get('type'),original_logs=a.get('original_logs',[]))
        write(folder/'diagnostics.json',diag)
        rows.append(dict(case_id=case['case_id'],problem=case['problem'],index=case['index'],case_code=a['case_code'],
            status=r['status'],cleared=r['clear_successes'],N_public=a['source_total_post_exit'],Nd_public=a['directional_total_post_exit'],
            total_virtual_time_s=r['total_virtual_time_s'],time_per_successful_clear_s=r['total_virtual_time_s']/r['clear_successes'],
            walk_distance_m=r['walk_distance_m'],measures=r['measures'],switches=r['switches'],clear_attempts=r['clear_attempts'],
            failed_clear_attempts=r['clear_attempts']-r['clear_successes'],program_real_time_s=r['program_real_time_s'],
            completion_evidence_type=stop.get('type'),diagnostics=diag))
    sections=[]
    for p in (3,4):
        group=[r for r in rows if r['problem']==p]
        sections.append(dict(problem=p,cases=len(group),normal_completion=sum(r['status']=='complete' for r in group),
            mean_total_virtual_time_s=statistics.fmean(r['total_virtual_time_s'] for r in group),
            mean_time_per_successful_clear_s=statistics.fmean(r['time_per_successful_clear_s'] for r in group),
            mean_program_real_time_s=statistics.fmean(r['program_real_time_s'] for r in group)))
    note='正式测试界面不公开真实源总数N及定向源数Nd。表中列出成功清除数C；平均清除耗时=T/C。算法正常结束并提供覆盖/16源完成判据，不能将此字段冒充官方公开的全清率。时间单位秒，移动单位米；程序时间为客户端记录的实际运行时间。'
    payload=dict(title='最新版算法：P3、P4各三次正式测试',environment='official_formal',note=note,summary=sections,cases=rows)
    upload=out/'upload_status.json'
    histories=[]
    for p in (3,4):
        path=out/f'p{p}_history_ui.json'
        if not path.exists():continue
        snap=read(path)
        data=[v['name'] for v in snap['items'] if v['type']=='ControlType.DataItem']
        for r in rows:
            if r['problem']!=p or r['case_code'] not in data:continue
            i=data.index(r['case_code'])
            histories.append(dict(case_id=r['case_id'],case_code=r['case_code'],
                started=data[i+1],ended=data[i+2],display_size=data[i+3],status=data[i+4],
                observed_utc=snap['captured_utc']))
    if histories:
        count=sum(r['status']=='已上传' for r in histories)
        write(upload,dict(cases=histories,uploaded=count,total=len(rows),
            summary=f'{count}/{len(rows)}份正式行为日志显示已上传。P3、P4次数以保存的历史页面为准。'))
    if upload.exists():payload['upload_status']=read(upload)
    write(report/'results.json',payload)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P3/P4 正式测试结果</title><style>
*{box-sizing:border-box}body{margin:0;color:#192334;background:#fff;font:14px/1.65 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1600px;margin:30px auto;padding:0 28px 40px}h1{font-size:27px}h2{margin:30px 0 8px}p{color:#526174}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td{white-space:nowrap;padding:13px 11px;border-bottom:1px solid #e8edf3;text-align:right}thead td{font-weight:700;color:#162b49;border-bottom:2px solid #ccd8e6}td:first-child,td:nth-child(2){text-align:left}tbody tr:hover{background:#f2f7fc}.cards{display:flex;gap:18px;flex-wrap:wrap}.card{flex:1;min-width:300px;border:1px solid #d9e3ef;border-radius:12px;padding:18px 22px;background:#f7faff}.big{font-size:26px;font-weight:700;color:#165caf}a{color:#165caf}code{font-size:12px}@media print{main{margin:0;padding:0}.scroll{overflow:visible}td{padding:7px 4px;font-size:9px}}</style><main><h1>最新版算法：P3、P4各三次正式测试</h1>'''
    page+='<p>'+html.escape(note)+'</p><div class="cards">'
    for s in sections:
        page+=f'<div class="card"><b>P{s["problem"]} · 正常完成 {s["normal_completion"]}/3</b><div class="big">{s["mean_time_per_successful_clear_s"]:.2f} 秒/清除源</div><span>平均总虚拟时间 {s["mean_total_virtual_time_s"]:.2f} 秒</span></div>'
    page+='</div>'
    for p in (3,4):
        scheme='opportunity（沿用上一批验证策略）' if p==3 else '双环覆盖＋顺路搜索（ring_search）'
        page+=f'<h2>P{p}</h2><p>{scheme}</p>'
        group=[r for r in rows if r['problem']==p]
        page+=table(['序号','官方案例编码','成功清除C','总虚拟时间','平均清除耗时T/C','移动米','检测','换频','清除尝试','失败','程序秒'],
            [[f'{r["index"]:03}',r['case_code'],r['cleared'],f(r['total_virtual_time_s']),f(r['time_per_successful_clear_s']),f(r['walk_distance_m']),r['measures'],r['switches'],r['clear_attempts'],r['failed_clear_attempts'],f(r['program_real_time_s'])] for r in group])
    page+='<h2>每局耗时与信息利用</h2>'
    page+=table(['题目/序号','方位/近距离/无信号','移动秒','检测秒','换频秒','清除秒','末次清除后秒','顺路搜索检测/发现','完成判据'],
        [[f'P{r["problem"]}/{r["index"]:03}', '/'.join(str(r['diagnostics']['measurement_outcomes'].get(k,0)) for k in ('direction','near','no_signal')),
          *[f(r['diagnostics']['costs_s'][k]) for k in ('move','measure','switch','clear')],f(r['diagnostics']['time_after_last_clear_s']),
          f"{r['diagnostics']['opportunity_search_measures']}/{r['diagnostics']['opportunity_search_discoveries']}",r['completion_evidence_type']] for r in rows])
    page+='<h2>逐局原始数据</h2><p>失败列为清除尝试失败次数。全部场次均保留，没有按成绩筛选。</p><ul>'
    for r in rows:
        base='../cases/'+r['case_id']+'/'
        links=[('requests.jsonl','请求响应'),('decisions.jsonl','决策与几何'),('result.json','完整结果'),('diagnostics.json','诊断'),('post_exit_ui.json','正式结果页')]
        links += [('original_logs/'+l['filename'],'官方原始日志') for l in r['diagnostics']['original_logs']]
        page+=f'<li>P{r["problem"]}/{r["index"]:03}：'+' · '.join(f'<a href="{base}{file}">{label}</a>' for file,label in links)+'</li>'
    page+='</ul>'
    if 'upload_status' in payload:
        page+='<h2>正式日志上传状态</h2><p>'+html.escape(payload['upload_status'].get('summary','详见状态文件'))+'</p><a href="../upload_status.json">上传状态记录</a>'
    page+='</main></html>'
    (report/'results.html').write_text(page,encoding='utf-8')
    (out/'README.md').write_text('# P3/P4 正式测试\n\n打开 report/results.html 查看六局成绩、耗时诊断和原始日志链接。\n\n'+note+'\n\nsource/ 保存实际使用的算法快照；requests.jsonl 为全部请求响应，decisions.jsonl 为逐步决策和几何知识。正式测试机会已使用，不应将此运行脚本当作无限次数演练脚本重复启动。\n',encoding='utf-8')
    with (out/'README.md').open('a',encoding='utf-8') as log:
        log.write('\nconsole.log 和 exception.txt 保留了首次适配正式测试启动弹窗、返回流程时的控制程序中断记录；已结束的正式局未重跑。每局算法结果以 cases/ 下的 result.json 为准。\n')
    for name in ('run_selected_formal.py','formal_ui.ps1','formal_history_ui.ps1','finalize_formal.py'):
        shutil.copy2(ROOT/name,out/'source'/name)
    files=[p for p in sorted(out.rglob('*')) if p.is_file() and p.name not in ('collector.lock','manifest.json')]
    write(out/'manifest.json',{str(p.relative_to(out)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files})
    archive=ROOT/'delivery'/(out.name+'_complete.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files+[out/'manifest.json']: z.write(p,str(p.relative_to(out)))
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise RuntimeError('Archive integrity failed')
    print(json.dumps(dict(report=str(report/'results.html'),archive=str(archive),summary=sections,retained_logs=sum(len(r['diagnostics']['original_logs']) for r in rows)),ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True)
    finish(ap.parse_args().out)
