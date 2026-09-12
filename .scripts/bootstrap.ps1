


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
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "bootstrap: uv was installed but is not on PATH; open a new shell and re-run"
    }
}

$InstallArgs = @($InstallArgs | Where-Object { $_ -ne "--" })

Write-Host "bootstrap: installing basicly@$Ref"
uv tool run --from "git+$RepoUrl@$Ref" basicly install @InstallArgs
exit $LASTEXITCODE
