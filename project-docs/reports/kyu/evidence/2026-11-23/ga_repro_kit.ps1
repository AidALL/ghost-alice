# Ghost-ALICE Windows 재현 키트
#
# 보고서 4절(발견한 문제)의 4-1, 4-2, 4-3, 4-7 을 같은 순서로 실행하고
# 결과를 파일 하나(ga-repro-<이름>-<날짜>.txt)로 모은다. 아무 설정도 바꾸지 않는다.
#
# 사용법 (PowerShell 5.1 또는 7, 파일 위치에서):
#   powershell -ExecutionPolicy Bypass -File .\ga_repro_kit.ps1 -Name jy
#   powershell -ExecutionPolicy Bypass -File .\ga_repro_kit.ps1 -Name sk -Repeat 10
#
# 매개변수
#   -Name        결과 파일에 붙일 이름 (kyu / jy / sk)
#   -Repeat      4-7 테스트 반복 횟수 (기본 10, 0이면 건너뜀)
#   -AutopilotTestRepo  4-7 테스트를 실행할 Autopilot 체크아웃 (기본: autopilot/main 을 받아 둔 폴더)

param(
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$Repeat = 10,
    [string]$AutopilotTestRepo = ''
)

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$outFile = Join-Path $PSScriptRoot "ga-repro-$Name-$stamp.txt"
$lines = New-Object System.Collections.Generic.List[string]
function Log([string]$text) { $lines.Add($text); Write-Host $text }
function Section([string]$title) { Log ''; Log ('=' * 70); Log $title; Log ('=' * 70) }
function Run([string]$exe, [string[]]$argv, [string]$stdin = $null) {
    $psi = New-Object Diagnostics.ProcessStartInfo $exe
    foreach ($a in $argv) { [void]$psi.ArgumentList.Add($a) }
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true; $psi.UseShellExecute = $false
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $p = [Diagnostics.Process]::Start($psi)
    if ($stdin) { $p.StandardInput.Write($stdin) }
    $p.StandardInput.Close()
    $o = $p.StandardOutput.ReadToEnd(); $e = $p.StandardError.ReadToEnd(); $p.WaitForExit()
    return @{ code = $p.ExitCode; out = $o.Trim(); err = $e.Trim() }
}
if ($PSVersionTable.PSVersion.Major -lt 6) {
    # Windows PowerShell 5.1 has no ProcessStartInfo.ArgumentList; build a quoted string instead.
    function Run([string]$exe, [string[]]$argv, [string]$stdin = $null) {
        $psi = New-Object Diagnostics.ProcessStartInfo $exe
        $psi.Arguments = ($argv | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $psi.RedirectStandardInput = $true; $psi.UseShellExecute = $false
        $psi.StandardOutputEncoding = [Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
        $p = [Diagnostics.Process]::Start($psi)
        if ($stdin) { $p.StandardInput.Write($stdin) }
        $p.StandardInput.Close()
        $o = $p.StandardOutput.ReadToEnd(); $e = $p.StandardError.ReadToEnd(); $p.WaitForExit()
        return @{ code = $p.ExitCode; out = $o.Trim(); err = $e.Trim() }
    }
}

$ghost = Join-Path $env:USERPROFILE '.ghost-alice'
$work = Join-Path $env:TEMP ("ga-repro-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory $work | Out-Null

# 훅이 쓰는 Python: 설치된 훅 명령의 첫 실행 파일. 못 찾으면 PATH 의 python.
$hookPython = $null
try {
    $settings = Get-Content (Join-Path $env:USERPROFILE '.claude\settings.json') -Raw -Encoding UTF8
    $m = [regex]::Match($settings, '"command"\s*:\s*"([^"]*?python[^"]*?\.exe)')
    if ($m.Success) { $hookPython = $m.Groups[1].Value -replace '\\\\', '\' -replace '/', '\' }
} catch {}
if (-not $hookPython -or -not (Test-Path $hookPython)) { $hookPython = (Get-Command python -ErrorAction SilentlyContinue).Source }

# ---------------------------------------------------------------- 0. 환경
Section "0. 환경 ($Name, $(Get-Date -Format 'yyyy-MM-dd HH:mm'))"
$os = Get-CimInstance Win32_OperatingSystem
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$nls = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage'
Log "OS: $($os.Caption) $($cv.DisplayVersion) ($($os.Version), build $($os.BuildNumber))"
Log "언어/지역: UI $((Get-UICulture).Name), 형식 $((Get-Culture).Name), 시스템 로캘 $((Get-WinSystemLocale).Name)"
Log "코드 페이지: ANSI $($nls.ACP), OEM $($nls.OEMCP)"
Log "이 스크립트를 실행한 셸: PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
$ps5 = Run 'powershell.exe' @('-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()')
Log "Windows PowerShell: $($ps5.out)"
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwsh) { Log "PowerShell 7: $((Run $pwsh.Source @('-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()')).out)" } else { Log 'PowerShell 7: 없음' }
Log "Git: $((Run 'git' @('--version')).out)"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) { Log "Claude Code: $((Run 'cmd.exe' @('/c', 'claude', '--version')).out)" } else { Log 'Claude Code: PATH 에 없음' }
$pyInfo = "import sys,locale,platform;print(platform.python_version(), sys.executable, 'encoding=' + locale.getpreferredencoding(False), 'utf8_mode=' + str(sys.flags.utf8_mode))"
Log "훅용 Python: $((Run $hookPython @('-c', $pyInfo)).out)"
$pathPy = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pathPy -and $pathPy -ne $hookPython) { Log "PATH 기본 Python: $((Run $pathPy @('-c', $pyInfo)).out)" }
Log "PYTHONUTF8 환경 변수: $(if ($env:PYTHONUTF8) { $env:PYTHONUTF8 } else { '(없음)' })"

# 설치 버전
try {
    $state = Get-Content (Join-Path $ghost 'install-state\claude.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Log "설치된 Core: $($state.source_branch) @ $($state.source_head) (설치 $($state.installed_at))"
    $coreNow = (Run 'git' @('-C', $state.source_root, 'rev-parse', 'HEAD')).out
    Log "Core 폴더 현재 커밋: $coreNow $(if ($coreNow -eq $state.source_head) { '(설치와 같음)' } else { '(설치와 다름)' })"
} catch { Log "설치 기록을 읽지 못함: $_" }
$adapterScript = $null
try {
    $registry = Get-Content (Join-Path $ghost 'addons\claude\autopilot-mode.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $adapterScript = ($registry.provided | Where-Object { $_.target -like '*autopilot_mode.py' } | Select-Object -First 1).target
    $apRepo = Split-Path (Split-Path (Split-Path (Split-Path (Split-Path $adapterScript))))
    Log "설치된 Autopilot: $apRepo @ $((Run 'git' @('-C', $apRepo, 'rev-parse', 'HEAD')).out) ($((Run 'git' @('-C', $apRepo, 'describe', '--tags', '--always')).out))"
} catch { Log "Autopilot 등록 정보를 읽지 못함: $_" }

# 기준 버전 판정 (팀 기준: Core c6019ac = 태그 test-baseline-2026-11-16, Autopilot 2075768 = v0.5.1)
$coreOk = ($state -and "$($state.source_head)".StartsWith('c6019ac'))
$apHead = if ($apRepo) { (Run 'git' @('-C', $apRepo, 'rev-parse', 'HEAD')).out } else { '' }
$apOk = $apHead.StartsWith('2075768')
Log ("기준 버전 판정: Core " + $(if ($coreOk) { '일치' } else { '불일치' }) + ", Autopilot " + $(if ($apOk) { '일치' } else { '불일치' }))
if (-not ($coreOk -and $apOk)) {
    Log '  → 기준 버전이 아닙니다. 이 결과는 참고용입니다. 팀 저장소 project-docs/README.md 2절 순서로 기준 버전을 설치한 뒤 키트를 다시 실행하세요.'
}

# ---------------------------------------------------------------- 4-1
Section '4-1. 실행 폴더 자리에 일반 파일이 있을 때'
$probe = @'
import os, tempfile
from pathlib import Path
d = Path(tempfile.mkdtemp()); f = d / "run-dir"; f.write_text("occupied")
for label, op in [("open(file/'x')", lambda: open(f / "x")), ("os.listdir(file)", lambda: os.listdir(f))]:
    try: op(); print(label, "-> no error")
    except OSError as e: print(label, "->", type(e).__name__, "errno", e.errno, "winerror", getattr(e, "winerror", None))
'@
$probeFile = Join-Path $work 'probe41.py'; [IO.File]::WriteAllText($probeFile, $probe, (New-Object Text.UTF8Encoding($false)))
Log ((Run $hookPython @($probeFile)).out)
if ($adapterScript -and (Test-Path $adapterScript)) {
    $runFile = Join-Path $work 'not-a-directory'; Set-Content $runFile 'occupied'
    $env:GHOST_ALICE_AUTOPILOT_RUN_DIR = $runFile
    $env:GHOST_ALICE_CORE_ROOT = $state.source_root
    $r = Run $hookPython @($adapterScript) '{"hook_event_name":"Stop"}'
    Remove-Item Env:GHOST_ALICE_AUTOPILOT_RUN_DIR, Env:GHOST_ALICE_CORE_ROOT -ErrorAction SilentlyContinue
    Log "어댑터 출력 (종료 코드 $($r.code)): $($r.out)"
    if ($r.err) { Log "어댑터 stderr: $($r.err)" }
    Log ("판정: " + $(if ($r.out -match '"recovery"') { 'recovery 안내 있음 (Linux와 같은 동작)' } else { 'recovery 안내 없음 (문제 재현)' }))
} else { Log '어댑터 파일을 찾지 못해 건너뜀' }

# ---------------------------------------------------------------- 4-2
Section '4-2. 텍스트 모드로 넘긴 완료 문구의 줄바꿈'
$probe42 = @'
import subprocess, sys
child = "import sys; data = sys.stdin.buffer.read(); print(repr(data))"
sent = "line1\nline2\n"
r = subprocess.run([sys.executable, "-c", child], input=sent, text=True, capture_output=True)
print("보낸 문자열:", repr(sent))
print("자식이 받은 바이트:", r.stdout.strip())
print("판정:", "CRLF로 바뀜 (문제 재현 조건)" if "\\r\\n" in r.stdout else "LF 그대로")
'@
$probe42File = Join-Path $work 'probe42.py'; [IO.File]::WriteAllText($probe42File, $probe42, (New-Object Text.UTF8Encoding($false)))
$r = Run $hookPython @($probe42File)
Log $r.out; if ($r.err) { Log $r.err }

# ---------------------------------------------------------------- 4-3
Section '4-3. Core 응답 검토 도구에 한국어 파일 넘기기'
$tool = Join-Path $ghost 'runtime\current\_shared\completion_assessment.py'
if (Test-Path $tool) {
    $resp = Join-Path $work 'response.md'
    [IO.File]::WriteAllText($resp, "작업을 완료했어요.`n", (New-Object Text.UTF8Encoding($false)))
    $saved = $env:PYTHONUTF8; Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    $r1 = Run $hookPython @($tool, 'prepare-response', '--response-file', $resp)
    Log "기본 설정 (종료 코드 $($r1.code)): $($r1.out) $($r1.err)"
    $env:PYTHONUTF8 = '1'
    $r2 = Run $hookPython @($tool, 'prepare-response', '--response-file', $resp)
    Log "PYTHONUTF8=1 (종료 코드 $($r2.code)): $($r2.out) $($r2.err)"
    if ($saved) { $env:PYTHONUTF8 = $saved } else { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    Log ("판정: " + $(if ("$($r1.out)$($r1.err)" -match 'codec can''t decode') { '인코딩 오류 재현' } else { '인코딩 오류 없음' }))
} else { Log "도구를 찾지 못함: $tool" }

# ---------------------------------------------------------------- 4-7
Section "4-7. 병렬 상태 갱신 테스트 반복 실행 ($Repeat 회)"
if ($Repeat -gt 0) {
    if (-not $AutopilotTestRepo) {
        $candidates = @((Join-Path $env:USERPROFILE 'ghost-alice-autopilot-win'), (Join-Path $env:USERPROFILE 'ghost-alice-autopilot'))
        $AutopilotTestRepo = $candidates | Where-Object { Test-Path (Join-Path $_ 'tests\test_autopilot_state.py') } | Select-Object -First 1
    }
    $runner = Join-Path $AutopilotTestRepo 'scripts\run_windows_tests.ps1'
    if ($AutopilotTestRepo -and (Test-Path $runner)) {
        Log "테스트 폴더: $AutopilotTestRepo @ $((Run 'git' @('-C', $AutopilotTestRepo, 'rev-parse', '--short', 'HEAD')).out) ($((Run 'git' @('-C', $AutopilotTestRepo, 'branch', '--show-current')).out))"
        # 4-7 needs pytest. Keep a PYTHON the user already set; otherwise pick the first
        # candidate that has pytest (hook Python, then PATH python). Report the choice.
        $testPython = $null
        foreach ($cand in @($env:PYTHON, $hookPython, $pathPy) | Where-Object { $_ } | Select-Object -Unique) {
            if ((Run $cand @('-c', 'import pytest')).code -eq 0) { $testPython = $cand; break }
        }
        if (-not $testPython) {
            Log 'pytest 가 있는 Python 을 찾지 못해 4-7 을 건너뜀. 다음 중 하나를 한 뒤 키트를 다시 실행하세요:'
            Log "  $hookPython -m pip install pytest"
            Log '  또는 pytest 가 설치된 Python 경로를 $env:PYTHON 에 지정'
            $Repeat = 0
        } else {
            $env:PYTHON = $testPython
            $pyInfo47 = (Run $testPython @('-c', 'import sys, pytest; print(sys.version.split()[0], sys.executable, "pytest", pytest.__version__)')).out
            Log "4-7 실행 Python: $pyInfo47"
        }
        $test = 'tests\test_autopilot_state.py::AutopilotStateTest::test_parallel_advance_claims_ready_item_once'
        $fails = 0
        for ($i = 1; $i -le $Repeat; $i++) {
            Push-Location $AutopilotTestRepo
            $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $runner $test -q --tb=long 2>&1 | Out-String
            Pop-Location
            $passed = $out -match '1 passed'
            if (-not $passed) { $fails++; Log "[$i] 실패. 전체 출력:"; Log $out } else { Log "[$i] 통과" }
        }
        if ($Repeat -gt 0) { Log "판정: $Repeat 회 중 실패 $fails 회" }
    } else {
        Log 'Autopilot 테스트 폴더가 없어 4-7 을 건너뜀. 아래 명령으로 만든 뒤 키트를 다시 실행하세요 (설치된 Ghost-ALICE에는 영향 없음):'
        Log '  git clone -b autopilot/main https://github.com/bogop8-star/ghost-alice.git $HOME\ghost-alice-autopilot-win'
        Log '  이미 다른 위치에 있으면: -AutopilotTestRepo <그 폴더 경로>'
    }
} else { Log '건너뜀 (-Repeat 0)' }

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
[IO.File]::WriteAllLines($outFile, $lines, (New-Object Text.UTF8Encoding($true)))
Write-Host ''
Write-Host "결과 파일: $outFile" -ForegroundColor Cyan
Write-Host '보내기 전에 파일을 열어 개인 정보(경로의 사용자 이름 등)를 확인하세요.' -ForegroundColor Cyan
