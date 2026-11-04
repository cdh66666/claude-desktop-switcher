"""列出本机所有 Claude 对话里用户最近说的话（所有账号的对话记录都在同一个文件夹）。

用法：python -I _tools/recent_requests.py [小时数，默认 24] [关键词，可用 | 分隔]
结果写到 _tools/recent_requests.txt（UTF-8），也打印条数。
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import project_files  # noqa: E402

hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
kws = [k for k in (sys.argv[2].split('|') if len(sys.argv) > 2 else []) if k]
since = time.time() - hours * 3600
skip = ('Base directory for this skill', 'task-notification', 'Page contract', '[Image: original')

rows = []
for f in project_files():
    if os.path.getmtime(f) < since:
        continue
    for line in open(f, encoding='utf-8', errors='ignore'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get('type') != 'user' or not isinstance(d.get('message'), dict):
            continue
        c = d['message'].get('content')
        if isinstance(c, list):
            c = ' '.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
        if not isinstance(c, str):
            continue
        c = re.sub(r'<system-reminder>.*?</system-reminder>', '', c, flags=re.S).strip()
        if not c or any(s in c for s in skip):
            continue
        if kws and not any(k in c for k in kws):
            continue
        rows.append(f"{d.get('timestamp', '')[:19]} {os.path.basename(f)[:8]} | {c[:600]}")

rows.sort()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recent_requests.txt')
open(out, 'w', encoding='utf-8').write('\n'.join(rows))
print(len(rows), 'messages ->', out)
