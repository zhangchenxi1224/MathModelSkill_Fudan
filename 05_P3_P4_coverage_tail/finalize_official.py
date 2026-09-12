"""Summarize preserved official logs and package the twenty practice runs."""
from pathlib import Path
import argparse
from collections import Counter
import hashlib
import html
import json
import shutil
import statistics
import zipfile
from run_selected_official import report


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def finish(out):
    out = Path(out).resolve()
    payload = report(out)
    details = []
    for section in payload['sections']:
        for row in section['cases']:
            folder = out / 'cases' / row['case_id']
            result = read(folder / 'result.json')
            audit = read(folder / 'post_exit_audit.json')
            requests = records(folder / 'requests.jsonl')
            decisions = records(folder / 'decisions.jsonl')
            measures = Counter(r.get('response', {}).get('measure_result') for r in requests if r.get('path') == '/measure' and r.get('outcome') == 'accepted')
            plans = [r['planning'] for r in decisions if r.get('event') in ('all_feedback_plan','resweep_reconsider')]
            successful = [r['response']['virtual_time_s'] for r in requests if r.get('path') == '/clear' and r.get('response', {}).get('clear_result') == 'success']
            costs = dict(move_s=result['walk_distance_m']/5, measure_s=5*result['measures'],
                         switch_s=result['switches'], clear_s=3*result['clear_attempts']+2*result['clear_successes'])
            detail = dict(case_id=row['case_id'], problem=row['problem'], case_code=row['case_code'],
                          measurement_outcomes=dict(measures), planning_calls=len(plans),
                          planning_guard_hits=sum(bool(p.get('elapsed_budget_reached')) for p in plans),
                          planning_decisions=dict(Counter(p.get('decision', {}).get('kind', p.get('reason', 'unknown')) for p in plans)),
                          http_records=len(requests), decision_records=len(decisions),
                          http_outcomes=dict(Counter(r.get('outcome', 'other') for r in requests)),
                          costs=costs, timing_residual_s=result['timing_residual_s'],
                          stop_evidence_type=(result.get('stop_evidence') or {}).get('type'),
                          time_after_last_successful_clear_s=result['total_virtual_time_s']-max(successful) if successful else None,
                          feedback_no_signal=result.get('feedback_no_signal'),
                          feedback_pruned_clear_points=result.get('feedback_pruned_clear_points'),
                          count_route_changes=result.get('count_route_changes',0),
                          resweep_checks=result.get('resweep_checks',0),
                          resweep_measures=result.get('resweep_measures',0),
                          station_information_measures=result.get('station_information_measures',0),
                          future_service_deferrals=result.get('future_service_deferrals',0),
                          count_posterior_updates=sum(r.get('event')=='count_posterior' for r in decisions),
                          original_log_retained=bool(audit.get('original_log_sha256')))
            (folder / 'diagnostics.json').write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding='utf-8')
            details.append(detail)
    by_n=[]
    for section in payload['sections']:
        for n in range(10,17):
            group=[r for r in section['cases'] if r['N']==n]
            complete=[r for r in group if r['full_clear']]
            by_n.append(dict(problem=section['problem'],N=n,cases=len(group),full_clear_cases=len(complete),
                mean_T=statistics.fmean(r['total_virtual_time_s'] for r in complete) if complete else None,
                mean_T_per_N=statistics.fmean(r['average_time_per_source_s'] for r in complete) if complete else None))
    summary = dict(title='最新版算法官方演练：P3、P4各10局', environment='official_practice',
                   selected_policies=read(out / 'selection.json')['policies'],
                   sections=payload['sections'], by_N=by_n, diagnostics=details,
                   interpretation='本次为20个新官方演练案例，未与旧算法在相同案例配对；用于检验官方可运行性和实际表现，不能单凭本次均值推断相对提速。')
    (out / 'report/summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'report/by_N.json').write_text(json.dumps(by_n, ensure_ascii=False, indent=2), encoding='utf-8')
    page_path = out / 'report/results.html'
    page = page_path.read_text(encoding='utf-8')
    note = summary['interpretation']
    page = page.replace('</h1>', '</h1><p class="sub">' + html.escape(note) + '</p>', 1)
    extra='<h2>按干扰源总数分组</h2><p class="sub">源数来自每局结束后的官方结果页。未出现的数量组显示空值；每题只有10局，不把小组均值作为稳定性能结论。</p><div class="scroll"><table><thead><tr><th>题目</th><th>N</th><th>局数</th><th>全清</th><th>平均总虚拟时间</th><th>平均时间/源</th></tr></thead><tbody>'
    for g in by_n:
        values=[f"P{g['problem']}",str(g['N']),str(g['cases']),str(g['full_clear_cases'])]
        values += [f"{g[k]:.2f}" if g[k] is not None else '—' for k in ('mean_T','mean_T_per_N')]
        extra+='<tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr>'
    extra+='</tbody></table></div>'
    extra += '<h2>诊断数据与原始日志</h2><p class="sub">各局的几何知识范围、无信号记录、候选决策、请求响应和官方原始 .jlog 均已保留。下方可直接打开对应文件。</p><div class="scroll"><table><thead><tr>'
    headers = ['题目/序号', '方位/近距离/无信号', '规划次数', '规划保护触发', '移动耗时', '检测耗时', '换频耗时', '清除耗时', '末次清除后耗时', '后验重排', '顺路补测', '暂缓判断次数', '扫掠补测', '日志']
    extra += ''.join('<th>' + h + '</th>' for h in headers) + '</tr></thead><tbody>'
    for d in details:
        base = '../cases/' + d['case_id'] + '/'
        m = d['measurement_outcomes']
        label = f"P{d['problem']}/{d['case_id'][-3:]}"
        links = ' · '.join(f'<a href="{base}{name}">{label}</a>' for name, label in [('requests.jsonl','请求'),('decisions.jsonl','决策'),('post_exit_ui.json','官方结果'),('diagnostics.json','诊断'),('original_logs/','原始日志')])
        values = [label, f"{m.get('direction',0)}/{m.get('near',0)}/{m.get('no_signal',0)}", str(d['planning_calls']), str(d['planning_guard_hits'])]
        values += [f"{d['costs'][k]:.2f}" for k in ('move_s','measure_s','switch_s','clear_s')]
        values += [f"{d['time_after_last_successful_clear_s']:.2f}" if d['time_after_last_successful_clear_s'] is not None else '—']
        values += [str(d['count_route_changes']),str(d['station_information_measures']),str(d['future_service_deferrals']),str(d['resweep_measures'])]
        extra += '<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in values) + '<td>' + links + '</td></tr>'
    extra += '</tbody></table></div>'
    page = page.replace('</main>', extra + '</main>')
    page_path.write_text(page, encoding='utf-8')
    (out / 'README.md').write_text('''# 最新反馈算法官方演练结果

打开 `report/results.html` 查看 P3、P4 各10局的完整表格、诊断统计和日志链接。

- `report/summary.json`：结构化汇总和每局诊断。
- `cases/<案例>/requests.jsonl`：全部请求、响应、时间戳和请求前后状态。
- `cases/<案例>/decisions.jsonl`：全部决策、反馈后几何范围和规划摘要。
- `cases/<案例>/result.json`：算法最终统计；`diagnostics.json`：反馈计数与成本分解。
- `cases/<案例>/ready_ui.json`、`post_exit_ui.json`：官方页面公开结果快照。
- `cases/<案例>/original_logs/`：逐字节保留的官方加密行为日志。
- `source/`：本次策略与运行脚本快照；`selection.json`：实际策略参数。
- `console.log`：整批运行控制台日志。

时间均为秒。主表“平均时间/源”为该局总虚拟时间除以源数；汇总对每局该指标取算术平均。失败列为清除尝试失败次数，不代表该局失败。程序时间含客户端通信。

本次为新生成的官方演练案例，没有同案例旧算法对照，不能直接认定相对旧版的提速幅度。所有案例均列出，不按表现筛选。

再次运行：在原项目目录执行 `D:\\st_python\\python.exe -B -X utf8 -u run_selected_official.py --out <新的输出目录>`。程序使用模拟器当前已登录队号，也可显式传入 `--robot-id`；只启动演练测试。
''', encoding='utf-8')
    for name in ('run_selected_official.py', 'finalize_official.py'):
        shutil.copy2(Path(__file__).parent / name, out / 'source' / name)
    files = [p for p in sorted(out.rglob('*')) if p.is_file() and p.name not in ('collector.lock', 'manifest.json')]
    manifest = {str(p.relative_to(out)).replace('\\','/'): dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = Path(__file__).parent / 'delivery' / (out.name + '_complete.zip')
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files + [out / 'manifest.json']:
            z.write(p, str(p.relative_to(out)))
    with zipfile.ZipFile(archive) as z:
        error = z.testzip()
        if error:
            raise RuntimeError(error)
    print(json.dumps(dict(archive=str(archive), files=len(files)+1, bytes=archive.stat().st_size,
                         summaries={s['problem']:s['summary'] for s in payload['sections']},
                         retained_original_logs=sum(d['original_log_retained'] for d in details)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    finish(parser.parse_args().out)
