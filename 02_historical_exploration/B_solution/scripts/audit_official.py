"""Audit public responses and operator-observed POST-EXIT practice metadata.

This script makes no network calls. It never reads or decrypts target data.
It preserves original requests/results/jlogs and writes distinct derived files.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/official_practice'
SOURCE = ROOT.parent / 'B题/Jammers-simulator-win64/Jammers-simulator/JammersSimulatorData/behavior-logs'
RECORDS = [
    ('p3_02', 'MK6S-GWQ5-EBNN-VPAC', 15, 0, 'practice-p3-7098202057825360756-MK6S-GWQ5-EBNN-VPAC.jlog'),
    ('p3_03', 'GF8M-F766-BQYN-ZDXC', 11, 0, 'practice-p3-7997828451548612134-GF8M-F766-BQYN-ZDXC.jlog'),
    ('p4_01', '2UBT-Y22B-E6C6-MXQU', 16, 14, 'practice-p4-2926743685885109668-2UBT-Y22B-E6C6-MXQU.jlog'),
    ('p4_02', '63G4-VPK5-AZX3-2YPM', 14, 2, 'practice-p4-9221554332807856076-63G4-VPK5-AZX3-2YPM.jlog'),
]
CODE = 'b3cdd5b3089c9ea55f14a791b74702bfbc5777b8a6e8e8c8d19d3caf3f12b4bd'


def main():
    saved = OUT/'original_logs'
    saved.mkdir(exist_ok=True)
    rows = []
    for directory, code, total, directional, filename in RECORDS:
        path = OUT/directory
        result = json.loads((path/'result.json').read_text(encoding='utf-8'))
        requests = [json.loads(x) for x in (path/'requests.jsonl').read_text(encoding='utf-8').splitlines()]
        accepted = [x for x in requests if x.get('outcome') == 'accepted']
        assert accepted[0]['path'] == '/enter' and accepted[-1]['path'] == '/exit'
        assert accepted[-1]['response']['exit_reason'] == 'user_exit'
        assert len({x['request']['request_id'] for x in accepted}) == len(accepted)
        position, channel = (0., 0.), 1
        walk, switches, measures, attempts, successes = 0., 0, 0, 0, 0
        unique = set()
        for item in accepted:
            body, response = item['request'], item['response']
            if item['path'] in ('/measure', '/clear'):
                newpos = body['position']['x'], body['position']['y']
                walk += math.dist(position, newpos)
                position = newpos
            if item['path'] == '/measure':
                switches += int(channel != body['channel'])
                channel = body['channel']
                measures += 1
            if item['path'] == '/clear':
                attempts += 1
                if response['clear_result'] == 'success':
                    successes += 1
                    assert body['channel'] not in unique
                    unique.add(body['channel'])
        calculated = walk/5+switches+5*measures+3*attempts+2*successes
        residual = result['total_virtual_time_s']-calculated
        assert abs(residual) < len(accepted)*1e-6 + 1e-5
        assert successes == total == result['clear_successes']
        assert result['status'] == 'complete'
        target = saved/filename
        if (SOURCE/filename).exists():
            if not target.exists():
                shutil.copyfile(SOURCE/filename, target)
            assert target.read_bytes() == (SOURCE/filename).read_bytes()
        assert target.exists(), f'original encrypted log missing: {filename}'
        row = dict(environment='official_practice', directory=directory, problem=result['problem'],
                   case_code=code, source_total_post_exit=total, directional_total_post_exit=directional,
                   cleared=successes, clear_fraction=successes/total,
                   total_virtual_time_s=result['total_virtual_time_s'], average_clear_time_s=calculated/successes,
                   local_program_real_s=result['program_real_time_s'],
                   server_timestamp_span_s=(accepted[-1]['response']['real_timestamp_ms']-
                                            accepted[0]['response']['real_timestamp_ms'])/1000,
                   walk_m=walk, measures=measures, switches=switches, clear_attempts=attempts,
                   clear_failures=attempts-successes, timing_residual_s=residual,
                   stop_evidence_type=result['stop_evidence']['type'],
                   original_log_filename=filename, original_log_bytes=target.stat().st_size,
                   original_log_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                   code_sha256=CODE, metadata_source='post-exit UI; screenshots interface.png and completed.png')
        rows.append(row)
        (path/'post_exit_audit.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        # Derived convenience history explicitly marked; original log is untouched.
        before = {'position': {'x': 0., 'y': 0.}, 'current_channel': 1, 'virtual_time_s': 0.,
                  'cleared_count': 0, 'cleared_channels': [], 'entered': False, 'exited': False, 'deadline': None}
        with (path/'derived_state_transitions.jsonl').open('w', encoding='utf-8') as fp:
            for entry in requests:
                derived = dict(entry, state_before=before, derivation='previous confirmed state; original JSONL unmodified')
                fp.write(json.dumps(derived, ensure_ascii=False)+'\n')
                before = entry['state_after']
    (OUT/'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT/'summary.csv').open('w', encoding='utf-8-sig', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# 官方演练审计（不是正式结果）', '',
             '四局均在2026-09-11真实执行。总数仅在exit后由官方界面读取；原result.json的未知总数字段不改写。',
             '原始加密日志从官方自动保存的behavior-logs目录按原文件名逐字节复制，未解密、未修改；界面导出操作未得到另一个可定位的导出副本，故准确标记为原始保存副本。',
             '自有请求日志含实际robot_id，公开支撑包不包含这些私有JSONL与账号截图。原始日志在本机保留。', '',
             '|问题|案例|清除/总数|虚拟总秒|平均清除秒|本地程序秒|响应时间戳跨度秒|',
             '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"|{r['problem']}|{r['case_code']}|{r['cleared']}/{r['source_total_post_exit']}|{r['total_virtual_time_s']:.6f}|{r['average_clear_time_s']:.6f}|{r['local_program_real_s']:.6f}|{r['server_timestamp_span_s']:.3f}|")
    lines += ['', '额外接入失败：p3_01在官方测试未开放时提前enter，4次同ID传输均连接重置；未确认进入、无案例编码、0个确认动作。此项不是一个已执行案例，不混入四局的清除率；原始失败记录保留。',
              '', '本地单调程序时间包含HTTP和客户端整理开销；服务响应时间戳跨度使用enter/exit真实字段，但不冒充尚未执行的正式官方计时。',
              '', '正式问题3三次、问题4三次：均待用户明确授权与官方执行。正式不提供总数，不填写未知分母。']
    (OUT/'summary.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'official_practice_cases': len(rows), 'complete': len(rows), 'original_logs_preserved': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
