# 切换账号后自动在 Claude 桌面版的「总控」对话里发一句话（默认“继续”）。
# 原则：确认不了打开的就是目标对话，就不打字、不回车。
#
# 用法：
#   有总控：auto_continue.ps1 -SessionId local_xxx -ExpectTitle 总控 -Text 继续
#           打开后核对：窗口标题是「总控 - Claude Code」、只有一个输入框、输入框是空的，才打字；回车前再核对一次。
#   没总控：auto_continue.ps1 -Folder C:\work\demo -Text 继续
#           用 claude://code/new?folder=…&q=继续 新开；输入框里正好预填了「继续」才回车（自己不打字）。
#   -NoSend      只打字不回车（测试用），结束后清掉自己打的字。
#   -NoNavigate  不跳转，直接核对当前窗口（测试用）。
#   -MockFile / -MockLog  单元测试用：不碰真窗口，按 JSON 里的假界面走，把做过的动作写到 MockLog。
# 退出码：0 已发送（-NoSend 时为已打字）；2 等不到输入框（多半没登录）；
#         3 输入框里的字对不上，没回车；4 深链接失败；5 没确认是目标对话，没打字没回车
param(
    [string]$SessionId = "",
    [string]$Folder = "",
    [string]$Text = "继续",
    [string]$ExpectTitle = "",
    [int]$TimeoutSec = 90,
    [int]$VerifySec = 15,
    [switch]$NoSend,
    [switch]$NoNavigate,
    [string]$MockFile = "",
    [string]$MockLog = ""
)
$ErrorActionPreference = "Stop"
$PromptNames = @('Prompt', ([string][char]0x63D0 + [char]0x793A + [char]0x8BCD), 'Perintah')   # Prompt / 提示词 / Perintah
$RunLog = Join-Path $env:USERPROFILE ".claude-instances\_auto_continue.log"
$Mock = $null
$Actions = New-Object System.Collections.ArrayList

function Finish([int]$code, [string]$msg) {
    Write-Output $msg
    if (-not $MockFile) {
        # 每次结果记一行，没发出去时能查原因
        try { Add-Content -LiteralPath $RunLog -Encoding UTF8 -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " code=$code $msg") } catch {}
    }
    if ($MockLog) {
        @{ code = $code; msg = $msg; actions = @($Actions) } | ConvertTo-Json -Depth 5 |
            Set-Content -LiteralPath $MockLog -Encoding UTF8
    }
    exit $code
}

if ($MockFile) {
    # 假界面：before = 跳转前的样子；after = 跳转后的样子（深链接失效就和 before 一样）
    $Mock = Get-Content -LiteralPath $MockFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $script:MState = $Mock.before
    $script:MTyped = $null
} else {
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class UniType {
    [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION u; }
    [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    public static void Type(string s) {
        foreach (char c in s) {
            INPUT[] arr = new INPUT[2];
            arr[0].type = 1; arr[0].u.ki.wScan = c; arr[0].u.ki.dwFlags = 0x0004;            // KEYEVENTF_UNICODE
            arr[1].type = 1; arr[1].u.ki.wScan = c; arr[1].u.ki.dwFlags = 0x0004 | 0x0002;   // + KEYUP
            uint r = SendInput(2, arr, Marshal.SizeOf(typeof(INPUT))); if (r != 2) throw new Exception("SendInput=" + r);
            System.Threading.Thread.Sleep(15);
        }
    }
}
'@
    $A = [System.Windows.Automation.AutomationElement]
    $TS = [System.Windows.Automation.TreeScope]
}

function Get-ClaudeMainPids {
    Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
        Where-Object { $_.CommandLine -notmatch '--type=' } |
        ForEach-Object { [int]$_.ProcessId }
}

function Read-EditText($edit) {
    try { return [string]$edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value }
    catch {
        try { return [string]$edit.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(2000) }
        catch { return $null }
    }
}

# 看一眼界面：可见输入框有几个、所在窗口的网页标题（就是当前对话名）、输入框里的字
function Get-UiState {
    if ($Mock) {
        $s = $script:MState
        $t = if ($script:MTyped -ne $null) { $script:MTyped } else { [string]$s.text }
        return @{ Prompts = [int]$s.prompts; Title = [string]$s.title; Text = $t; Edit = $null; Window = $null }
    }
    $edits = @(); $title = $null; $win = $null
    foreach ($procId in Get-ClaudeMainPids) {
        $pc = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $procId)
        foreach ($w in $A::RootElement.FindAll($TS::Children, $pc)) {
            $ec = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
            # 输入框的名字跟着界面语言变（英文 Prompt、中文 提示词…）。先按名字认；都不认识时，窗口里只有一个可见输入框才用它
            $vis = @($w.FindAll($TS::Descendants, $ec) | Where-Object { -not $_.Current.IsOffscreen })
            $mine = @($vis | Where-Object { $PromptNames -contains $_.Current.Name })
            if ($mine.Count -eq 0 -and $vis.Count -eq 1) { $mine = $vis }
            if ($mine.Count -gt 0) {
                $edits += $mine; $win = $w
                $dc = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)
                foreach ($d in $w.FindAll($TS::Descendants, $dc)) {
                    if ($d.Current.AutomationId -eq 'RootWebArea' -and $d.Current.Name) { $title = [string]$d.Current.Name; break }
                }
            }
        }
    }
    $edit = if ($edits.Count -eq 1) { $edits[0] } else { $null }
    $txt = if ($edit) { Read-EditText $edit } else { $null }
    return @{ Prompts = $edits.Count; Title = $title; Text = $txt; Edit = $edit; Window = $win }
}

