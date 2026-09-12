"""Display only the selected latest-scheme P4 practice cases; no dispatch."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ['序号', '官方案例编码', 'N/Nd', '清除', '总虚拟时间（秒）', '平均时间/源（秒）',
           '移动（米）', '检测', '换频', '清除尝试', '失败', '程序（秒）']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt(value, digits=2):
    return f'{value:,.{digits}f}' if number(value) else '—'


def integer(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else '—'


def build(folder):
    folder = Path(folder).resolve()
    scope_path = folder/'scope_update.json'
    scope = read(scope_path)
    selected = scope.get('selected_case_ids')
    if not isinstance(selected, list) or len(selected) != 30 or len(set(selected)) != 30:
        raise ValueError('scope_update.json must select exactly 30 distinct latest-scheme case IDs')
    rows = []
    for index, case_id in enumerate(selected, 1):
        if not isinstance(case_id, str) or Path(case_id).name != case_id:
            raise ValueError('Invalid selected case ID')
        d = folder/'cases'/case_id
        result, audit, assignment = read(d/'result.json'), read(d/'post_exit_audit.json'), read(d/'assignment.json')
        for obj in (assignment, result):
            if obj and (obj.get('case_id') != case_id or obj.get('problem') != 4
                        or obj.get('arm') != 'combined_cover' or obj.get('stage', obj.get('split')) != 'validation'):
                raise ValueError(f'Selected record is not the latest P4 scheme: {case_id}')
        if audit and (audit.get('case_id') != case_id or audit.get('problem') != 4):
            raise ValueError(f'Public record identity mismatch: {case_id}')
        n, nd = audit.get('source_total_post_exit'), audit.get('directional_total_post_exit')
        cleared, attempts = result.get('clear_successes'), result.get('clear_attempts')
        failed = attempts-cleared if isinstance(attempts, int) and isinstance(cleared, int) else None
        if failed is not None and (failed < 0 or result.get('failed_clear_attempts', failed) != failed):
            raise ValueError(f'Clear attempt counts disagree: {case_id}')
        total = result.get('total_virtual_time_s')
        public_time = audit.get('total_virtual_time_s')
        full = (result.get('status') == 'complete' and audit.get('status') == 'complete'
                and isinstance(n, int) and n > 0 and cleared == n and audit.get('cleared') == n
                and number(total) and number(public_time) and abs(total-public_time) <= 1e-5)
        state = '已全清' if full else '待完成' if not result else '等待退出数据' if not audit else '未全清或待核对'
        row = {'index': index, 'case_id': case_id, 'case_code': audit.get('case_code'),
               'problem': 4, 'arm': 'combined_cover', 'state': state, 'full_clear': full,
               'has_result': bool(result), 'has_public_exit_record': bool(audit),
               'status': result.get('status'), 'error': result.get('error'),
               'original_allocation_sequence': assignment.get('sequence'),
               'N': n, 'Nd': nd, 'cleared': cleared, 'total_virtual_time_s': total,
               'average_time_per_source_s': total/n if full else None,
               'walk_distance_m': result.get('walk_distance_m'), 'measures': result.get('measures'),
               'switches': result.get('switches'), 'clear_attempts': attempts, 'failed_clear_attempts': failed,
               'program_real_time_s': result.get('program_real_time_s'),
               'original_log_filename': audit.get('original_log_filename'),
               'original_log_sha256': audit.get('original_log_sha256'),
               'raw_folder': d.relative_to(ROOT).as_posix() if d.is_relative_to(ROOT) else str(d),
               'source_sha256': {p.name: sha(p) for p in (d/'assignment.json', d/'result.json', d/'post_exit_audit.json') if p.exists()}}
        rows.append(row)
    complete = [r for r in rows if r['full_clear']]
    times = [r['total_virtual_time_s'] for r in complete]
    per_source = [r['average_time_per_source_s'] for r in complete]
    summary = {'selected_cases': len(rows), 'result_records': sum(r['has_result'] for r in rows),
               'public_exit_records': sum(r['has_public_exit_record'] for r in rows),
               'full_clear_cases': len(complete), 'all_30_full_clear': len(complete) == 30,
               'summary_denominator': len(complete), 'summary_scope': '已核对全清案例；未完成或未核对案例不计入完成耗时统计',
               'mean_total_virtual_time_s': statistics.fmean(times) if times else None,
               'median_total_virtual_time_s': statistics.median(times) if times else None,
               'max_total_virtual_time_s': max(times) if times else None,
               'mean_time_per_source_s': statistics.fmean(per_source) if per_source else None,
               'median_time_per_source_s': statistics.median(per_source) if per_source else None,
               'max_time_per_source_s': max(per_source) if per_source else None,
               'time_per_source_ge900_cases': sum(t >= 900 for t in per_source),
               'time_per_source_ge1000_cases': sum(t >= 1000 for t in per_source)}
    notes = ['只展示本次选定的30个P4最新方案官方演练案例，未进行比较或区间推断。',
             'N为退出后公开目标总数，Nd为公开定向目标数；“清除”显示成功数/总数。',
             '“失败”指清除尝试失败次数，等于清除尝试数减成功数；不等于整局失败数。',
             '时间单位为秒，移动单位为米；程序为本地客户端实际耗时，包含通信开销。',
             '序号为本批30局的展示顺序；原分配序号和原始目录保留在JSON。未完成局的已观测虚拟时间不冒充完成时间。']
    payload = {'title': 'P4最新方案官方演练结果', 'environment': 'official_practice',
               'scheme': 'combined_cover：25站覆盖＋当前可行区域光学覆盖；NoSignal/L1/joint',
               'scope_update_sha256': sha(scope_path), 'script_sha256': sha(Path(__file__)),
               'summary': summary, 'cases': rows, 'notes': notes}
    cells = []
    for r in rows:
        cells.append([f"{r['index']:03d}", r['case_code'] or r['state'], f"{integer(r['N'])}/{integer(r['Nd'])}",
                      f"{integer(r['cleared'])}/{integer(r['N'])}", fmt(r['total_virtual_time_s']),
                      fmt(r['average_time_per_source_s']), fmt(r['walk_distance_m']), integer(r['measures']),
                      integer(r['switches']), integer(r['clear_attempts']), integer(r['failed_clear_attempts']),
                      fmt(r['program_real_time_s'], 3)])
    summary_text = (f"全清 {len(complete)}/30 局。已全清局总虚拟时间：均值 {fmt(summary['mean_total_virtual_time_s'])} 秒，"
                    f"中位数 {fmt(summary['median_total_virtual_time_s'])} 秒，最大 {fmt(summary['max_total_virtual_time_s'])} 秒。"
                    f"每局 T/N ≥900 秒：{summary['time_per_source_ge900_cases']} 局；≥1000 秒：{summary['time_per_source_ge1000_cases']} 局。")
    markdown = ['# P4最新方案官方演练结果', '', payload['scheme'], '', summary_text, '',
                f"以下保留全部30个选定案例；目前结果记录 {summary['result_records']}/30，退出公开记录 {summary['public_exit_records']}/30。", '',
                '| '+' | '.join(HEADERS)+' |', '| '+' | '.join(['---']+['---']+['---:']*10)+' |']
    markdown += ['| '+' | '.join(str(x).replace('|', '\\|') for x in row)+' |' for row in cells]
    markdown += ['', *[f'- {note}' for note in notes], '']
    th = ''.join(f'<th>{html.escape(h)}</th>' for h in HEADERS)
    body = ''.join('<tr'+(' class="pending"' if not r['full_clear'] else '')+'>'+''.join(f'<td>{html.escape(str(c))}</td>' for c in row)+'</tr>' for r, row in zip(rows, cells))
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4最新方案官方演练结果</title><style>
*{box-sizing:border-box}body{margin:0;background:#fff;color:#172033;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
main{max-width:1700px;margin:28px auto;padding:0 24px}h1{font-size:25px;margin-bottom:8px}.sub{color:#526074}.summary{background:#f1f5f9;border-left:4px solid #2563eb;padding:16px;margin:20px 0;font-size:16px}
.table-scroll{overflow:auto;max-height:74vh;border:1px solid #dce3ed;border-radius:6px}table{border-collapse:separate;border-spacing:0;width:100%;min-width:1400px;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:#eaf0f8;color:#182b4a;z-index:1;white-space:nowrap}th,td{padding:10px 12px;border-bottom:1px solid #e5eaf1;text-align:right;white-space:nowrap}th:nth-child(2),td:nth-child(2){text-align:left;font-family:ui-monospace,"Microsoft YaHei",monospace}tbody tr:nth-child(even){background:#f8fafc}tbody tr:hover{background:#eff6ff}.pending{color:#7c5b28}.notes{color:#526074;margin-top:20px}.notes li{margin:5px 0}
@media print{main{padding:0}.table-scroll{max-height:none;overflow:visible}table{min-width:0;font-size:8px}th,td{padding:4px}th{position:static}}
</style><main>'''
    page += '<h1>P4最新方案官方演练结果</h1><p class="sub">'+html.escape(payload['scheme'])+'</p>'
    page += '<div class="summary">'+html.escape(summary_text)+'</div>'
    page += f'<p class="sub">全部30局 · 结果记录 {summary["result_records"]}/30 · 退出公开记录 {summary["public_exit_records"]}/30 · 横向滚动查看全部列</p>'
    page += '<div class="table-scroll"><table><thead><tr>'+th+'</tr></thead><tbody>'+body+'</tbody></table></div>'
    page += '<ul class="notes">'+''.join('<li>'+html.escape(n)+'</li>' for n in notes)+'</ul></main></html>'
    output = folder/'report'
    output.mkdir(exist_ok=True)
    (output/'latest_results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output/'latest_results.md').write_text('\n'.join(markdown), encoding='utf-8')
    (output/'latest_results.html').write_text(page, encoding='utf-8')
    print(json.dumps({'report': str(output/'latest_results.html'), 'summary': summary}, ensure_ascii=False, indent=2))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=Path, default=ROOT/'results/p4_refined_official')
    args = parser.parse_args()
    build(args.round)
