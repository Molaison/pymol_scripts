<#
.SYNOPSIS
Runs the PymolPlugins Host smoke gate from a Windows-local temporary directory.

.EXAMPLE
.\run_host_smoke.ps1 `
  -PymolExe "C:\Program Files\PyMOL\PyMOLWin.exe" `
  -RepositoryPath "\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins" `
  -WorkerUrl "http://127.0.0.1:8765"

.DESCRIPTION
RepositoryPath may be a WSL UNC path. The launcher creates a local Python
bootstrap which calls cmd.run on that path, validates the versioned JSON
record, and fails unless every automatic check passes. Manual checks remain
explicitly pending in the record.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PymolExe,

    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [string]$OutputPath = "",

    [string]$WorkerUrl = "",

    [string[]]$PymolArguments = @("-cq"),

    [switch]$KeepFixtures
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ExecutablePath {
    param([Parameter(Mandatory = $true)][string]$Command)

    $resolved = Get-Command -Name $Command -ErrorAction Stop
    if ($resolved.Source) {
        return $resolved.Source
    }
    return $resolved.Path
}

$resolvedPymol = Resolve-ExecutablePath -Command $PymolExe
$resolvedRepository = Resolve-Path -LiteralPath $RepositoryPath -ErrorAction Stop
# `.Path` may retain PowerShell's provider-qualified UNC form
# (`Microsoft.PowerShell.Core\\FileSystem::...`), which Python/PyMOL cannot
# open. ProviderPath is the native filesystem path expected by cmd.run.
$repository = $resolvedRepository.ProviderPath
$hostScript = Join-Path $repository "tests\host\host_smoke.py"
if (-not (Test-Path -LiteralPath $hostScript -PathType Leaf)) {
    throw "Host smoke script not found: $hostScript"
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path ([System.IO.Path]::GetTempPath()) "PymolPlugins-host-smoke-$stamp.json"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path (Get-Location).Path $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

$tempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("PymolPluginsHostSmoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
$bootstrapPath = Join-Path $tempDirectory "run_host_smoke.py"

# JSON string syntax is also valid for a Python string literal and safely
# escapes UNC backslashes without relying on the PyMOL command parser.
$hostScriptLiteral = ConvertTo-Json -InputObject $hostScript -Compress
$bootstrap = "from pymol import cmd`ncmd.run($hostScriptLiteral)`n"
[System.IO.File]::WriteAllText(
    $bootstrapPath,
    $bootstrap,
    [System.Text.UTF8Encoding]::new($false)
)

$previousOutput = $env:PP_HOST_SMOKE_OUTPUT
$previousKeep = $env:PP_HOST_SMOKE_KEEP_FIXTURES
$previousAutorun = $env:PP_HOST_SMOKE_AUTORUN
$previousWorkerUrl = $env:PYMOLPLUGINS_WORKER_URL
$previousCheckWorker = $env:PP_HOST_SMOKE_CHECK_WORKER
$exitCode = $null
try {
    $env:PP_HOST_SMOKE_OUTPUT = $OutputPath
    $env:PP_HOST_SMOKE_KEEP_FIXTURES = if ($KeepFixtures) { "1" } else { "0" }
    $env:PP_HOST_SMOKE_AUTORUN = "1"
    if ([string]::IsNullOrWhiteSpace($WorkerUrl)) {
        $env:PP_HOST_SMOKE_CHECK_WORKER = "0"
    } else {
        $env:PYMOLPLUGINS_WORKER_URL = $WorkerUrl.TrimEnd("/")
        $env:PP_HOST_SMOKE_CHECK_WORKER = "1"
    }

    # cmd.exe and several PyMOL launch paths reject UNC working directories.
    # The process starts from this Windows-local temporary directory while the
    # bootstrap explicitly asks PyMOL to run the configurable repository path.
    Push-Location -LiteralPath $tempDirectory
    try {
        & $resolvedPymol @PymolArguments $bootstrapPath
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if ($null -eq $previousOutput) {
        Remove-Item Env:PP_HOST_SMOKE_OUTPUT -ErrorAction SilentlyContinue
    } else {
        $env:PP_HOST_SMOKE_OUTPUT = $previousOutput
    }
    if ($null -eq $previousKeep) {
        Remove-Item Env:PP_HOST_SMOKE_KEEP_FIXTURES -ErrorAction SilentlyContinue
    } else {
        $env:PP_HOST_SMOKE_KEEP_FIXTURES = $previousKeep
    }
    if ($null -eq $previousAutorun) {
        Remove-Item Env:PP_HOST_SMOKE_AUTORUN -ErrorAction SilentlyContinue
    } else {
        $env:PP_HOST_SMOKE_AUTORUN = $previousAutorun
    }
    if ($null -eq $previousWorkerUrl) {
        Remove-Item Env:PYMOLPLUGINS_WORKER_URL -ErrorAction SilentlyContinue
    } else {
        $env:PYMOLPLUGINS_WORKER_URL = $previousWorkerUrl
    }
    if ($null -eq $previousCheckWorker) {
        Remove-Item Env:PP_HOST_SMOKE_CHECK_WORKER -ErrorAction SilentlyContinue
    } else {
        $env:PP_HOST_SMOKE_CHECK_WORKER = $previousCheckWorker
    }
    Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "PyMOL did not produce a host-smoke JSON record (exit code: $exitCode)"
}

$record = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json
Write-Host "Host smoke record: $OutputPath"
Write-Host "Automatic status: $($record.automatic_status)"
Write-Host "Overall status: $($record.overall_status)"

if ($exitCode -ne 0) {
    throw "PyMOL exited with code $exitCode; inspect $OutputPath"
}
if ($record.schema -ne "pymolplugins.host-smoke/1.0") {
    throw "Unexpected host-smoke schema: $($record.schema)"
}
if ($record.automatic_status -ne "pass") {
    throw "Automatic Host smoke gate failed; inspect $OutputPath"
}

Write-Host "Automatic Host smoke gate passed. Manual checks remain pending in the JSON record."
