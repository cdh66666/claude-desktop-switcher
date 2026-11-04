"""生成抖音图文（1080×1440，10 张）：写 HTML，再用 Edge 无界面模式截成 PNG。
跑法：python docs/promo/make_slides.py   （先跑 scripts/demo_screenshots.py 生成界面截图）
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHOTS = (HERE.parent / "screenshots").as_uri()
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
REPO = "github.com/cdh66666/claude-desktop-switcher"

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1440px;overflow:hidden;background:#F5F4EE;color:#191918;
 font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif;position:relative}
.pad{padding:96px 84px 0}
.tag{display:inline-block;background:#D97757;color:#fff;font-size:30px;font-weight:700;
 padding:10px 26px;border-radius:40px;letter-spacing:2px}
h1{font-size:96px;line-height:1.18;font-weight:900;margin-top:36px;letter-spacing:2px}
h2{font-size:76px;line-height:1.2;font-weight:900;margin-top:30px}
.sub{font-size:40px;color:#6F6E77;margin-top:26px;line-height:1.5}
.o{color:#D97757}
.page{position:absolute;right:60px;bottom:44px;font-size:28px;color:#A8A7B0}
.brand{position:absolute;left:84px;bottom:44px;font-size:28px;color:#A8A7B0}
.card{background:#fff;border:2px solid #E8E6DF;border-radius:32px;padding:36px 40px}
.shot{border-radius:28px;box-shadow:0 30px 80px rgba(80,50,30,.22);border:2px solid #E8E6DF}
.row{display:flex;align-items:center;gap:28px}
.num{flex:none;width:76px;height:76px;border-radius:50%;background:#D97757;color:#fff;
 font-size:40px;font-weight:900;display:flex;align-items:center;justify-content:center}
.big{font-size:46px;font-weight:800}
.small{font-size:34px;color:#6F6E77;margin-top:8px}
.arrow{font-size:44px;color:#D97757;text-align:center;line-height:1;margin:4px 0}
.x{color:#C25B3B}.ok{color:#3B8C5C}
"""

FOOT = '<div class="brand">Claude 换号器 · 开源免费</div><div class="page">{n}/{total}</div>'

SLIDES = []

# 1 封面
SLIDES.append(f"""
<div class="pad"><span class="tag">开源 · Windows · 免费</span>
<h1>Claude 额度用完<br><span class="o">自动换号</span><br>接着干活</h1>
<div class="sub">多个账号轮流用，不用再手动折腾</div></div>
<img class="shot" src="{SHOTS}/main.png" style="position:absolute;left:330px;top:690px;width:600px;height:640px;object-fit:cover;object-position:top;transform:rotate(-3deg)">
<div style="position:absolute;left:84px;top:880px;width:300px;z-index:2" class="card">
 <div class="big o">5小时 100%</div><div class="small">→ 自动换到<br>已恢复的号</div></div>
""")

# 2 痛点
steps_old = ["发现额度用完了", "退出 Claude", "登录另一个号", "找回刚才的对话", "打字“继续”"]
old = "".join(f'<div class="row" style="margin-top:26px"><div class="num" style="background:#C9C7C0">{i+1}</div>'
              f'<div class="big" style="color:#6F6E77">{t}</div></div>' for i, t in enumerate(steps_old))
SLIDES.append(f"""
<div class="pad"><span class="tag">以前</span>
<h2>额度一满<br>就得<span class="x">停下来折腾</span></h2>
<div class="card" style="margin-top:56px">{old}</div>
<div class="card" style="margin-top:40px;border-color:#D97757;background:#FCEEE8">
 <div class="big">现在：<span class="o">0 步</span></div>
 <div class="small">换号器自己查、自己换、自己发“继续”</div></div></div>
""")

# 3 一眼看清
SLIDES.append(f"""
<div class="pad"><span class="tag">功能 1</span><h2>每个号用了多少<br><span class="o">一眼看清</span></h2></div>
<div style="position:absolute;left:84px;top:450px;width:560px;height:880px;overflow:hidden" class="shot">
 <img src="{SHOTS}/main.png" style="width:840px;margin:-60px 0 0 -40px"></div>
<div style="position:absolute;left:680px;top:450px;width:330px">
 <div class="card"><div class="big">5h</div><div class="small">5 小时额度<br>用了多少 %</div></div>
 <div class="card" style="margin-top:28px"><div class="big">7d</div><div class="small">一周额度</div></div>
 <div class="card" style="margin-top:28px"><div class="big o">14:29</div><div class="small">几点恢复</div></div>
 <div class="card" style="margin-top:28px"><div class="big"><span class="ok">●</span> <span class="x">●</span></div><div class="small">绿=能用<br>红=用完</div></div>
</div>
""")

