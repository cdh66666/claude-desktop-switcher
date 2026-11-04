"""列出最近的 Claude 对话（所有账号共用本机记录），找出没跑完、卡住、等用户回复的任务。

用法：python -I _tools/sessions_status.py [小时数，默认 48]
结果写到 _tools/sessions_status.txt（UTF-8）。每个对话一段：
  状态  对话 id  最后活动时间
  第一句：用户开这个对话时说的话（看出是哪个项目）
  用户最后说：...
  Claude 最后说：...
状态：
  中断   最后一条是 Claude 调工具但没有结果，或用户刚说完话 Claude 没回（多半额度用完/被打断）
  等用户 Claude 最后一句里在问问题/等确认
  完成   正常结束
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import project_files  # noqa: E402

hours = float(sys.argv[1]) if len(sys.argv) > 1 else 48
since = time.time() - hours * 3600
noise = ('Base directory for this skill', 'Page contract', '[Image: original', '<task-notification>')
ask = re.compile(r'[？?]\s*$|要不要|是否|请确认|等你|你选|选哪|告诉我')


def text_of(c):
    if isinstance(c, list):
        return ' '.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
    return c if isinstance(c, str) else ''


def clean(s):
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', s, flags=re.S).strip()


blocks = []
for f in project_files():
    mt = os.path.getmtime(f)
    if mt < since:
        continue
    first = last_user = last_ai = last_ts = ''
    last_kind = None  # 'user' / 'ai_text' / 'tool_use' / 'tool_result'
    for line in open(f, encoding='utf-8', errors='ignore'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        last_ts = d.get('timestamp') or last_ts
        m = d.get('message')
        if not isinstance(m, dict):
            continue
        c = m.get('content')
        if d.get('type') == 'user':
            if isinstance(c, list) and any(isinstance(x, dict) and x.get('type') == 'tool_result' for x in c):
                last_kind = 'tool_result'
                continue
            t = clean(text_of(c))
            if t and not any(n in t for n in noise):
                first = first or t
                last_user = t
                last_kind = 'user'
        elif d.get('type') == 'assistant':
            if isinstance(c, list) and any(isinstance(x, dict) and x.get('type') == 'tool_use' for x in c):
                last_kind = 'tool_use'
            t = clean(text_of(c))
            if t:
                last_ai = t
                if last_kind != 'tool_use':
                    last_kind = 'ai_text'
    if not first:
        continue
    if last_kind in ('user', 'tool_use', 'tool_result'):
        state = '中断'
    elif ask.search(last_ai[-200:]):
        state = '等用户'
    else:
        state = '完成'
    blocks.append((last_ts, f"[{state}] {os.path.basename(f)[:8]}  {last_ts[5:16].replace('T', ' ')} UTC\n"
                       f"  第一句：{first[:200]}\n  用户最后说：{last_user[:300]}\n  Claude 最后说：{last_ai[-400:]}"))

blocks.sort(reverse=True)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sessions_status.txt')
open(out, 'w', encoding='utf-8').write('\n\n'.join(b for _, b in blocks))
print(len(blocks), 'sessions ->', out)
