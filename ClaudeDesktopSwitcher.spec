# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置 — Claude Desktop Switcher

block_cipher = None

a = Analysis(
    ['claude_switcher.py'],
    pathex=[],
    binaries=[],
    # 资源文件：保留 assets/ 子目录结构
    datas=[('assets/claude-icon.ico', 'assets'), ('auto_continue.ps1', '.')],
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'auto_switch',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'pytest', 'IPython', 'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ClaudeDesktopSwitcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/claude-icon.ico',
    version=None,
)
