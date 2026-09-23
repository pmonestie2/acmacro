param([int]$Seconds = 30)
$ErrorActionPreference = 'Stop'
$captureDir = Join-Path $PSScriptRoot 'captures'
New-Item -ItemType Directory -Force -Path $captureDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$prefix = Join-Path $captureDir $stamp
$filters = @()
$started = $false
Start-Transcript -Path "$prefix.log" | Out-Null
try {
    & "$PSScriptRoot\probe_admin.ps1"
    $probe = Get-Content (Join-Path $captureDir 'admin_probe.json') -Raw | ConvertFrom-Json
    if (-not $probe.elevated) { throw 'Administrator access required' }
    $status = (& pktmon status 2>&1 | Out-String)
    $status | Write-Output
    if ($LASTEXITCODE -ne 0 -or $status -notmatch 'not running|stopped') { throw 'Packet Monitor is already active or its status is unknown; leaving it untouched' }
    $existing = (& pktmon filter list 2>&1 | Out-String)
    $existing | Write-Output
    if ($LASTEXITCODE -ne 0 -or $existing -notmatch 'No filters|(?m)^\s*None\s*$') { throw 'Existing Packet Monitor filters found; leaving them untouched' }
    $ports = @($probe.tcp | Where-Object { $_.RemotePort -ne 0 } | ForEach-Object { [pscustomobject]@{ transport='TCP'; port=$_.LocalPort } })
    $ports += @($probe.udp | ForEach-Object { [pscustomobject]@{ transport='UDP'; port=$_.LocalPort } })
    if (-not $ports.Count) { throw 'No game endpoints found' }
    foreach ($port in $ports) {
        $name = "acmacro_$stamp`_$($filters.Count)"
        & pktmon filter add $name -t $port.transport -p $port.port
        if ($LASTEXITCODE -ne 0) { throw 'Cannot install game endpoint filter' }
        $filters += $name
    }
    & pktmon start --capture --pkt-size 0 --file-size 64 --file-name "$prefix.etl"
    if ($LASTEXITCODE -ne 0) { throw 'Capture start failed' }
    $started = $true
    Set-Content -LiteralPath (Join-Path $captureDir 'capture_status.json') -Value (@{state='capturing';prefix=$prefix;seconds=$Seconds} | ConvertTo-Json)
    Start-Sleep -Seconds $Seconds
    & pktmon stop
    if ($LASTEXITCODE -ne 0) { throw 'Capture stop failed' }
    $started = $false
    & pktmon etl2pcap "$prefix.etl" --out "$prefix.pcapng"
    if ($LASTEXITCODE -ne 0) { throw 'PCAP conversion failed' }
    Set-Content -LiteralPath (Join-Path $captureDir 'capture_status.json') -Value (@{state='complete';prefix=$prefix} | ConvertTo-Json)
} catch {
    $_ | Out-String | Write-Output
    Set-Content -LiteralPath (Join-Path $captureDir 'capture_status.json') -Value (@{state='failed';prefix=$prefix;error=$_.Exception.Message} | ConvertTo-Json)
} finally {
    if ($started) { & pktmon stop }
    foreach ($name in $filters) { & pktmon filter remove $name }
    Stop-Transcript | Out-Null
}
