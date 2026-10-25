


[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Ref = "latest",
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
if ($Ref -eq "latest") {
    $response = Invoke-WebRequest "$RepoUrl/releases/latest" -UseBasicParsing
    $uri = $response.BaseResponse.ResponseUri
    if (-not $uri) { $uri = $response.BaseResponse.RequestMessage.RequestUri }
    $Ref = $uri.Segments[-1]
}
if ($Ref -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "bootstrap: -Ref must name a release such as v0.27.0; source branches require artifact preparation"
}
$version = $Ref.Substring(1)
$wheel = "$RepoUrl/releases/download/$Ref/basicly-$version-py3-none-any.whl"
uv tool run --python '>=3.14' --from $wheel basicly install @InstallArgs
if ($LASTEXITCODE -ne 0) {
    throw "bootstrap: basicly install failed with exit code $LASTEXITCODE"
}
