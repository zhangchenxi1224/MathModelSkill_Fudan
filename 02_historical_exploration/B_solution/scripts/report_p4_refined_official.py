"""Read-only report from freshly completed P4 practice cases, never dispatches."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parents[1]
ARMS = ('current', 'combined_cover')
LABELS = {'current': '原候选 NoSignal/L1/joint', 'combined_cover': '新组合 25站＋当前区域光学覆盖'}
BOOTSTRAP_SEED = 2026091137
BOOTSTRAP_DRAWS = 10000
FAILURE_PENALTY_S = 360000.


def path_label(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def fmt(value, digits=2):
    return f'{value:.{digits}f}' if value is not None else '无完整案例'


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def wilson(failures, n):
    if not isinstance(n, int) or n < 1 or not isinstance(failures, int) or not 0 <= failures <= n:
        raise ValueError('Wilson interval requires valid counts')
    z = statistics.NormalDist().inv_cdf(.975)
    p, denominator = failures/n, 1+z*z/n
    center = (p+z*z/(2*n))/denominator
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denominator
    return [max(0., center-half), min(1., center+half)]


def independent_failure_difference(f0, n0, f1, n1):
    """New minus current, independent Wilson-score (Newcombe) interval."""
    p0, p1 = f0/n0, f1/n1
    l0, u0 = wilson(f0, n0)
    l1, u1 = wilson(f1, n1)
    difference = p1-p0
    return {'difference': difference,
            'ci95': [max(-1., difference-math.sqrt((p1-l1)**2+(u0-p0)**2)),
                     min(1., difference+math.sqrt((u1-p1)**2+(p0-l0)**2))],
            'method': 'independent Wilson-score difference; no degenerate zero-failure bootstrap'}


def accepted_feedback(events):
    """Keep each confirmed request once; reject missing/conflicting identities."""
    confirmed, fingerprints, successful, cleared = {}, {}, [], set()
    outcomes, failures, retries, clear_attempts = Counter(), [], 0, 0
    for e in events:
        req, reply = e.get('request') or {}, e.get('response') or {}
        identity = req.get('request_id')
        retries += int(e.get('attempt', 1) > 1)
        if identity is not None:
            fingerprint = json.dumps([e.get('path'), req], sort_keys=True, separators=(',', ':'))
            if identity in fingerprints and fingerprints[identity] != fingerprint:
                raise ValueError('Repeated request_id has conflicting path/body')
            fingerprints[identity] = fingerprint
        if reply.get('accepted') is not True:
            continue
        if not isinstance(identity, str) or not identity:
            raise ValueError('Accepted action requires a nonempty request_id')
        response_signature = {k: v for k, v in reply.items() if k not in ('real_timestamp_ms', 'remaining_real_duration_s')}
        if identity in confirmed:
            if confirmed[identity] != response_signature:
                raise ValueError('Repeated confirmed request has conflicting response')
            continue
        confirmed[identity] = response_signature
        if e['path'] == '/measure':
            if reply.get('measure_result') not in ('direction', 'near', 'no_signal'):
                raise ValueError('Unknown accepted measure feedback')
            outcomes[reply['measure_result']] += 1
        elif e['path'] == '/clear':
            clear_attempts += 1
            if reply.get('clear_result') == 'success':
                if req.get('channel') in cleared:
                    raise ValueError('Distinct clear requests report duplicate successful channel')
                cleared.add(req['channel'])
                successful.append(reply['virtual_time_s'])
            elif reply.get('clear_result') == 'no_target_in_range':
                failures.append({'request_id': identity, 'channel': req.get('channel'),
                                 'position': req.get('position'), 'virtual_time_s': reply.get('virtual_time_s')})
            else:
                raise ValueError('Unknown accepted clear feedback')
        elif e['path'] not in ('/enter', '/exit'):
            raise ValueError('Unknown accepted action')
    return {'successful_times': successful, 'successful_channels': sorted(cleared),
            'outcomes': dict(outcomes), 'failures': failures, 'retries': retries,
            'clear_attempts': clear_attempts, 'unique_accepted_actions': len(confirmed)}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quantile(values, fraction):
    values = sorted(values)
    x = (len(values)-1)*fraction
    lo, hi = math.floor(x), math.ceil(x)
    return values[lo]+(values[hi]-values[lo])*(x-lo)


def independent_bootstrap(a, b, seed=BOOTSTRAP_SEED):
    if not a or not b or any(not finite_number(x) or x <= 0 for x in (*a, *b)):
        raise ValueError('Independent time bootstrap requires nonempty positive finite arms')
    rng = random.Random(seed)
    deltas, percentages = [], []
    for _ in range(BOOTSTRAP_DRAWS):
        x = statistics.fmean(rng.choices(a, k=len(a)))
        y = statistics.fmean(rng.choices(b, k=len(b)))
        deltas.append(y-x)
        percentages.append(100*(1-y/x))
    return {'difference_s': statistics.fmean(b)-statistics.fmean(a),
            'difference_ci95_s': [quantile(deltas,.025), quantile(deltas,.975)],
            'reduction_percent': 100*(1-statistics.fmean(b)/statistics.fmean(a)),
            'reduction_ci95_percent': [quantile(percentages,.025), quantile(percentages,.975)],
            'method': 'independent resampling within each arm; unpaired; percentile bootstrap',
            'difference_sign': 'negative means combined_cover is faster',
            'reduction_sign': 'positive means combined_cover reduces penalized loss',
            'sample_unit': 'whole official case, independently within each arm',
            'current_n': len(a), 'combined_cover_n': len(b),
            'seed': seed, 'draws': BOOTSTRAP_DRAWS}


def build(folder):
    folder = Path(folder).resolve()
    plan = read(folder/'plan.json')
    integrity = read(folder/'integrity/overall.json')
    issue_count = integrity.get('issue_count', integrity.get('n_issues'))
    if issue_count != 0:
        raise ValueError('Completed zero-issue independent audit is required for this full report')
    if (integrity.get('complete_prefix_only') is not False
            or integrity.get('n_audited_cases') != len(plan['cases'])
            or integrity.get('n_planned_unstarted') != 0
            or integrity.get('n_skipped_in_progress_or_incomplete') != 0):
        raise ValueError('Full audit of every planned attempt is required; no complete-only prefix')
    ids = [c['case_id'] for c in plan['cases']]
    sequences = [c['sequence'] for c in plan['cases']]
    if len(set(ids)) != len(ids) or len(set(sequences)) != len(sequences):
        raise ValueError('Duplicate planned case ID or allocation sequence')
    if sequences != list(range(len(sequences))):
        raise ValueError('Plan must preserve the complete ordered allocation sequence')
    if any(c.get('problem') != 4 or c.get('environment') != 'official_practice' for c in plan['cases']):
        raise ValueError('This report accepts P4 official practice only')
    if plan.get('formal_authorized') is not False or plan.get('practice_authorized_by_round_plan') is not True:
        raise ValueError('Expected the frozen practice-only allocation')
    rows, sources = [], []
    for case in plan['cases']:
        d = folder/'cases'/case['case_id']
        assignment = read(d/'assignment.json')
        if assignment != case:
            raise ValueError('Per-case assignment differs from frozen plan')
        r, a = read(d/'result.json'), read(d/'post_exit_audit.json')
        if (r.get('case_id') != case['case_id'] or r.get('arm') != case['arm']
                or r.get('stage') != case.get('stage', case.get('split'))
                or a.get('case_id') != case['case_id'] or a.get('problem') != 4):
            raise ValueError('Result/public audit identity does not match assigned case')
        events = [json.loads(s) for s in (d/'requests.jsonl').read_text('utf-8').splitlines() if s.strip()]
        feedback = accepted_feedback(events)
        successful = feedback['successful_times']
        if (r['clear_successes'] != len(successful) or r['clear_attempts'] != feedback['clear_attempts']
                or r['measures'] != sum(feedback['outcomes'].values())):
            raise ValueError('Result action counts disagree with accepted unique journal')
        n = a.get('source_total_post_exit')
        total = r['total_virtual_time_s']
        if not finite_number(total) or total < 0:
            raise ValueError('Invalid observed virtual time')
        reasons = []
        if r['status'] != 'complete': reasons.append('result_status_not_complete')
        if a.get('status') != 'complete': reasons.append('public_audit_status_not_complete')
        if not isinstance(n, int) or isinstance(n, bool) or not 10 <= n <= 16:
            reasons.append('public_total_missing_or_invalid')
        elif r['clear_successes'] != n or a.get('cleared') != n:
            reasons.append('public_total_clear_count_mismatch')
        if a.get('total_virtual_time_s') is None or abs(a['total_virtual_time_s']-total) > 1e-5:
            reasons.append('public_total_time_missing_or_mismatch')
        if (r.get('stop_evidence') or {}).get('type') not in ('known_upper_bound', 'per_channel_coverage'):
            reasons.append('completion_certificate_missing')
        full = not reasons
        if full and (not successful or successful[-1] > total+1e-5):
            raise ValueError('Last successful clear time inconsistent with complete result')
        stage = case.get('stage',case.get('split'))
        row = dict(case_id=case['case_id'], case_code=a.get('case_code'), stage=stage, arm=case['arm'],
            sequence=case['sequence'], n=n, n_directed=a.get('directional_total_post_exit'), complete=full,
            status=r['status'], observed_virtual_time_s=total, completion_time_s=total if full else None,
            penalized_time_s=total if full else FAILURE_PENALTY_S, average_clear_time_s=total/n if full else None,
            failed_or_unverified_reasons=reasons, error=r.get('error'),
            walk_distance_m=r['walk_distance_m'], measures=r['measures'], switches=r['switches'],
            clear_attempts=r['clear_attempts'], clear_successes=r['clear_successes'],
            failed_clear_attempts=len(feedback['failures']), failed_clear_records=feedback['failures'], movement_s=r['walk_distance_m']/5,
            measurement_s=5*r['measures'], switch_s=r['switches'], optical_s=3*r['clear_attempts'],
            laser_s=2*r['clear_successes'], post_clear_stop_tail_s=total-successful[-1] if full else None,
            program_real_time_s=r['program_real_time_s'], feedback=feedback['outcomes'], retry_records=feedback['retries'],
            stop_type=(r.get('stop_evidence') or {}).get('type'),
            original_log_filename=a.get('original_log_filename'), original_log_sha256=a.get('original_log_sha256'),
            raw_folder=path_label(d))
        rows.append(row)
        sources.extend({'path': path_label(p), 'sha256':sha(p)} for p in
                       (d/'assignment.json',d/'result.json',d/'post_exit_audit.json',d/'requests.jsonl'))
    if any(r['stage'] not in ('pilot','validation') or r['arm'] not in ARMS for r in rows):
        raise ValueError('Unexpected allocation; do not silently mix stages or arms')
    for case in plan['cases']:
        stage = case.get('stage', case.get('split'))
        if case.get('split') != stage or case.get('pilot') is not (stage == 'pilot'):
            raise ValueError('Conflicting stage/split/pilot allocation fields')
    validation = [r for r in rows if r['stage']=='validation']
    blocks = {}
    for case in plan['cases']:
        if case.get('stage', case.get('split')) == 'validation':
            blocks.setdefault(case.get('block'), []).append(case['arm'])
    if len(blocks) != 15 or any(Counter(v) != Counter({'current': 2, 'combined_cover': 2}) for v in blocks.values()):
        raise ValueError('Expected fifteen balanced four-case validation blocks')
    groups = []
    fields = ('observed_virtual_time_s','penalized_time_s','walk_distance_m','measures','switches',
              'clear_attempts','clear_successes','failed_clear_attempts','movement_s','measurement_s',
              'switch_s','optical_s','laser_s','program_real_time_s')
    for stage in ('pilot','validation'):
        for arm in ARMS:
            subset = [r for r in rows if r['stage']==stage and r['arm']==arm]
            if not subset: continue
            complete = [r for r in subset if r['complete']]
            g = dict(stage=stage, arm=arm, cases=len(subset), complete=len(complete),
                failed_or_unverified=len(subset)-len(complete),
                total_sources=sum(r['n'] for r in subset if r['n'] is not None),
                total_directed=sum(r['n_directed'] for r in subset if r['n_directed'] is not None))
            g.update(failure_rate=g['failed_or_unverified']/g['cases'],
                     failure_rate_wilson95=wilson(g['failed_or_unverified'], g['cases']),
                     public_N_known_cases=sum(r['n'] is not None for r in subset),
                     public_Ndir_known_cases=sum(r['n_directed'] is not None for r in subset))
            g['means'] = {k:statistics.fmean(r[k] for r in subset) for k in fields}
            g['conditional_complete_means'] = {k:statistics.fmean(r[k] for r in complete) if complete else None
                for k in ('completion_time_s','average_clear_time_s','post_clear_stop_tail_s')}
            t=[r['completion_time_s'] for r in complete]
            g.update(median_completion_s=statistics.median(t) if t else None,
                descriptive_p95_completion_s=quantile(t,.95) if t else None,
                max_completion_s=max(t) if t else None,
                sd_completion_s=statistics.stdev(t) if len(t)>1 else None,
                ge900_per_source=sum(r['average_clear_time_s']>=900 for r in complete),
                ge1000_per_source=sum(r['average_clear_time_s']>=1000 for r in complete),
                composition_counts=dict(Counter(f"{r['n']}/{r['n_directed']}" for r in subset)))
            groups.append(g)
    arms={arm:[r for r in validation if r['arm']==arm] for arm in ARMS}
    if len(arms['current'])!=30 or len(arms['combined_cover'])!=30:
        raise ValueError('Report is fixed to the planned 30+30 validation, no optional stopping')
    if any(sum(r['stage']=='pilot' and r['arm']==arm for r in rows) != 1 for arm in ARMS):
        raise ValueError('Expected one separate engineering pilot per arm')
    compare = independent_bootstrap([r['penalized_time_s'] for r in arms['current']],
                                    [r['penalized_time_s'] for r in arms['combined_cover']])
    payload=dict(scope='new official P4 practice; independent cases; not formal tests',
        pilot_cases=sum(r['stage']=='pilot' for r in rows),validation_cases=len(validation),formal_cases=0,
        groups=groups, comparison_penalized=compare, cases=rows,
        failure_penalty_s=FAILURE_PENALTY_S,
        failed_or_unverified_cases=[r for r in rows if not r['complete']],
        validation_failure_rate_difference=independent_failure_difference(
            sum(not r['complete'] for r in arms['current']), 30,
            sum(not r['complete'] for r in arms['combined_cover']), 30),
        all_complete=all(r['complete'] for r in rows), validation_all_complete=all(r['complete'] for r in validation), source_hashes=sources,
        original_logs_count=sum(bool(r['original_log_filename']) for r in rows),
        source_integrity_sha256=sha(folder/'integrity/overall.json'),
        source_plan_sha256=sha(folder/'plan.json'),script_sha256=sha(Path(__file__)))
    out=folder/'report'
    out.mkdir(exist_ok=True)
    (out/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    selected={g['arm']:g for g in groups if g['stage']=='validation'}
    a,b=selected['current'],selected['combined_cover']
    lines=['# P4新组合方案：官方演练迁移验证','',
        '本报告只记录本轮真实官方演练。比较原候选 NoSignal/L1/joint 与已经冻结的新组合（25站覆盖＋当前可行区域光学覆盖）；未加入下一轮处理顺序或自适应测量改动。', '',
        f"先执行{payload['pilot_cases']}个工程接入案例，另执行{len(validation)}个预先随机分配的验证案例，各臂30局，按区组交错。工程案例不混入效应估计；官方不能复位同一环境，因此两臂使用独立案例。正式测试0次。",'',
        '## 完整性与主要结果','',
        '|指标|原候选|新组合|','|---|---:|---:|',
        f"|完整清除局数|{a['complete']}/30|{b['complete']}/30|",
        f"|失败或未验证局数|{a['failed_or_unverified']}|{b['failed_or_unverified']}|",
        f"|失败率 Wilson 95% 区间|[{100*a['failure_rate_wilson95'][0]:.2f}%, {100*a['failure_rate_wilson95'][1]:.2f}%]|[{100*b['failure_rate_wilson95'][0]:.2f}%, {100*b['failure_rate_wilson95'][1]:.2f}%]|",
        f"|全尝试惩罚均值（秒，主指标）|{a['means']['penalized_time_s']:.2f}|{b['means']['penalized_time_s']:.2f}|",
        f"|成功局平均整局虚拟秒|{fmt(a['conditional_complete_means']['completion_time_s'])}|{fmt(b['conditional_complete_means']['completion_time_s'])}|",
        f"|成功局平均每局T/N（秒/源）|{fmt(a['conditional_complete_means']['average_clear_time_s'])}|{fmt(b['conditional_complete_means']['average_clear_time_s'])}|",
        f"|成功局整局中位数（秒）|{fmt(a['median_completion_s'])}|{fmt(b['median_completion_s'])}|",
        f"|成功局本批最慢整局（秒）|{fmt(a['max_completion_s'])}|{fmt(b['max_completion_s'])}|",
        f"|T/N≥900秒的局数|{a['ge900_per_source']}|{b['ge900_per_source']}|",
        f"|T/N≥1000秒的局数|{a['ge1000_per_source']}|{b['ge1000_per_source']}|",'',
        f"新组合减原候选的平均全尝试惩罚时间差为{compare['difference_s']:.2f}秒，独立分臂bootstrap 95%区间[{compare['difference_ci95_s'][0]:.2f}, {compare['difference_ci95_s'][1]:.2f}]秒。降幅{compare['reduction_percent']:.2f}%，区间[{compare['reduction_ci95_percent'][0]:.2f}%, {compare['reduction_ci95_percent'][1]:.2f}%]。失败或未验证局按预定360000秒计，不把失败前短耗时当成完成耗时；全清时该对比等于完整整局耗时对比。",'',
        '## 成本与组成','', '|指标（每局均值）|原候选|新组合|','|---|---:|---:|']
    for field,name in [('walk_distance_m','移动米'),('measures','检测次数'),('switches','切频道次数'),
                       ('failed_clear_attempts','失败清除次数'),('program_real_time_s','客户端现实运行秒')]:
        lines.append(f"|{name}|{a['means'][field]:.2f}|{b['means'][field]:.2f}|")
    lines += [f"|成功局最后成功清除后尾部秒|{fmt(a['conditional_complete_means']['post_clear_stop_tail_s'])}|{fmt(b['conditional_complete_means']['post_clear_stop_tail_s'])}|",
        f"|目标总数|{a['total_sources']}|{b['total_sources']}|",
        f"|定向目标总数|{a['total_directed']}|{b['total_directed']}|",'',
        f"公开N已知案例数：原候选{a['public_N_known_cases']}/30、新组合{b['public_N_known_cases']}/30；公开Ndir已知案例数分别为{a['public_Ndir_known_cases']}/30、{b['public_Ndir_known_cases']}/30。上表组成总数仅累加已知记录，不把未知数记为零。",'',
        '数量和类型只从退出后的官方可见界面获取；它们没有反馈给策略。目标组成可能随机不平衡，应结合整局数据解释，不能把序号相邻的两局当成同一个场景。尾部时间只作诊断，不用于提前停止或调参。','',
        '## 每局官方原始结果','',
        '|阶段|分配序号|策略|官方案例编码|核验全清|清除/总数|定向数|已观测虚拟秒|T/N秒|客户端现实秒|',
        '|---|---:|---|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        avg=f"{r['average_clear_time_s']:.2f}" if r['average_clear_time_s'] is not None else '未完成'
        lines.append(f"|{r['stage']}|{r['sequence']}|{r['arm']}|{r['case_code']}|{'是' if r['complete'] else '否'}|{r['clear_successes']}/{r['n']}|{r['n_directed']}|{r['observed_virtual_time_s']:.2f}|{avg}|{r['program_real_time_s']:.3f}|")
    risk = payload['validation_failure_rate_difference']
    lines += ['', '## 失败与未验证案例','',
        f"验证集失败率差（新组合减原候选）为{100*risk['difference']:.2f}个百分点，独立Wilson得分差95%区间[{100*risk['ci95'][0]:.2f}, {100*risk['ci95'][1]:.2f}]个百分点。0/30失败的单臂Wilson上限仍约11.35%；该样本不能证明总体零风险或失败率非劣性。",'']
    if payload['failed_or_unverified_cases']:
        lines += ['下列全部工程或验证失败/未验证记录均保留。工程案例不进入主比较；验证案例按360000秒计，未用失败前已观测耗时替代完成时间。','',
                  '|阶段|案例ID|策略|状态|未通过原因|','|---|---|---|---|---|']
        for row in payload['failed_or_unverified_cases']:
            lines.append(f"|{row['stage']}|{row['case_id']}|{row['arm']}|{row['status']}|{', '.join(row['failed_or_unverified_reasons'])}|")
    else:
        lines.append('本批全部工程与验证案例均有公开全清核对；没有失败或未验证案例被排除。')
    lines += ['', '## 证据、限制与复现','',
        f"独立原始请求审计为0问题；{payload['original_logs_count']}个官方行为日志按原文件名保存，SHA可在各局post_exit_audit.json与本报告JSON核对。没有解密、改写官方日志。",'',
        '每臂30局可评价平均耗时迁移，但不能证明所有难例均改善，不能据样本零失败声称总体失败概率为零，也不能可靠推断最慢5%的总体表现。程序现实时间为本地客户端单调计时，包含HTTP开销；不冒充正式官方程序时间。', '',
        '复现本报告：`python scripts/report_p4_refined_official.py --round results/p4_refined_official`。此命令只读取已保存证据，不消耗演练案例。统计使用预先固定的10000次独立分臂bootstrap、种子2026091137。', '',
        '全部案例原始目录：`results/p4_refined_official/cases`；独立审计：`integrity/overall.json`与`integrity/cases.json`；本报告结构化数据：`report/results.json`。']
    (out/'official_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'validation_cases':len(validation),'all_complete':payload['all_complete'],
        'comparison':compare,'report':str(out/'official_report.md')},ensure_ascii=False,indent=2))
    return payload


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--round',type=Path,default=ROOT/'results/p4_refined_official')
    a=p.parse_args()
    build(a.round.resolve())
