# Build Talk Man as a Windows desktop application.
# Output: dist-desktop\\TalkMan\\TalkMan.exe
[CmdletBinding()]
param(
    [switch]$Clean,
    [ValidatePattern('^dist-[a-z0-9-]+$')]
    [string]$OutputDirectory = 'dist-desktop',
    [string]$DependencyDirectory
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'talkman_app.py'
$dist = Join-Path $PSScriptRoot $OutputDirectory
$work = Join-Path $PSScriptRoot 'build'
$spec = Join-Path $work 'TalkMan.spec'
$candidates = @(
    "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    'py', 'python'
)

$python = $null
foreach ($candidate in $candidates) {
    if ($candidate -eq 'py' -or $candidate -eq 'python') {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $python = $candidate
            break
        }
    } elseif (Test-Path -LiteralPath $candidate) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw 'Python 3.10+ was not found. Install Python with Tcl/Tk support.'
}

& $python -c "import tkinter, sys; assert sys.version_info >= (3,10)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python is missing tkinter or is older than 3.10.'
}
$dependencyRoot = if ($DependencyDirectory) { $DependencyDirectory } elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'TalkMan-libs')) {
    $PSScriptRoot
} else {
    Join-Path $PSScriptRoot '..\TalkMan-COLOPLAY客户端'
}
$libs = Join-Path $dependencyRoot 'TalkMan-libs'
$deviceLibs = Join-Path $dependencyRoot 'TalkMan-coloplay-libs'
$env:PYTHONPATH = $deviceLibs + [IO.Path]::PathSeparator + $libs + [IO.Path]::PathSeparator + $env:PYTHONPATH
& $python -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is missing. Run: python -m pip install pyinstaller'
}

# This runtime imports tkinter but PyInstaller's Tcl probe fails and excludes it.
# Keep normal module analysis and collect Tcl/Tk at the standard runtime paths.
$pythonRoot = (& $python -c "import sys; print(sys.prefix)").Trim()
$hooks = Join-Path $dependencyRoot 'TalkMan-build-hooks'
if (-not (Test-Path -LiteralPath (Join-Path $libs 'sounddevice.py'))) {
    throw 'Install project dependencies from TalkMan-requirements.txt into TalkMan-libs first.'
}
& $python -c "import sounddevice, websocket, cffi, certifi"
if ($LASTEXITCODE -ne 0) { throw 'Audio dependencies could not be imported.' }
$tkArgs = @()
$tkFiles = @(
    @{ Path = Join-Path $pythonRoot 'DLLs\_tkinter.pyd'; Kind = 'binary'; Destination = '.' },
    @{ Path = Join-Path $pythonRoot 'DLLs\tcl86t.dll'; Kind = 'binary'; Destination = '.' },
    @{ Path = Join-Path $pythonRoot 'DLLs\tk86t.dll'; Kind = 'binary'; Destination = '.' },
    @{ Path = Join-Path $pythonRoot 'tcl\tcl8.6'; Kind = 'data'; Destination = '_tcl_data' },
    @{ Path = Join-Path $pythonRoot 'tcl\tk8.6'; Kind = 'data'; Destination = '_tk_data' }
)
foreach ($tkFile in $tkFiles) {
    if (Test-Path -LiteralPath $tkFile.Path) {
        $tkArgs += if ($tkFile.Kind -eq 'binary') {
            @('--add-binary', "$($tkFile.Path);$($tkFile.Destination)")
        } else {
            @('--add-data', "$($tkFile.Path);$($tkFile.Destination)")
        }
    }
}

