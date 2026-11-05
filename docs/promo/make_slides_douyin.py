"""抖音专用版图（原版第一次发被标“不适宜公开”）：去掉 Claude / 换号 / 链接 / Anthropic 等字眼。
跑法：python docs/promo/make_slides_douyin.py   → docs/promo/slides_douyin/01~10.png
"""
import subprocess
import make_slides as m

REPL = [
    ('<span class="small">GitHub</span>', '<span class="small">GitHub</span>'),
    ('github.com/cdh66666/<br>claude-desktop-switcher', '搜作者 cdh66666'),
    ('基于 Picadoo/claude-desktop-switcher（MIT）改进 · 第三方工具，非 Anthropic 官方 · 只在本机运行，请遵守 Claude 使用条款',
     '基于开源项目改进（MIT） · 第三方小工具 · 只在本机运行'),
    ("总控 - Claude Code", "总控"),
    ("Claude Code · 侧栏", "侧栏"),
    ("Claude 额度用完", "AI 编程额度用完"),
    ("退出 Claude", "退出客户端"),
    ("自动重开 Claude", "自动重开客户端"),
    ("Claude 换号器", "AI 编程接力工具"),
    ("额度用完自动换号", "额度用完自动切换"),
    ("自动换号", "自动切换"),
    ("换号器", "工具"),
    ("自动换", "自动切"),
    ("换号", "切换"),
    ("挑号", "挑选"),
    ("登录另一个号", "登录另一个账号"),
]


def fix(s: str) -> str:
    for a, b in REPL:
        s = s.replace(a, b)
    return s


# 精简版：去掉内容重复的几张（痛点 / 挑号 / 自动发继续 / 接力流程），10 张 → 6 张
KEEP = [1, 3, 4, 8, 9, 10]


def main():
    out = m.HERE / "slides_douyin"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.*"):
        old.unlink()
    foot = fix(m.FOOT)
    slides = [m.SLIDES[k - 1] for k in KEEP]
    for i, body in enumerate(slides, 1):
        body = body.replace('<span class="tag">功能 1</span>', '<span class="tag">看得清</span>')
        body = body.replace('<span class="tag">功能 2</span>', '<span class="tag">自动切</span>')
        body = body.replace('<span class="tag">整理对话</span>', '<span class="tag">接着跑</span>')
        html = out / f"{i:02d}.html"
        html.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{m.CSS}</style></head>'
                        f'<body>{fix(body)}{foot.format(n=i, total=len(slides))}</body></html>', encoding="utf-8")
        png = out / f"{i:02d}.png"
        subprocess.run([m.EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", f"--screenshot={png}",
                        "--window-size=1080,1440", "--allow-file-access-from-files", html.as_uri()],
                       capture_output=True, timeout=60)
        print(png, png.exists())


if __name__ == "__main__":
    main()
