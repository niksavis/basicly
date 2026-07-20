#Requires -Version 5
<#
Bootstrap basicly into the current repo without a pre-installed uv/Python.

Usage (from the consumer repo root):
  powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/niksavis/basicly/main/.scripts/bootstrap.ps1 | iex"

To pin a version or pass install arguments, download first:
  irm .../bootstrap.ps1 -OutFile bootstrap.ps1
  ./bootstrap.ps1 -Ref v0.5.0 -- --technologies python,zsh

-Ref pins the basicly version (default: main); every other argument passes
through to `basicly install`. POSIX users: see bootstrap.sh.
#>
param(
    [string]$Ref = "main",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallArgs = @()
)
$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/niksavis/basicly"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "bootstrap: git is required"
}
git rev-parse --git-dir *> $null
if ($LASTEXITCODE -ne 0) {
    throw "bootstrap: run this from inside the consumer git repository"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "bootstrap: uv not found; installing it from astral.sh"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # The installer defaults to %USERPROFILE%\.local\bin; make sure this same
    # run can see the fresh binary.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "bootstrap: uv was installed but is not on PATH; open a new shell and re-run"
    }
}

# PowerShell keeps a literal "--" separator in remaining args; drop it.
$InstallArgs = @($InstallArgs | Where-Object { $_ -ne "--" })

Write-Host "bootstrap: installing basicly@$Ref"
uv tool run --from "git+$RepoUrl@$Ref" basicly install @InstallArgs
exit $LASTEXITCODE
