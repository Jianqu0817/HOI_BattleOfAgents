[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dependency = Join-Path $root '.deps/minhook'
$revision = 'c3fcafdc10146beb5919319d0683e44e3c30d537'
if (!(Test-Path -LiteralPath $dependency)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root '.deps') | Out-Null
    & git clone 'https://github.com/TsudaKageyu/minhook.git' $dependency
    if ($LASTEXITCODE) { throw 'MinHook clone failed' }
    & git -C $dependency checkout --detach $revision
    if ($LASTEXITCODE) { throw 'MinHook checkout failed' }
}
$actual = & git -C $dependency rev-parse HEAD
if ($LASTEXITCODE -or $actual -ne $revision) { throw 'Existing MinHook revision differs; preserve it separately before setup' }
Write-Output "MinHook ready: $revision"
