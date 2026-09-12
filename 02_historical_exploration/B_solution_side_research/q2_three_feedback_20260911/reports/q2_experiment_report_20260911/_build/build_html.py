from pathlib import Path
import html
import re
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0,str(here/'vendor'))
from latex2mathml.converter import convert

md = (here.parent/'第二问实验报告_最终稿.md').read_text(encoding='utf-8')
lines=md.splitlines(); result=[]; i=0

def inline(text):
    return re.sub(r'\*\*(.*?)\*\*',r'<strong>\1</strong>',html.escape(text))

while i<len(lines):
    s=lines[i].strip()
    if not s: i+=1; continue
    if s=='$$':
        i+=1; eq=[]
        while i<len(lines) and lines[i].strip()!='$$': eq.append(lines[i]); i+=1
        result.append('<div class="eq">'+convert('\n'.join(eq)).replace('display="inline"','display="block"')+'</div>')
        i+=1; continue
    if s.startswith('```'):
        i+=1; code=[]
        while i<len(lines) and not lines[i].startswith('```'): code.append(lines[i]); i+=1
        result.append('<pre>'+html.escape('\n'.join(code))+'</pre>'); i+=1; continue
    if s.startswith('|'):
        matrix=[]
        while i<len(lines) and lines[i].strip().startswith('|'):
            matrix.append([x.strip() for x in lines[i].strip().strip('|').split('|')]); i+=1
        matrix.pop(1)
        result.append('<table><thead><tr>'+''.join('<th>'+inline(x)+'</th>' for x in matrix[0])+'</tr></thead><tbody>')
        result.extend('<tr>'+''.join('<td>'+inline(x)+'</td>' for x in row)+'</tr>' for row in matrix[1:])
        result.append('</tbody></table>'); continue
    if s.startswith('### '): result.append('<h3>'+inline(s[4:])+'</h3>')
    elif s.startswith('## '): result.append('<h2>'+inline(s[3:])+'</h2>')
    elif s.startswith('# '): result.append('<h1>'+inline(s[2:])+'</h1>')
    elif s=='原理分析 模型建立 模型求解 结果检验': result.append('<p class="subtitle">'+s+'</p>')
    else:
        cls='step' if re.match(r'^\d+\. ',s) else 'ref' if re.match(r'^\[\d+\]',s) else ''
        result.append('<p class="'+cls+'">'+inline(s)+'</p>')
    i+=1

css='''
@page {size:Letter; margin:18mm 18mm 19mm;}
*{box-sizing:border-box}
html{color:#111; font-family:"Times New Roman","SimSun",serif; font-size:11pt;}
body{margin:0; line-height:1.53;}
h1,h2,h3{font-family:"Microsoft YaHei",sans-serif; color:#000; break-after:avoid; font-weight:600;}
h1{font-size:20pt;line-height:1.4;margin:0 0 8pt;}
.subtitle{font-family:"Microsoft YaHei";font-size:10.5pt;margin:0 0 15pt;}
h2{font-size:15pt;line-height:1.4;margin:16pt 0 7pt;}
h3{font-size:12pt;line-height:1.4;margin:12pt 0 6pt;}
p{margin:0 0 7pt;orphans:2;widows:2;}
.step{padding-left:14pt;text-indent:-14pt;margin-bottom:5pt;}
.ref{font-size:9pt;overflow-wrap:anywhere;}
.eq{break-inside:avoid;text-align:center;margin:9pt 0 12pt;width:100%;}
math{font-family:"Cambria Math",math;font-size:12pt;}
table{border-collapse:collapse;width:100%;table-layout:auto;font-size:9.5pt;line-height:1.35;margin:10pt 0 12pt;}
thead{display:table-header-group;}tr{break-inside:avoid;}
th,td{border:0.6pt solid #d9d9d9;padding:6pt 7pt;vertical-align:middle;}
th{background:#e5ebef;font-weight:bold;}td{text-align:center;}td:first-child{text-align:left;}
pre{font-family:Consolas,"SimSun",monospace;white-space:pre-wrap;overflow-wrap:anywhere;font-size:8.5pt;line-height:1.4;margin:7pt 0 10pt;}
strong{font-weight:bold;}
'''
out='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>第二问实验报告</title><style>'+css+'</style></head><body>'+''.join(result)+'</body></html>'
(here/'final_report.html').write_text(out,encoding='utf-8')
print('HTML prepared')
