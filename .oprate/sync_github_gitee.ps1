[CmdletBinding()]
param(
    [string]$GitHubRemote = 'origin',
    [string]$GiteeRemote = 'gitee',
    [string]$GiteeUrl,
    [string]$Branch,
    [switch]$Push,
    [switch]$Force,
    [switch]$SkipFetch
)

$ErrorActionPreference = 'Stop'

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & git @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    return @($output)
}

function Test-GitRemote {
    param([Parameter(Mandatory = $true)][string]$Name)

    $remoteNames = @(Invoke-Git @('remote'))
    return $remoteNames -contains $Name
}

function Get-Count {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return 0 }
    return [int]$Value.Trim()
}

if (-not (Test-Path (Join-Path (Get-Location) '.git'))) {
    throw 'Run this script from the Git repository root.'
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
    $Branch = (Invoke-Git @('branch', '--show-current') | Select-Object -First 1).Trim()
}
if ([string]::IsNullOrWhiteSpace($Branch)) {
    throw 'Detached HEAD detected. Specify a local branch with -Branch.'
}

if (-not (Test-GitRemote -Name $GitHubRemote)) {
    throw "GitHub remote '$GitHubRemote' was not found. Use -GitHubRemote to specify its name."
}

if (-not (Test-GitRemote -Name $GiteeRemote)) {
    if ([string]::IsNullOrWhiteSpace($GiteeUrl)) {
        Write-Warning "Gitee remote '$GiteeRemote' was not found. Pass -GiteeUrl to add it."
    }
    else {
        Invoke-Git @('remote', 'add', $GiteeRemote, $GiteeUrl) | Out-Null
        Write-Host "Added Gitee remote '$GiteeRemote'."
    }
}

$remotes = @($GitHubRemote)
if (Test-GitRemote -Name $GiteeRemote) {
    $remotes += $GiteeRemote
}

$status = @(Invoke-Git @('status', '--short'))
if ($status.Count -gt 0) {
    Write-Warning 'The working tree has uncommitted changes. This script never commits them automatically.'
    $status | ForEach-Object { Write-Host "  $_" }
}
else {
    Write-Host 'Working tree is clean.'
}

if (-not $SkipFetch) {
    foreach ($remote in $remotes) {
        Write-Host "Fetching $remote ..."
        Invoke-Git @('fetch', '--prune', $remote) | Out-Null
    }
}

$localHead = (Invoke-Git @('rev-parse', $Branch) | Select-Object -First 1).Trim()
Write-Host "Local branch: $Branch ($localHead)"

$canPush = $true
foreach ($remote in $remotes) {
    $remoteRef = "$remote/$Branch"
    & git rev-parse --verify --quiet $remoteRef *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$remote`: remote branch does not exist and will be created on push."
        continue
    }

    $countLine = (Invoke-Git @('rev-list', '--left-right', '--count', "$remoteRef...$Branch") | Select-Object -First 1)
    $counts = $countLine.Trim().Split("`t")
    $behind = Get-Count $counts[0]
    $ahead = Get-Count $counts[1]

    if ($behind -eq 0 -and $ahead -eq 0) {
        Write-Host "$remote`: synchronized."
    }
    elseif ($ahead -gt 0 -and $behind -eq 0) {
        Write-Host "$remote`: local branch is ahead by $ahead commit(s)."
    }
    elseif ($behind -gt 0 -and $ahead -eq 0) {
        Write-Warning "$remote`: remote branch is ahead by $behind commit(s). Pull and resolve before pushing."
        $canPush = $false
    }
    else {
        Write-Warning "$remote`: branches diverged (local ahead $ahead, remote ahead $behind)."
        $canPush = $false
    }
}

if (-not $Push) {
    Write-Host 'Check complete. Nothing was pushed. Add -Push to synchronize remotes.'
    exit 0
}

if (-not $canPush -and -not $Force) {
    throw 'Push blocked because a remote is ahead or diverged. Use -Force only when replacement is intentional.'
}

foreach ($remote in $remotes) {
    $arguments = @('push', $remote, "$Branch`:$Branch")
    if ($Force) {
        $arguments = @('push', '--force-with-lease', $remote, "$Branch`:$Branch")
    }
    Write-Host "Pushing to $remote/$Branch ..."
    Invoke-Git $arguments | ForEach-Object { Write-Host $_ }
}

Write-Host 'GitHub/Gitee synchronization complete.'