function Open-Url([string]$url) {
    [void]$Actions.Add("navigate:$url")
    if ($Mock) {
        if ($Mock.deeplinkThrows) { throw "deeplink" }
        $script:MState = $Mock.after; $script:MTyped = $null; return
    }
    Start-Process $url
}

function Focus-Edit($st) {
    [void]$Actions.Add("focus")
    if ($Mock) { if ($Mock.afterFocus) { $script:MState = $Mock.afterFocus }; return }   # 模拟用户中途切走
    $h = [IntPtr]$st.Window.Current.NativeWindowHandle
    [UniType]::ShowWindow($h, 9) | Out-Null
    [UniType]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 300
    $st.Edit.SetFocus()
    Start-Sleep -Milliseconds 300
}

function Type-Text([string]$s) {
    [void]$Actions.Add("type:$s")
    if ($Mock) { $script:MTyped = if ($Mock.typeResult -ne $null) { [string]$Mock.typeResult } else { $s }; return }
    [UniType]::Type($s)
    Start-Sleep -Milliseconds 400
}

function Clear-Edit {
    [void]$Actions.Add("clear")
    if ($Mock) { $script:MTyped = ""; return }
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    [System.Windows.Forms.SendKeys]::SendWait("{DEL}")
}

function Press-Enter {
    [void]$Actions.Add("enter")
    if ($Mock) { $script:MTyped = if ($Mock.enterIgnored) { $script:MTyped } else { "" }; return }
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
}

function Wait-Sec([double]$s) { if (-not $Mock) { Start-Sleep -Milliseconds ([int]($s * 1000)) } }

function Is-Target($st) {
    if ($st.Prompts -ne 1) { return $false }        # 没有输入框，或有好几个（分屏）都不算
    if (-not $st.Title) { return $false }
    return ($st.Title -eq $ExpectTitle) -or ($st.Title -match ('^' + [regex]::Escape($ExpectTitle) + ' - Claude( Code)?$'))
}

function Norm($s) { if ($s -eq $null) { return $null }; return ([string]$s).Trim() }

# 1) 等 Claude 起来并且已登录（有输入框）
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ($true) {
    $st = Get-UiState
    if ($st.Prompts -ge 1) { break }
    if ($Mock -or (Get-Date) -ge $deadline) { Finish 2 "no-prompt" }
    Start-Sleep -Seconds 1
}
Wait-Sec 2   # 让侧栏和会话列表加载完
$before = Get-UiState

if ($SessionId) {
    # ===== 有总控：打开它，核对标题后才打字 =====
    if (-not $ExpectTitle) { Finish 5 "no-expect-title" }
    if (-not $NoNavigate) {
        try { Open-Url "claude://code/continue?session=$SessionId" } catch { Finish 4 "deeplink-failed" }
    }
    $ok = $false
    $vd = (Get-Date).AddSeconds($VerifySec)
    while ($true) {
        Wait-Sec 1
        $st = Get-UiState
        if (Is-Target $st) { $ok = $true; break }
        if ($Mock -or (Get-Date) -ge $vd) { break }
    }
    if (-not $ok) { Finish 5 ("not-target: prompts=" + $st.Prompts + " title=[" + $st.Title + "]") }
    $cur = Norm $st.Text
    if ($cur) { Finish 3 "draft-not-empty: [$cur]" }   # 总控里有没发的草稿：不动它
    Focus-Edit $st
    $st = Get-UiState
    if (-not (Is-Target $st)) { Finish 5 ("lost-target-before-type: title=[" + $st.Title + "]") }
    Type-Text $Text
    $st = Get-UiState
    $cur = Norm $st.Text
    if ($cur -ne $Text) {
        if ((Is-Target $st) -and $cur) { Clear-Edit }   # 只在确认还是总控时清自己打的字
        Finish 3 "text-mismatch: [$cur]"
    }
} else {
    # ===== 没总控：新开对话，只认预填，自己不打字 =====
    if (-not $Folder) { Finish 5 "no-folder" }
    if ((Norm $before.Text) -eq $Text) { Finish 5 "already-prefilled-before-open" }  # 分不清是不是新对话
    $q = [uri]::EscapeDataString($Text)
    $f = [uri]::EscapeDataString($Folder)
    try { Open-Url "claude://code/new?folder=$f&q=$q" } catch { Finish 4 "deeplink-failed" }
    $ok = $false
    $vd = (Get-Date).AddSeconds($VerifySec)
    while ($true) {
        Wait-Sec 1
        $st = Get-UiState
        if ($st.Prompts -eq 1 -and (Norm $st.Text) -eq $Text) { $ok = $true; break }
        if ($Mock -or (Get-Date) -ge $vd) { break }
    }
    if (-not $ok) { Finish 5 ("new-not-prefilled: prompts=" + $st.Prompts + " text=[" + (Norm $st.Text) + "]") }
    Focus-Edit $st
}

if ($NoSend) {
    if ($SessionId) { Clear-Edit }    # 清掉自己打的字；预填的不是我们打的，不动
    Finish 0 ("typed-ok: [" + (Norm $st.Text) + "]")
}

# 回车前最后核对一次（不对就什么都不做，不去清别的对话里的字）
$st = Get-UiState
if ($SessionId -and -not (Is-Target $st)) { Finish 5 ("lost-target-before-enter: title=[" + $st.Title + "]") }
if ($st.Prompts -ne 1 -or (Norm $st.Text) -ne $Text) { Finish 3 ("text-changed-before-enter: [" + (Norm $st.Text) + "]") }
Press-Enter
Wait-Sec 0.8
$after = Get-UiState
if ((Norm $after.Text) -eq $Text) { Finish 3 "enter-not-taken" }
Finish 0 "sent"