# 4 自动换号流程
flow = [("每 3 分钟", "查一次当前号的额度"), ("额度用完？", "5 小时到 100% 或被限流"),
        ("挑一个号", "已恢复的优先"), ("切过去", "自动重开 Claude"), ("发“继续”", "对话接着跑")]
f = '<div class="arrow">↓</div>'.join(
    f'<div class="card row" style="padding:18px 36px"><div class="num">{i+1}</div><div><div class="big">{a}</div>'
    f'<div class="small" style="margin-top:2px">{b}</div></div></div>' for i, (a, b) in enumerate(flow))
SLIDES.append(f"""
<div class="pad"><span class="tag">功能 2</span><h2>用完了<span class="o">自动换</span></h2>
<div style="margin-top:36px">{f}</div></div>
""")

# 5 挑号
def mini(name, s, w, verdict, color, why):
    return (f'<div class="card row" style="padding:24px 34px;margin-top:22px">'
            f'<div style="flex:1"><div class="big" style="font-size:42px">{name}</div>'
            f'<div class="small">5小时 {s} · 周 {w}</div></div>'
            f'<div style="text-align:right"><div class="big" style="color:{color}">{verdict}</div>'
            f'<div class="small">{why}</div></div></div>')
SLIDES.append(f"""
<div class="pad"><span class="tag">功能 3</span><h2>挑号<span class="o">有讲究</span></h2>
<div class="sub">先挑已恢复的，没有就挑最快恢复的</div>
<div style="margin-top:40px">
{mini("备用号B", "0%", "18%", "✓ 选它", "#3B8C5C", "已恢复，周额度多")}
{mini("学习号", "57%", "92%", "跳过", "#C25B3B", "周额度快满")}
{mini("备用号A", "100%", "63%", "跳过", "#C25B3B", "还没恢复")}
{mini("旧号", "—", "—", "跳过", "#C25B3B", "登录失效")}
</div>
<div class="sub" style="font-size:34px;margin-top:36px">都不能用？不换，告诉你几点有号恢复</div></div>
""")

# 6 自动发继续
SLIDES.append(f"""
<div class="pad"><span class="tag">功能 4</span><h2>换完<span class="o">自动接着干</span></h2>
<div class="sub">打开“总控”对话，自动发“继续”</div>
<div class="card" style="margin-top:56px;padding:0;overflow:hidden">
 <div style="background:#F0EFED;padding:26px 36px;font-size:34px;color:#6F6E77">总控 - Claude Code</div>
 <div style="padding:40px 36px;font-size:36px;line-height:1.7;color:#6F6E77">
  …上一个号额度用完，对话停在这里<br>
  <span style="color:#A8A7B0">（换号中…）</span></div>
 <div style="margin:0 36px 36px;border:3px solid #D97757;border-radius:24px;padding:28px 32px;font-size:44px;display:flex;justify-content:space-between">
  <span>继续<span class="o">|</span></span><span class="tag" style="font-size:30px">发送 ↵</span></div>
</div>
<div class="row" style="margin-top:44px;gap:18px;font-size:34px;color:#6F6E77">
 <span class="ok big" style="font-size:40px">✓</span>先核对是不是“总控”，对不上就不发</div>
<div class="card" style="margin-top:40px;display:flex;justify-content:space-between;align-items:center;font-size:36px;font-weight:800">
 <span>打开总控</span><span class="o">→</span><span>核对标题</span><span class="o">→</span><span>输入继续</span><span class="o">→</span><span>回车</span></div></div>
""")

# 7 接力流程
relay = [("号 A 额度用完", "5 小时 100%", "#C9C7C0"), ("换号器自动换到号 B", "挑已恢复的号", "#D97757"),
         ("“总控”自动收到“继续”", "换号器打开总控、发出去", "#D97757"),
         ("总控读所有账号的记录", "谁停在半路、谁在等你", "#D97757"), ("各项目对话接着做", "接的是最新进度", "#3B8C5C")]
rl = '<div class="arrow">↓</div>'.join(
    f'<div class="card row" style="padding:18px 36px"><div class="num" style="background:{c}">{i+1}</div>'
    f'<div><div class="big" style="font-size:40px">{a}</div><div class="small" style="margin-top:2px">{b}</div></div></div>'
    for i, (a, b, c) in enumerate(relay))
SLIDES.append(f"""
<div class="pad"><span class="tag">搭配整理对话</span><h2>换号之后<br><span class="o">所有项目接着跑</span></h2>
<div style="margin-top:32px">{rl}</div></div>
""")

