[CmdletBinding()]
param([string]$VisualStudioDir = '', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (!$OutputDirectory) { $OutputDirectory = Join-Path $root 'runs/native-build' }
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $out | Out-Null
$minhook = Join-Path $root '.deps/minhook'
# Reuse an imported x64 toolchain instead of repeatedly growing its environment.
$reuseToolchain = !$VisualStudioDir -and $env:VSCMD_ARG_TGT_ARCH -eq 'x64' -and $env:VCToolsInstallDir -and (Get-Command cl.exe -ErrorAction SilentlyContinue)
if (!$reuseToolchain) {
    if (!$VisualStudioDir) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
        $VisualStudioDir = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    }
    if (!$VisualStudioDir) { throw 'Visual Studio C++ tools required' }
    $vcvars = Join-Path $VisualStudioDir 'VC/Auxiliary/Build/vcvars64.bat'
    foreach ($line in (& $env:ComSpec /d /c ('call "' + $vcvars + '" >nul 2>&1 && set'))) {
        if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
    }
    if ($LASTEXITCODE) { throw 'vcvars64 failed' }
}
Push-Location $out
try {
    $dll = Join-Path $out 'iron_bridge.dll'
    if (Test-Path $dll) { [IO.File]::Open($dll, 'Open', 'ReadWrite', 'None').Dispose() }
    $common = @('/nologo','/O2','/MT','/W4','/utf-8','/EHsc','/std:c++20')
    $objects = @()
    foreach ($source in @('buffer.c','hook.c','trampoline.c','hde/hde64.c')) {
        $obj = ([IO.Path]::GetFileNameWithoutExtension($source)) + '.obj'
        & cl.exe /nologo /O2 /MT /W3 /c (Join-Path $minhook ('src/' + $source)) ('/Fo' + $obj)
        if ($LASTEXITCODE) { throw "MinHook compile failed: $source" }; $objects += $obj
    }
    $sources = @((Join-Path $PSScriptRoot 'bridge.cpp'))
    $defines = @()
    if (Test-Path (Join-Path $PSScriptRoot 'command_move.cpp')) { $sources += Join-Path $PSScriptRoot 'command_move.cpp'; $defines += '/DIRON_HAS_NATIVE_MOVE' }
    & cl.exe @common /LD @defines ('/I' + (Join-Path $minhook 'include')) @sources @objects /link bcrypt.lib /OUT:iron_bridge.dll
    if ($LASTEXITCODE) { throw 'Bridge compile failed' }
    & cl.exe @common (Join-Path $PSScriptRoot 'test_host.cpp') /link /OUT:iron_bridge_test_host.exe
    if ($LASTEXITCODE) { throw 'Synthetic host compile failed' }
    & cl.exe @common (Join-Path $PSScriptRoot 'injector.cpp') /link /OUT:iron_injector.exe
    if ($LASTEXITCODE) { throw 'Injector compile failed' }
    Write-Output "Built $dll"
} finally { Pop-Location }
