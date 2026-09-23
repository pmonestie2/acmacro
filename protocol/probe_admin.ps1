$ErrorActionPreference = 'Stop'
$captureDir = Join-Path $PSScriptRoot 'captures'
New-Item -ItemType Directory -Force -Path $captureDir | Out-Null
$resultPath = Join-Path $captureDir 'admin_probe.json'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ProtocolProcessProbe {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")]
    public static extern bool CloseHandle(IntPtr handle);
}
'@
    $processes = @(Get-Process client,net7proxy -ErrorAction SilentlyContinue)
    $probes = @(foreach ($process in $processes) {
        $handle = [ProtocolProcessProbe]::OpenProcess(0x1010, $false, $process.Id)
        $win32Error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $readable = $handle -ne [IntPtr]::Zero
        if ($readable) { [ProtocolProcessProbe]::CloseHandle($handle) | Out-Null; $win32Error = 0 }
        [pscustomobject]@{ name=$process.ProcessName; id=$process.Id; read_access=$readable; error=$win32Error }
    })
    $processIds = @($processes.Id)
    $tcp = @(Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in $processIds } | Select-Object OwningProcess,LocalAddress,LocalPort,RemoteAddress,RemotePort,State)
    $udp = @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in $processIds } | Select-Object OwningProcess,LocalAddress,LocalPort)
    [pscustomobject]@{ time=(Get-Date -Format o); elevated=$isAdmin; processes=$probes; tcp=$tcp; udp=$udp } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    [pscustomobject]@{ error=$_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
