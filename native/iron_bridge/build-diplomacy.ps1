[CmdletBinding()]
param([string]$VisualStudioDir = '', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (!$OutputDirectory) { $OutputDirectory = Join-Path $root 'runs/native-diplomacy-v2' }
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $out | Out-Null
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
Push-Location $out
try {
    $dll = Join-Path $out 'iron_diplomacy_v2.dll'
    if (Test-Path $dll) { [IO.File]::Open($dll, 'Open', 'ReadWrite', 'None').Dispose() }
    & cl.exe /nologo /O2 /MT /W4 /WX /utf-8 /EHsc /std:c++20 /LD (Join-Path $PSScriptRoot 'diplomacy.cpp') (Join-Path $PSScriptRoot 'diplomacy_sidecar.cpp') /link bcrypt.lib /OUT:iron_diplomacy_v2.dll
    if ($LASTEXITCODE) { throw 'Diplomacy sidecar compile failed' }
    & cl.exe /nologo /O2 /MT /W4 /WX /utf-8 /EHsc /std:c++20 (Join-Path $PSScriptRoot 'diplomacy.cpp') (Join-Path $PSScriptRoot 'diplomacy_test.cpp') /link /OUT:iron_diplomacy_test.exe
    if ($LASTEXITCODE) { throw 'Diplomacy tests compile failed' }
    & (Join-Path $out 'iron_diplomacy_test.exe')
    if ($LASTEXITCODE) { throw 'Diplomacy tests failed' }
    Write-Output "Built $dll"
} finally { Pop-Location }
