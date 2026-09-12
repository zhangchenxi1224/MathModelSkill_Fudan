"""Read exported OOXML and caches without rewriting the workbook."""
from pathlib import Path
import json
import math
import hashlib
import zipfile
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
book=HERE.parent/'候选与基线官方完整数据.xlsx'
inputs=json.loads((HERE/'inputs.json').read_text(encoding='utf-8'))
independent=json.loads((HERE/'candidate_source_audit.json').read_text(encoding='utf-8'))
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def close(a,b): assert isinstance(a,(int,float)) and math.isclose(a,b,rel_tol=0,abs_tol=1e-8),(a,b)
with zipfile.ZipFile(book) as z:
    strings=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
    sheets=[ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')) for i in (1,2)]
    def cells(sheet):
        result={}
        for c in sheet.findall('.//s:sheetData/s:row/s:c',NS):
            v=c.find('s:v',NS)
            if c.get('t')=='s': value=strings[int(v.text)]
            elif c.get('t')=='inlineStr': value=''.join(c.find('s:is',NS).itertext())
            elif c.get('t')=='b': value=v.text=='1'
            elif c.get('t')=='e': raise AssertionError((c.get('r'),v.text))
            elif v is None or v.text is None: value=None
            else:
                try: value=float(v.text)
                except ValueError: value=v.text
            if c.find('s:f',NS) is not None: assert v is not None and v.text is not None,c.get('r')
            result[c.get('r')]=value
        return result
    summary,detail=map(cells,sheets)
    checked=0
    for row,expected in enumerate(inputs['rows'],6):
        for col,value in enumerate(expected):
            letter=chr(65+col) # A:X original fields plus provenance
            actual=detail[f'{letter}{row}']
            if isinstance(value,(float,int)) and not isinstance(value,bool):
                # Export retains the exact IEEE-754 value read from the CSV/JSON.
                assert actual==value,(row,col,actual,value)
            else: assert actual==value,(row,col,actual,value)
            checked+=1
        close(detail[f'Y{row}'],expected[15]-expected[16])
        close(detail[f'Z{row}'],expected[12]/5)
        close(detail[f'AA{row}'],expected[13]*5)
        close(detail[f'AB{row}'],expected[14])
        close(detail[f'AC{row}'],expected[15]*3)
        close(detail[f'AD{row}'],expected[16]*2)
        close(detail[f'AE{row}'],expected[12]/5+expected[13]*5+expected[14]+expected[15]*3+expected[16]*2)
        close(detail[f'AF{row}'],expected[10]-detail[f'AE{row}'])
        close(detail[f'AG{row}'],expected[5]/expected[4])
        close(detail[f'AH{row}'],1)
    for i,c in enumerate(inputs['controls']):
        r=16+i
        for col,expected in {'C':30,'D':30,'E':0,'F':0,'G':c['completion_time_mean_s'],'H':c['walk_distance_mean_m'],'I':c['measurements_mean'],'J':c['failed_clears_mean']}.items():close(summary[f'{col}{r}'],expected)
    for group in independent['groups']:
        i=0 if group['problem']==3 else 1
        r=17+2*i
        close(summary[f'G{r}'],group['total_virtual_time_s']['mean'])
        close(summary[f'F{33+2*i}'],group['per_case_average_clear_time_s']['mean'])
        close(summary[f'H{26+2*i}'],group['program_real_time_s']['mean'])
        close(summary[f'H{33+2*i}'],group['total_virtual_time_s']['sum'])
        close(summary[f'I{33+2*i}'],group['walk_distance_m_total'])
        close(summary[f'J{33+2*i}'],group['action_totals']['measures'])
    for i,problem in enumerate((3,4)):
        src=inputs['summary']['problems'][str(problem)]
        expected=[*src['candidate_minus_baseline']['verified_completion_time_s']['ci95'],*src['candidate_minus_baseline']['penalized_loss_s']['ci95'],src['arms']['candidate']['failure_rate_wilson95'][1]]
        for letter,value in zip('DEFGH',expected):assert summary[f'{letter}{39+i}']==value
    table=ET.fromstring(z.read('xl/tables/table1.xml'))
    assert table.get('ref')=='A5:AH125'
    assert table.find('s:autoFilter',NS) is not None
    pane=sheets[1].find('.//s:pane',NS)
    assert pane is not None and float(pane.get('xSplit'))==4 and float(pane.get('ySplit'))==5
    workbook=ET.fromstring(z.read('xl/workbook.xml'))
    assert [s.get('name') for s in workbook.find('s:sheets',NS)]==['汇总','逐局记录']
    formulas=sum(len(s.findall('.//s:f',NS)) for s in sheets)
qa={'status':'passed_read_only_exported_ooxml_and_cache_checks','workbook':book.name,'workbook_sha256':sha(book),'sheets':['汇总','逐局记录'],'source_and_provenance_cells_checked':checked,'original_csv_cells_checked':120*18,'raw_cell_values_exactly_preserved':True,'all_120_rows_calculation_caches_checked':True,'all_formula_caches_present':True,'formula_cells':formulas,'error_cells':0,'autofilter_range':'A5:AH125','frozen_identifying_columns':4,'frozen_header_rows':5,'independent_candidate_audit_sha256':sha(HERE/'candidate_source_audit.json'),'independent_candidate_statistics_agree':True,'frozen_ci_values_exactly_preserved':True,'native_excel_interactive_engine_not_invoked':True}
(HERE/'saved_workbook_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(qa,ensure_ascii=False,indent=2))
