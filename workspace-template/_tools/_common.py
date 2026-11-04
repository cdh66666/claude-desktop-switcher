r"""共用：找到本工作区在 ~/.claude/projects 下的对话记录。

Claude Code 把每个文件夹的对话存在 ~/.claude/projects/<编码后的路径>/*.jsonl，
编码规则：路径里除字母数字以外的字符都换成 "-"（如 D:\work → D--work）。
同一台电脑上所有账号共用这个文件夹，所以换号后也能读到其他账号的对话。
"""
import glob
import os
import re

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_files(workspace=WORKSPACE):
    root = os.path.expanduser('~/.claude/projects')
    key = re.sub(r'[^A-Za-z0-9]', '-', os.path.normpath(workspace))
    # 带 * 是为了把这个文件夹下的子目录、worktree 也算进来
    return glob.glob(os.path.join(root, glob.escape(key) + '*', '*.jsonl'))
