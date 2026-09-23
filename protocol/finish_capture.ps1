param([Parameter(Mandatory=$true)][string]$Prefix, [int]$OwnerId, [int]$DelaySeconds = 20)
$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds $DelaySeconds
$statusPath = Join-Path $PSScriptRoot 'captures\capture_status.json'
$current = Get-Content $statusPath -Raw | ConvertFrom-Json
if ($current.prefix -ne $Prefix -or $current.state -ne 'capturing') { exit }
$owner = Get-CimInstance Win32_Process -Filter "ProcessId=$OwnerId"
if ($owner -and $owner.CommandLine -like '*capture_admin.ps1*') {
    Stop-Process -Id $OwnerId -Force
}
& pktmon stop
if ($LASTEXITCODE -ne 0) { throw 'Capture stop failed' }
& pktmon etl2pcap "$Prefix.etl" --out "$Prefix.pcapng"
if ($LASTEXITCODE -ne 0) { throw 'Conversion failed' }
# The capture helper starts only with an empty filter set and owns these filters.
$stamp = Split-Path $Prefix -Leaf
$probe = Get-Content (Join-Path $PSScriptRoot 'captures\admin_probe.json') -Raw | ConvertFrom-Json
$count = @($probe.tcp | Where-Object { $_.RemotePort -ne 0 }).Count + @($probe.udp).Count
for ($index = 0; $index -lt $count; $index++) { & pktmon filter remove "acmacro_$stamp`_$index" }
@{state='complete';prefix=$Prefix;stopped_early=$true} | ConvertTo-Json | Set-Content $statusPath