# 8 侧栏整理
def item(name, state, color, child=True):
    pad = "padding-left:76px" if child else ""
    line = '<span style="color:#C9C7C0;margin-right:16px">└</span>' if child else ""
    return (f'<div style="display:flex;justify-content:space-between;align-items:center;padding:22px 36px;{pad};'
            f'border-top:2px solid #F0EFED"><span class="big" style="font-size:40px">{line}{name}</span>'
            f'<span style="font-size:30px;color:{color};font-weight:700">{state}</span></div>')
side = (item("总控", "收到“继续”", "#D97757", False) + item("项目 A", "接着做 ▶", "#3B8C5C")
        + item("项目 B", "接着做 ▶", "#3B8C5C") + item("项目 C", "等你回复 ？", "#C25B3B"))
rules = [("一个项目一个对话", "不重复开，侧栏不乱"), ("接最新进度", "先读别的号做到哪"), ("每次换号都复盘", "哪步慢、挑错号都能看出来")]
rr = "".join(f'<div class="row" style="margin-top:22px"><span class="ok big" style="font-size:44px">✓</span>'
             f'<div><span class="big" style="font-size:40px">{a}</span><span class="small" style="margin-left:18px">{b}</span></div></div>'
             for a, b in rules)
SLIDES.append(f"""
<div class="pad"><span class="tag">整理对话</span><h2>侧栏只剩<br><span class="o">总控 + 每个项目一个</span></h2>
<div class="card" style="margin-top:48px;padding:0;overflow:hidden">
 <div style="background:#F0EFED;padding:20px 36px;font-size:30px;color:#6F6E77">Claude Code · 侧栏</div>{side}</div>
<div style="margin-top:30px">{rr}</div></div>
""")

# 9 防翻车
g = [("10 分钟", "两次换号至少隔<br>10 分钟，不来回切"), ("先存档", "切走前保存登录<br>换回来不用重登"),
     ("有记录", "每次换号都记日志<br>哪步慢一目了然"), ("能关掉", "一个勾选框<br>随时关自动换号")]
grid = "".join(f'<div class="card" style="width:440px;height:330px"><div class="big o" style="font-size:56px">{a}</div>'
               f'<div class="small" style="margin-top:22px;line-height:1.6">{b}</div></div>' for a, b in g)
SLIDES.append(f"""
<div class="pad"><span class="tag">安心</span><h2>防翻车</h2>
<div style="display:flex;flex-wrap:wrap;gap:32px;margin-top:56px">{grid}</div></div>
<img class="shot" src="{SHOTS}/auto_switch_bar.png" style="position:absolute;left:84px;top:1110px;width:912px">
""")

# 10 开源地址
SLIDES.append(f"""
<div class="pad"><span class="tag">开源免费</span><h2>代码全公开<br><span class="o">拿去就能用</span></h2>
<div class="card" style="margin-top:44px;border-color:#D97757">
 <div class="small">GitHub</div><div class="big" style="font-size:44px;margin-top:10px">github.com/cdh66666/<br>claude-desktop-switcher</div></div>
<div class="card" style="margin-top:36px">
 <div class="row"><div class="num">1</div><div class="big" style="font-size:40px">下载，双击打开</div></div>
 <div class="row" style="margin-top:26px"><div class="num">2</div><div class="big" style="font-size:40px">每个号登录一次、点保存</div></div>
 <div class="row" style="margin-top:26px"><div class="num">3</div><div class="big" style="font-size:40px">勾上“额度用完自动换号”</div></div>
 <div class="row" style="margin-top:26px"><div class="num">4</div><div class="big" style="font-size:40px">整理对话模板放进工作文件夹</div></div>
</div>
<div class="small" style="margin-top:40px;font-size:28px;line-height:1.6">基于 Picadoo/claude-desktop-switcher（MIT）改进 · 第三方工具，非 Anthropic 官方 · 只在本机运行，请遵守 Claude 使用条款</div></div>
""")


def main():
    out = HERE / "slides"
    out.mkdir(exist_ok=True)
    for i, body in enumerate(SLIDES, 1):
        html = out / f"{i:02d}.html"
        html.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>'
                        f'<body>{body}{FOOT.format(n=i, total=len(SLIDES))}</body></html>', encoding="utf-8")
        png = out / f"{i:02d}.png"
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", f"--screenshot={png}",
                        "--window-size=1080,1440", "--allow-file-access-from-files", html.as_uri()],
                       capture_output=True, timeout=60)
        print(png, png.exists())


if __name__ == "__main__":
    main()
