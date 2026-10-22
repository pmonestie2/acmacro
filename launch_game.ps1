param(
    [ValidateSet('normal', 'double')]
    [string]$Scale = 'normal',
    [ValidateSet(1, 2)]
    [int]$Slot = 1
)

$ErrorActionPreference = 'Stop'
$gameDirectory = if ($Slot -eq 1) { 'Earth & Beyond' } else { 'EnB-client-2' }
$gameClient = "C:\Program Files (x86)\EA GAMES\$gameDirectory\release\client.exe"
$proxy = 'C:\Program Files (x86)\Net-7\bin\net7proxy.exe'
if (!(Test-Path -LiteralPath $gameClient) -or !(Test-Path -LiteralPath $proxy)) {
    throw 'Game client or Net-7 proxy was not found.'
}

# Compatibility flags are per executable and take effect at the next launch.
# Match both long and legacy short paths; preserve unrelated compatibility flags.
if (-not ('GameLaunch.NativePaths' -as [type])) {
    Add-Type @'
using System.Runtime.InteropServices;
using System.Text;
namespace GameLaunch {
    public static class NativePaths {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        public static extern uint GetShortPathName(string path, StringBuilder output, uint size);
    }
}
'@
}
$shortBuffer = New-Object System.Text.StringBuilder 32768
$shortLength = [GameLaunch.NativePaths]::GetShortPathName($gameClient, $shortBuffer, 32768)
$clientPaths = @($gameClient)
if ($shortLength -gt 0 -and $shortLength -lt 32768) { $clientPaths += $shortBuffer.ToString() }
$layers = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'
if (!(Test-Path -LiteralPath $layers)) { New-Item -Path $layers -Force | Out-Null }
$dpiFlag = if ($Scale -eq 'normal') { 'HIGHDPIAWARE' } else { 'DPIUNAWARE' }
foreach ($clientPath in ($clientPaths | Select-Object -Unique)) {
    $oldFlags = Get-ItemPropertyValue -LiteralPath $layers -Name $clientPath -ErrorAction SilentlyContinue
    $keptFlags = @($oldFlags -split '\s+' | Where-Object {
        $_ -and $_ -notin @('~', 'HIGHDPIAWARE', 'DPIUNAWARE', 'GDIDPISCALING')
    })
    $newFlags = (@('~') + $keptFlags + @($dpiFlag)) -join ' '
    New-ItemProperty -LiteralPath $layers -Name $clientPath -Value $newFlags -PropertyType String -Force | Out-Null
}

# Only the game's executable gets a DPI override. Launch Net-7 from its bin
# directory, just like the macro: the proxy depends on relative support files.
Push-Location -LiteralPath (Split-Path -Parent $proxy)
try {
    & $proxy '/ADDRESS:216.219.87.147' "/CLIENT:$gameClient" '/SSL:443' '/POPT' '/DML'
} finally {
    Pop-Location
}
