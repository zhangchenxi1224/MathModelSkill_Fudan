"""Latest P3/P4 practice tables only; reads saved records and never dispatches."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import statistics

from report_latest_p4_practice import ROOT, HEADERS, read, sha, number, fmt, integer


def load_case(folder, case_id, problem, index):
    d = folder/'cases'/case_id
    r, a, assignment = read(d/'result.json'), read(d/'post_exit_audit.json'), read(d/'assignment.json')
    for obj in (r, a, assignment):
        if obj and (obj.get('case_id') != case_id or obj.get('problem') != problem):
            raise ValueError(f'Case identity mismatch: {case_id}')
    n, nd = a.get('source_total_post_exit'), a.get('directional_total_post_exit')
    cleared, attempts, t = r.get('clear_successes'), r.get('clear_attempts'), r.get('total_virtual_time_s')
    failed = attempts-cleared if isinstance(attempts, int) and isinstance(cleared, int) else None
    if failed is not None and (failed < 0 or r.get('failed_clear_attempts', failed) != failed):
        raise ValueError(f'Inconsistent clear counts: {case_id}')
    full = (r.get('status') == 'complete' and a.get('status') == 'complete' and isinstance(n, int)
            and n > 0 and cleared == n and a.get('cleared') == n and number(t)
            and number(a.get('total_virtual_time_s')) and abs(t-a['total_virtual_time_s']) <= 1e-5)
    return {'index': index, 'case_id': case_id, 'case_code': a.get('case_code'), 'problem': problem,
            'full_clear': full, 'state': '已全清' if full else '待完成' if not r else '未全清或等待退出记录',
            'has_result': bool(r), 'has_public_exit_record': bool(a), 'status': r.get('status'), 'error': r.get('error'),
            'N': n, 'Nd': nd, 'cleared': cleared, 'total_virtual_time_s': t,
            'average_time_per_source_s': t/n if full else None,
            'walk_distance_m': r.get('walk_distance_m'), 'measures': r.get('measures'), 'switches': r.get('switches'),
            'clear_attempts': attempts, 'failed_clear_attempts': failed, 'program_real_time_s': r.get('program_real_time_s'),
            'original_allocation_sequence': assignment.get('sequence'), 'policy_spec': r.get('policy_spec', assignment.get('policy_spec')),
            'original_log_filename': a.get('original_log_filename'), 'original_log_sha256': a.get('original_log_sha256'),
            'raw_folder': d.relative_to(ROOT).as_posix() if d.is_relative_to(ROOT) else str(d),
            'source_sha256': {p.name: sha(p) for p in (d/'assignment.json', d/'result.json', d/'post_exit_audit.json') if p.exists()}}


def summarize(rows):
    done = [r for r in rows if r['full_clear']]
    times, rates = [r['total_virtual_time_s'] for r in done], [r['average_time_per_source_s'] for r in done]
    return {'listed_cases': len(rows), 'full_clear_cases': len(done), 'mean_total_virtual_time_s': statistics.fmean(times) if times else None,
            'median_total_virtual_time_s': statistics.median(times) if times else None, 'max_total_virtual_time_s': max(times) if times else None,
            'mean_time_per_source_s': statistics.fmean(rates) if rates else None,
            'median_time_per_source_s': statistics.median(rates) if rates else None, 'max_time_per_source_s': max(rates) if rates else None,
            'time_per_source_ge900_cases': sum(v >= 900 for v in rates), 'time_per_source_ge1000_cases': sum(v >= 1000 for v in rates),
            'summary_denominator': len(done), 'scope': '均值、中位数、最大值和阈值次数只统计本组已核对全清案例'}


def cells(r):
    return [f"{r['index']:03d}", r['case_code'] or r['state'], f"{integer(r['N'])}/{integer(r['Nd'])}",
            f"{integer(r['cleared'])}/{integer(r['N'])}", fmt(r['total_virtual_time_s']), fmt(r['average_time_per_source_s']),
            fmt(r['walk_distance_m']), integer(r['measures']), integer(r['switches']), integer(r['clear_attempts']),
            integer(r['failed_clear_attempts']), fmt(r['program_real_time_s'], 3)]


def summary_text(s):
    return (f"全清 {s['full_clear_cases']}/{s['listed_cases']} 局；总虚拟时间均值 {fmt(s['mean_total_virtual_time_s'])} 秒，"
            f"中位数 {fmt(s['median_total_virtual_time_s'])} 秒，最大 {fmt(s['max_total_virtual_time_s'])} 秒。"
            f"每局 T/N 均值 {fmt(s['mean_time_per_source_s'])} 秒/源；T/N≥900秒 {s['time_per_source_ge900_cases']} 局，"
            f"≥1000秒 {s['time_per_source_ge1000_cases']} 局。")


def build(p3_folder, p4_folder, output):
    p3_folder, p4_folder, output = (Path(p).resolve() for p in (p3_folder, p4_folder, output))
    p4_scope, stop = read(p4_folder/'scope_update.json'), read(p4_folder/'stop_decision.json')
    p4_selected = p4_scope.get('selected_case_ids', [])
    # Started/completed P4 records only: omit cancelled unstarted slots entirely.
    p4_ids = [case_id for case_id in p4_selected if any((p4_folder/'cases'/case_id/name).exists()
              for name in ('result.json', 'post_exit_audit.json', 'requests.jsonl', 'session.json', 'launch_intent.json'))]
    if isinstance(stop.get('executed_case_ids'), list):
        actual_ids = set(stop['executed_case_ids'])
        p4_ids = [case_id for case_id in p4_selected if case_id in actual_ids]
    p3_plan = read(p3_folder/'plan.json')
    p3_ids = [c['case_id'] for c in p3_plan.get('cases', [])]
    if len(set(p4_ids)) != len(p4_ids) or len(set(p3_ids)) != len(p3_ids):
        raise ValueError('Duplicate selected case IDs')
    sections = []
    for problem, folder, ids, scheme in ((3, p3_folder, p3_ids, 'P3最新候选：normal / active / L1 / joint'),
                                        (4, p4_folder, p4_ids, 'P4最新方案：combined_cover，25站覆盖＋当前可行区域光学覆盖')):
        rows = [load_case(folder, case_id, problem, i) for i, case_id in enumerate(ids, 1)]
        sections.append({'problem': problem, 'scheme': scheme, 'primary_requested_cases': 15,
                         'primary_summary': summarize(rows[:15]), 'extra_summary': summarize(rows[15:]),
                         'all_recorded_summary': summarize(rows), 'primary_cases': rows[:15], 'extra_cases': rows[15:]})
    p4_note = ('P4数量收窄为15局时，已有19局完成、1局在途，最终20局全部保留。首15局作为主汇总，额外5局完整附表；未启动的10个槽位不显示、不计入统计。'
               if len(p4_ids) == 20 else 'P4按实际已启动案例保留记录，首15局作为主汇总；超出15局的实际记录单独附表，未启动槽位不显示。')
    notes = [p4_note, 'P3按本次15局计划展示，尚未完成的行明确标注。',
             'N为退出后公开目标总数，Nd为公开定向目标数；“清除”表示成功数/总数。',
             '“失败”是清除尝试失败次数，等于清除尝试减清除成功，不是整局失败数。',
             '时间单位为秒，距离为米；程序为本地客户端实际耗时，包含通信开销。未完成记录的已观测时间不计入完成耗时汇总。',
             '仅展示最新方案实际结果，不进行对照比较或区间推断；表内编号为各题本批顺序，原分配序号保留在JSON。']
    source_paths = (p3_folder/'plan.json', p4_folder/'scope_update.json', p4_folder/'stop_decision.json')
    payload = {'title': 'P3 / P4 最新方案官方演练结果', 'environment': 'official_practice',
               'sections': sections, 'notes': notes, 'p4_stop_decision': stop,
               'source_sha256': {str(p): sha(p) for p in source_paths if p.exists()},
               'script_sha256': sha(Path(__file__))}
    md = ['# P3 / P4 最新方案官方演练结果', '', *[f'- {n}' for n in notes], '']
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P3 / P4 最新方案官方演练结果</title>
<style>*{box-sizing:border-box}body{margin:0;background:white;color:#18243b;font:14px/1.65 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1740px;margin:28px auto;padding:0 24px}h1{font-size:26px}h2{font-size:22px;margin-top:32px}h3{font-size:17px}.notes{color:#536276}.summary{background:#eef4fb;border-left:4px solid #2563eb;padding:14px 18px;margin:14px 0}.extra{border-color:#b7791f;background:#fff8ec}.scroll{overflow:auto;max-height:76vh;border:1px solid #dbe3ef;border-radius:6px}table{width:100%;min-width:1400px;border-collapse:separate;border-spacing:0;font-variant-numeric:tabular-nums}th{position:sticky;top:0;background:#eaf0f8;z-index:1}th,td{padding:10px 12px;border-bottom:1px solid #e5ebf3;text-align:right;white-space:nowrap}th:nth-child(2),td:nth-child(2){text-align:left;font-family:ui-monospace,"Microsoft YaHei",monospace}tbody tr:nth-child(even){background:#f8fafc}tbody tr:hover{background:#eff6ff}.pending{color:#8d622b}@media print{.scroll{max-height:none;overflow:visible}table{min-width:0;font-size:8px}th,td{padding:4px}th{position:static}}</style><main><h1>P3 / P4 最新方案官方演练结果</h1>'''
    page += '<ul class="notes">'+''.join('<li>'+html.escape(n)+'</li>' for n in notes)+'</ul>'
    for section in sections:
        md += [f"## P{section['problem']}", '', section['scheme'], '']
        page += f"<h2>P{section['problem']}</h2><p class=\"notes\">{html.escape(section['scheme'])}</p>"
        if section['extra_cases']:
            all_text = f"全部{section['all_recorded_summary']['listed_cases']}局补充汇总（含下方额外案例）："+summary_text(section['all_recorded_summary'])
            md += [all_text, '']
            page += '<p class="notes">'+html.escape(all_text)+'</p>'
        groups = [('首15局：主统计', section['primary_cases'], section['primary_summary'], False)]
        if section['extra_cases']:
            groups.append(('额外已执行案例：完整保留，未混入首15局主统计', section['extra_cases'], section['extra_summary'], True))
        for title, rows, summary, extra in groups:
            text = summary_text(summary)
            md += [f'### {title}', '', text, '']
            page += '<h3>'+html.escape(title)+'</h3><div class="summary'+(' extra' if extra else '')+'">'+html.escape(text)+'</div>'
            if not rows:
                md += ['尚无已生成的案例记录。', '']
                page += '<p class="notes">尚无已生成的案例记录。</p>'
                continue
            md += ['| '+' | '.join(HEADERS)+' |', '| '+' | '.join(['---']*12)+' |']
            page += '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in HEADERS)+'</tr></thead><tbody>'
            for row in rows:
                values = cells(row)
                md.append('| '+' | '.join(str(v).replace('|', '\\|') for v in values)+' |')
                page += '<tr'+(' class="pending"' if not row['full_clear'] else '')+'>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>'
            md += ['']
            page += '</tbody></table></div>'
    page += '</main></html>'
    output.mkdir(parents=True, exist_ok=True)
    (output/'latest_results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output/'latest_results.md').write_text('\n'.join(md), encoding='utf-8')
    (output/'latest_results.html').write_text(page, encoding='utf-8')
    print(json.dumps({'html': str(output/'latest_results.html'), 'problems': [{
        'problem': s['problem'], 'primary_full_clear': s['primary_summary']['full_clear_cases'],
        'primary_rows': len(s['primary_cases']), 'extra_rows': len(s['extra_cases'])} for s in sections]}, indent=2))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p3', type=Path, default=ROOT/'results/p3_latest_official')
    parser.add_argument('--p4', type=Path, default=ROOT/'results/p4_refined_official')
    parser.add_argument('--output', type=Path, default=ROOT/'results/latest_practice_report')
    args = parser.parse_args()
    build(args.p3, args.p4, args.output)
