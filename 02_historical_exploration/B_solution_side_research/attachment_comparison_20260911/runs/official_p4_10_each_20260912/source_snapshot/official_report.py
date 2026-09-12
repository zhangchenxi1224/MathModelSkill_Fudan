"""Tables from actual official results and unchanged original logs."""
from pathlib import Path
import argparse
import hashlib
import html
import json
import statistics as st


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


LABELS = {'attachment': '附件迭代版 p4_route_rescue',
          'baseline': '当前强基线 p4_L2_d1200_w1'}
HEADERS = ['序号', '官方案例编码', 'N/Nd', '清除', '总虚拟时间/s', '平均时间/源/s',
           '移动/m', '检测', '换频', '清除尝试', '失败', '程序/s']


def report(out):
    plan = read(out / 'plan.json')
    rows = []
    for case in plan['cases']:
        folder = out / 'cases' / case['case_id']
        if not (folder / 'post_exit_audit.json').exists():
            continue
        a, r = read(folder / 'post_exit_audit.json'), read(folder / 'result.json')
        requests = [json.loads(s) for s in (folder / 'requests.jsonl').read_text(encoding='utf-8').splitlines() if s]
        decisions = [json.loads(s) for s in (folder / 'decisions.jsonl').read_text(encoding='utf-8').splitlines() if s]
        accepted = [x for x in requests if x.get('outcome') == 'accepted']
        measures = [x for x in decisions if x['event'] == 'measure']
        last_clear = max([x['virtual_time_s'] for x in decisions
                          if x['event'] == 'clear' and x.get('response', {}).get('clear_result') == 'success'] or [0])
        row = {**case, 'case_code': a['case_code'], 'N': a['source_total_post_exit'],
               'Nd': a['directional_total_post_exit'], 'cleared': a['cleared'], 'status': r['status'],
               'total_virtual_time_s': r['total_virtual_time_s'],
               'average_time_per_source_s': r['average_clear_time_s'],
               'walk_distance_m': r['walk_distance_m'], 'measures': r['measures'],
               'switches': r['switches'], 'clear_attempts': r['clear_attempts'],
               'clear_failures': r['clear_attempts'] - r['clear_successes'],
               'program_real_time_s': r['program_real_time_s'],
               'tail_after_last_clear_s': r['total_virtual_time_s'] - last_clear,
               'last_successful_clear_virtual_s': last_clear,
               'feedback_counts': {v: sum(x['response']['measure_result'] == v for x in measures)
                                   for v in ('direction', 'near', 'no_signal')},
               'local_no_signal_count': sum(x['response']['measure_result'] == 'no_signal' and
                                            x.get('reason') != 'global_coverage' for x in measures),
               'request_records': len(requests), 'accepted_requests': len(accepted),
               'decision_records': len(decisions),
               'stop_evidence': r['stop_evidence'], 'policy_stats': r,
               'original_log_filename': a['original_log_filename'],
               'original_log_sha256': a['original_log_sha256'], 'folder': str(folder)}
        row['movement_time_s'] = row['walk_distance_m'] / 5
        row['measurement_time_s'] = row['measures'] * 5
        row['switch_time_s'] = row['switches']
        row['clear_time_s'] = row['clear_failures'] * 3 + row['cleared'] * 5
        row['accounting_residual_s'] = row['total_virtual_time_s'] - sum(row[k] for k in (
            'movement_time_s', 'measurement_time_s', 'switch_time_s', 'clear_time_s'))
        original = folder / 'original_logs' / (a['original_log_filename'] or 'MISSING')
        row['original_log_retained'] = original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest() == a['original_log_sha256']
        rows.append(row)
    summaries = {}
    for arm in LABELS:
        group = [x for x in rows if x['arm'] == arm]
        if not group:
            continue
        summaries[arm] = {'cases': len(group), 'complete_cases': sum(x['cleared'] == x['N'] and x['status'] == 'complete' for x in group),
                          'total_sources': sum(x['N'] for x in group), 'total_directional': sum(x['Nd'] for x in group),
                          'total_cleared': sum(x['cleared'] for x in group)}
        for key in ['total_virtual_time_s', 'average_time_per_source_s', 'walk_distance_m',
                    'measures', 'switches', 'clear_attempts', 'clear_failures', 'program_real_time_s',
                    'movement_time_s', 'measurement_time_s', 'switch_time_s', 'clear_time_s',
                    'tail_after_last_clear_s', 'local_no_signal_count']:
            summaries[arm]['mean_' + key] = st.mean(x[key] for x in group)
        summaries[arm]['median_total_virtual_time_s'] = st.median(x['total_virtual_time_s'] for x in group)
        summaries[arm]['max_total_virtual_time_s'] = max(x['total_virtual_time_s'] for x in group)
    payload = {'environment': 'official_practice', 'plan': plan, 'summaries': summaries, 'cases': rows}
    if len(summaries) == 2:
        a = summaries['attachment']['mean_total_virtual_time_s']
        b = summaries['baseline']['mean_total_virtual_time_s']
        payload['attachment_minus_baseline_mean_s'] = a - b
        payload['attachment_vs_baseline_percent'] = 100 * (a / b - 1)
    (out / 'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# P4 官方演练：两方案各10局', '',
             '新官方案例按10个区组交错运行，每个区组内随机决定先后。各局独立，无法复位配对。没有运行正式测试。', '',
             '|方案|完成局数|清除总数|平均整局/s|平均每源/s|最慢整局/s|', '|---|---:|---:|---:|---:|---:|']
    for arm, s in summaries.items():
        lines.append(f"|{LABELS[arm]}|{s['complete_cases']}/{s['cases']}|{s['total_cleared']}/{s['total_sources']}|{s['mean_total_virtual_time_s']:.2f}|{s['mean_average_time_per_source_s']:.2f}|{s['max_total_virtual_time_s']:.2f}|")
    tables = []
    for arm in LABELS:
        table = []
        for r in sorted((x for x in rows if x['arm'] == arm), key=lambda x: x['block']):
            table.append([f"{r['block']:03d}", r['case_code'], f"{r['N']}/{r['Nd']}",
                          f"{r['cleared']}/{r['N']}", f"{r['total_virtual_time_s']:.2f}",
                          f"{r['average_time_per_source_s']:.2f}", f"{r['walk_distance_m']:.2f}",
                          str(r['measures']), str(r['switches']), str(r['clear_attempts']),
                          str(r['clear_failures']), f"{r['program_real_time_s']:.3f}"])
        lines += ['', '## ' + LABELS[arm], '', '|' + '|'.join(HEADERS) + '|',
                  '|' + '|'.join(['---'] * len(HEADERS)) + '|']
        lines += ['|' + '|'.join(t) + '|' for t in table]
        tables.append((LABELS[arm], table))
    lines += ['', 'N=事后官方目标总数，Nd=事后官方定向源数量；平均每源为各局T/N的算术平均。程序时间包含逐请求和额外状态日志开销。', '',
              '两组使用不同案例，10局均值差仅描述本批结果，不单凭它判定稳定优劣。', '',
              '每局保留 requests.jsonl（请求、响应、动作前后状态）、decisions.jsonl（选点、清除证据、区域、路线）、observable_states.jsonl（每次决策的单调时钟与全频道状态增量）、final_observable_state.json、result.json、演练前后界面、未改名原始 .jlog、进程输出。', '',
              'source_snapshot保存实际执行源码；source_manifest.json保存哈希。results.json包含每局额外诊断字段和全部终止证据。']
    (out / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    body = '<h1>P4 官方演练结果</h1><p>两方案各10局；独立新案例，区组内随机先后。N/Nd为退出后官方公布数据。</p>'
    for label, table in tables:
        body += '<h2>' + html.escape(label) + '</h2><div><table><thead><tr>' + ''.join('<th>' + html.escape(h) + '</th>' for h in HEADERS) + '</tr></thead><tbody>'
        body += ''.join('<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in t) + '</tr>' for t in table)
        body += '</tbody></table></div>'
    page = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>P4官方演练结果</title><style>body{font:15px system-ui;margin:32px;color:#18202a}div{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}td,th{padding:12px;text-align:right;border-bottom:1px solid #eee}th{background:#f4f6f8}td:nth-child(2),th:nth-child(2){text-align:left}h2{margin-top:36px}</style>' + body + '</html>'
    (out / 'results.html').write_text(page, encoding='utf-8')
    print(json.dumps({'summaries': summaries, 'report': str(out / 'results.md')}, ensure_ascii=False), flush=True)
    return payload


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('out', type=Path)
    report(p.parse_args().out.resolve())
