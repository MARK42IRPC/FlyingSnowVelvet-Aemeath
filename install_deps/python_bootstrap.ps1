param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [Parameter(Mandatory = $true)]
    [string]$ResourceName,
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedSha256,
    [switch]$ResolveOnly
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Resolve-ManifestResourceUrls {
    param(
        [string]$Path,
        [string]$Name
    )

    $baseUrls = New-Object System.Collections.Generic.List[string]
    $directUrls = New-Object System.Collections.Generic.List[string]
    foreach ($rawLine in [IO.File]::ReadAllLines([IO.Path]::GetFullPath($Path), [Text.Encoding]::UTF8)) {
        $value = $rawLine.Trim()
        if (-not $value -or $value.StartsWith('#')) {
            continue
        }
        $uri = $null
        if (-not [Uri]::TryCreate($value, [UriKind]::Absolute, [ref]$uri)) {
            continue
        }
        if ($uri.Scheme -notin @('http', 'https')) {
            continue
        }
        $fileName = [Uri]::UnescapeDataString([IO.Path]::GetFileName($uri.AbsolutePath))
        if ($uri.AbsolutePath.EndsWith('/')) {
            $baseUrls.Add($value.TrimEnd('/') + '/') | Out-Null
        }
        elseif ($fileName -eq $Name) {
            $directUrls.Add($uri.AbsoluteUri) | Out-Null
        }
    }

    $urls = New-Object System.Collections.Generic.List[string]
    foreach ($url in $directUrls) {
        $urls.Add($url) | Out-Null
    }
    foreach ($baseUrl in $baseUrls) {
        $urls.Add(([Uri]::new([Uri]$baseUrl, [Uri]::EscapeDataString($Name))).AbsoluteUri) | Out-Null
    }
    return @($urls | Select-Object -Unique)
}

function Test-ExpectedHash {
    param(
        [string]$Path,
        [string]$Expected
    )
    if (-not [IO.File]::Exists($Path)) {
        return $false
    }
    $actual = Get-Sha256 -Path $Path
    return $actual.Equals($Expected, [StringComparison]::OrdinalIgnoreCase)
}

function Get-Sha256 {
    param([string]$Path)
    $stream = $null
    $hasher = $null
    try {
        $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
        $hasher = [Security.Cryptography.SHA256]::Create()
        $bytes = $hasher.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($hasher) { $hasher.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}

function Receive-ResourceFile {
    param(
        [string]$Url,
        [string]$PartPath
    )

    $offset = if ([IO.File]::Exists($PartPath)) { ([IO.FileInfo]$PartPath).Length } else { 0L }
    $request = [Net.HttpWebRequest][Net.WebRequest]::Create($Url)
    $request.UserAgent = 'FlyingSnowVelvetBootstrap/1.0'
    $request.Accept = 'application/octet-stream, */*'
    $request.Timeout = 30000
    $request.ReadWriteTimeout = 30000
    if ($offset -gt 0) {
        $request.AddRange($offset)
    }

    $response = $null
    $inputStream = $null
    $outputStream = $null
    try {
        $response = [Net.HttpWebResponse]$request.GetResponse()
        $append = $offset -gt 0 -and [int]$response.StatusCode -eq 206
        if (-not $append) {
            $offset = 0L
        }
        $mode = if ($append) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Open($PartPath, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = New-Object byte[] (256 * 1024)
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $count)
        }
    }
    finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

try {
    $urls = @(Resolve-ManifestResourceUrls -Path $ManifestPath -Name $ResourceName)
    if ($ResolveOnly) {
        $urls | ForEach-Object { Write-Output $_ }
        exit 0
    }
    if ($urls.Count -eq 0) {
        throw "Resource URL not found: $ResourceName"
    }

    $target = [IO.Path]::GetFullPath($TargetPath)
    $parent = [IO.Path]::GetDirectoryName($target)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    if (Test-ExpectedHash -Path $target -Expected $ExpectedSha256) {
        Write-Host "[INFO] Verified existing Python installer: $ResourceName"
        exit 0
    }
    if ([IO.File]::Exists($target)) {
        Remove-Item -LiteralPath $target -Force
        Write-Warning 'Existing Python installer failed SHA-256 verification and was removed.'
    }

    $part = $target + '.part'
    $lastError = 'all sources failed'
    for ($index = 0; $index -lt $urls.Count; $index++) {
        $url = $urls[$index]
        try {
            if (Test-ExpectedHash -Path $part -Expected $ExpectedSha256) {
                Move-Item -LiteralPath $part -Destination $target -Force
                Write-Host '[INFO] Reused and verified completed partial download.'
                exit 0
            }
            Write-Host "[INFO] Downloading $ResourceName [$($index + 1)/$($urls.Count)]"
            Write-Host "       $url"
            Receive-ResourceFile -Url $url -PartPath $part
            if (-not (Test-ExpectedHash -Path $part -Expected $ExpectedSha256)) {
                $actual = Get-Sha256 -Path $part
                Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                throw "SHA-256 mismatch: $actual"
            }
            Move-Item -LiteralPath $part -Destination $target -Force
            Write-Host '[INFO] Python installer download verified.'
            exit 0
        }
        catch {
            $lastError = $_.Exception.Message
            Write-Warning "Download source failed: $lastError"
        }
    }
    throw "Unable to download a verified Python installer: $lastError"
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