if ($Clean) {
    $projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    foreach ($outputPath in @($dist, $work)) {
        $absoluteOutput = [IO.Path]::GetFullPath($outputPath)
        if (-not $absoluteOutput.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a directory outside the project: $absoluteOutput"
        }
    }
    Remove-Item -LiteralPath $dist, $work -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $dist, $work | Out-Null
$versionFile = Join-Path $work 'TalkMan-version.txt'
& $python -c "import sys; from pathlib import Path; from talkman_version import windows_resource, FULL_VERSION; Path(sys.argv[1]).write_text(windows_resource(), encoding='utf-8'); Path(sys.argv[2]).write_text('#define ProductVersion '+chr(34)+FULL_VERSION+chr(34), encoding='utf-8')" $versionFile (Join-Path $work 'TalkMan-version.iss')
if ($LASTEXITCODE -ne 0) { throw 'Could not generate version metadata.' }
$pyinstallerArgs = @(
    '--noconfirm', '--clean', '--windowed', '--onedir',
    '--name', 'TalkMan', '--version-file', $versionFile, '--distpath', $dist, '--workpath', $work,
    '--icon', (Join-Path $PSScriptRoot 'TalkMan-assets\TalkMan-COLOPLAY.ico'),
    '--specpath', $work, '--additional-hooks-dir', $hooks,
    '--paths', $libs, '--paths', $deviceLibs, '--collect-all', '_sounddevice_data', '--collect-submodules', 'comtypes',
    '--collect-submodules', 'websockets', '--hidden-import', 'regex',
    '--collect-all', 'libusb_package', '--collect-submodules', 'usb.backend',
    '--hidden-import', 'serial.tools.list_ports_windows', '--hidden-import', 'lupa.lua54',
    '--hidden-import', '_cffi_backend', '--hidden-import', 'comtypes',
    '--hidden-import', 'comtypes.client', '--hidden-import', 'comtypes.gen.UIAutomationClient',
    # Keep the source ASCII so Windows PowerShell 5.1 reads UTF-8 scripts correctly.
    '--collect-data', 'certifi', '--runtime-hook', (Join-Path $PSScriptRoot ([regex]::Unescape('TalkMan-\u8bc1\u4e66\u94a9\u5b50.py'))),
    '--add-data', ((Join-Path $PSScriptRoot 'TalkMan-assets') + ';TalkMan-assets'),
    '--add-data', ((Join-Path $PSScriptRoot ([regex]::Unescape('TalkMan-\u8bbe\u5907\u5361\u5e26'))) + ';' + [regex]::Unescape('TalkMan-\u8bbe\u5907\u5361\u5e26'))
) + $tkArgs + @($script)
& $python -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$exe = Join-Path $dist 'TalkMan\TalkMan.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "The expected executable was not found: $exe"
}
$requiredTkFiles = @(
    (Join-Path $dist 'TalkMan\_internal\_tkinter.pyd'),
    (Join-Path $dist 'TalkMan\_internal\tcl86t.dll'),
    (Join-Path $dist 'TalkMan\_internal\tk86t.dll'),
    (Join-Path $dist 'TalkMan\_internal\_tcl_data\init.tcl'),
    (Join-Path $dist 'TalkMan\_internal\_tk_data\tk.tcl')
)
foreach ($requiredTkFile in $requiredTkFiles) {
    if (-not (Test-Path -LiteralPath $requiredTkFile)) {
        throw "The packaged Tcl/Tk runtime is incomplete: $requiredTkFile"
    }
}
$pyz = Join-Path $work 'TalkMan\PYZ-00.pyz'
& $python -c "import sys; from pathlib import Path; from PyInstaller.archive.readers import ZlibArchiveReader; modules = ZlibArchiveReader(sys.argv[1]).toc; required = {'talkman_free_service', 'talkman_system_style', 'talkman_coloplay_focus', 'talkman_version', 'talkman_coloplay_scroll', 'tkinter', 'tkinter.ttk', 'talkman_qwen', 'talkman_ai_edit', 'talkman_conventions', 'talkman_conventions_ui', 'talkman_convention_learning', 'talkman_delivery_ui', 'talkman_translation', 'talkman_translation_ui', 'talkman_voice', 'talkman_asr', 'talkman_assets', 'talkman_asr_volc_baidu', 'talkman_asr_tencent_xunfei', 'talkman_shortcuts', 'talkman_magnifier', 'talkman_target', 'talkman_target_windows', 'talkman_settings_ui', 'talkman_settings_viewport', 'talkman_ui_widgets', 'talkman_editor', 'talkman_credentials', 'talkman_onboarding', 'talkman_hover_help', 'talkman_target_picker', 'talkman_sync', 'talkman_live_sync', 'sounddevice', 'websocket', 'ssl', 'wave', 'comtypes.gen.UIAutomationClient'}; missing = required - modules.keys(); assert not missing, f'Packaged modules are missing: {missing}'; assert any(name.startswith('comtypes.gen._944DE083_') for name in modules), 'Missing UI Automation interfaces'; root=Path(sys.argv[2]); assert list(root.rglob('*portaudio*.dll')), 'Missing PortAudio'; assert list(root.rglob('_cffi_backend*.pyd')), 'Missing icons'; assert list(root.rglob('cacert.pem')), 'Missing TLS certificates'" $pyz (Join-Path $dist 'TalkMan')
if ($LASTEXITCODE -ne 0) {
    throw 'The packaged tkinter modules are incomplete.'
}
Remove-Item -LiteralPath $spec -Force -ErrorAction SilentlyContinue
Write-Host "Build succeeded: $exe" -ForegroundColor Green
