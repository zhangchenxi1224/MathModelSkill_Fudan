"""Make a compact table from saved official practice results; never launches a case."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import statistics

HEADERS = ['序号', '官方案例编码', 'N/Nd', '清除', '总虚拟时间', '平均时间/源',
           '移动米', '检测', '换频', '清除尝试', '失败', '程序']
SCHEMES = {
    3: '1150米覆盖环＋一次局部测向＋紧致清除覆盖＋路线重排',
    4: '两次局部测向＋1200米绕行阈值＋紧致清除覆盖＋路线重排',
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt(value, digits=2):
    return f'{value:.{digits}f}' if numeric(value) else '—'


def integer(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else '—'


def case_ids(folder, problem):
    plan = read(folder / 'plan.json')
    ids = [c['case_id'] for c in plan.get('cases', [])
           if int(c.get('problem', plan.get('problem', problem))) == problem]
    if not ids:
        for d in sorted((folder / 'cases').glob('*')):
            assignment = read(d / 'assignment.json')
            result = read(d / 'result.json')
            if assignment.get('problem', result.get('problem')) == problem:
                ids.append(d.name)
    if len(ids) != len(set(ids)):
        raise ValueError(f'Duplicate case IDs in P{problem}')
    return ids


def load_case(folder, case_id, problem, index):
    d = folder / 'cases' / case_id
    result = read(d / 'result.json')
    public = read(d / 'post_exit_audit.json')
    n = public.get('source_total_post_exit')
    nd = public.get('directional_total_post_exit')
    cleared, attempts = result.get('clear_successes'), result.get('clear_attempts')
    total = result.get('total_virtual_time_s')
    full = (result.get('status') == 'complete' and public.get('status') == 'complete'
            and isinstance(n, int) and n > 0 and cleared == n and public.get('cleared') == n)
    failed = attempts - cleared if isinstance(attempts, int) and isinstance(cleared, int) else None
    return {
        'index': index, 'case_id': case_id, 'problem': problem,
        'case_code': public.get('case_code'), 'status': result.get('status', 'pending'),
        'full_clear': full, 'N': n, 'Nd': nd, 'cleared': cleared,
        'total_virtual_time_s': total,
        'average_time_per_source_s': total / n if full and numeric(total) else None,
        'walk_distance_m': result.get('walk_distance_m'), 'measures': result.get('measures'),
        'switches': result.get('switches'), 'clear_attempts': attempts,
        'failed_clear_attempts': failed, 'program_real_time_s': result.get('program_real_time_s'),
        'error': result.get('error'), 'raw_folder': str(d),
    }


def cells(row):
    return [f"{row['index']:03d}", row['case_code'] or ('未完成' if row['status'] != 'complete' else '等待退出记录'),
            f"{integer(row['N'])}/{integer(row['Nd'])}", f"{integer(row['cleared'])}/{integer(row['N'])}",
            fmt(row['total_virtual_time_s']), fmt(row['average_time_per_source_s']),
            fmt(row['walk_distance_m']), integer(row['measures']), integer(row['switches']),
            integer(row['clear_attempts']), integer(row['failed_clear_attempts']),
            fmt(row['program_real_time_s'], 3)]


def summarize(rows):
    complete = [r for r in rows if r['full_clear']]
    def mean(field):
        values = [r[field] for r in complete if numeric(r[field])]
        return statistics.fmean(values) if values else None
    return {'cases': len(rows), 'full_clear_cases': len(complete),
            'mean_total_virtual_time_s': mean('total_virtual_time_s'),
            'mean_time_per_source_s': mean('average_time_per_source_s'),
            'mean_program_real_time_s': mean('program_real_time_s')}


def build(p3_folder, p4_folder, output):
    sections = []
    for problem, folder in [(3, Path(p3_folder)), (4, Path(p4_folder))]:
        rows = [load_case(folder, case_id, problem, i)
                for i, case_id in enumerate(case_ids(folder, problem), 1)]
        sections.append({'problem': problem, 'scheme': SCHEMES[problem],
                         'summary': summarize(rows), 'cases': rows})
    note = '时间单位：秒；移动单位：米。“失败”是清除尝试失败次数；程序时间包含客户端通信。'
    md = ['# 新方案官方演练结果', '', note, '']
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>新方案官方演练结果</title><style>
*{box-sizing:border-box}body{margin:0;background:#fff;color:#192334;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
main{max-width:1500px;margin:30px auto;padding:0 28px 36px}h1{font-size:26px}h2{font-size:22px;margin:32px 0 6px}
.sub{color:#5d6877;margin:6px 0}.summary{font-size:16px;margin:12px 0 18px}.scroll{overflow:auto}
table{width:100%;min-width:1180px;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:14px 11px;border-bottom:1px solid #edf0f4;white-space:nowrap;text-align:right}
th{border-bottom-color:#dce2ea;color:#142135}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
tbody tr:hover{background:#f5f8fb}.pending{color:#9a6500}@media print{main{padding:0;margin:0}.scroll{overflow:visible}table{min-width:0;font-size:8px}th,td{padding:6px 4px}}
</style><main><h1>新方案官方演练结果</h1>'''
    page += '<p class="sub">' + html.escape(note) + '</p>'
    for section in sections:
        s = section['summary']
        summary_text = (f"全清 {s['full_clear_cases']}/{s['cases']} 局 · 平均总虚拟时间 {fmt(s['mean_total_virtual_time_s'])} 秒"
                        f" · 平均每源时间 {fmt(s['mean_time_per_source_s'])} 秒")
        md += [f"## P{section['problem']}", '', section['scheme'], '', summary_text, '',
               '| ' + ' | '.join(HEADERS) + ' |', '| ' + ' | '.join(['---', '---'] + ['---:'] * 10) + ' |']
        page += f"<h2>P{section['problem']}</h2><p class=\"sub\">{html.escape(section['scheme'])}</p>"
        page += '<p class="summary">' + html.escape(summary_text) + '</p><div class="scroll"><table><thead><tr>'
        page += ''.join('<th>' + html.escape(h) + '</th>' for h in HEADERS) + '</tr></thead><tbody>'
        for row in section['cases']:
            values = cells(row)
            md += ['| ' + ' | '.join(str(v).replace('|', '\\|') for v in values) + ' |']
            page += '<tr' + ('' if row['full_clear'] else ' class="pending"') + '>'
            page += ''.join('<td>' + html.escape(str(v)) + '</td>' for v in values) + '</tr>'
        md += ['']
        page += '</tbody></table></div>'
    page += '</main></html>'
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    payload = {'title': '新方案官方演练结果', 'environment': 'official_practice', 'sections': sections, 'note': note}
    (output / 'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output / 'results.md').write_text('\n'.join(md), encoding='utf-8')
    (output / 'results.html').write_text(page, encoding='utf-8')
    print(json.dumps({'html': str(output / 'results.html'), 'summary': {s['problem']: s['summary'] for s in sections}}, ensure_ascii=False))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p3', type=Path, required=True)
    parser.add_argument('--p4', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.p3, args.p4, args.output)
