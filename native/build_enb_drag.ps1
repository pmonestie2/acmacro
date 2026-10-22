param(
    [string]$Compiler = (Join-Path $PSScriptRoot '..\diagnostics\native_tools\tcc\tcc.exe'),
    [string]$WinApiInclude = (Join-Path $PSScriptRoot '..\diagnostics\native_tools\winapi\winapi-full-for-0.9.27\include\winapi'),
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\enb_drag.exe')
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'enb_drag.c'
$output = [IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath $Compiler)) {
    throw 'Supply -Compiler with the path to the 64-bit Tiny C Compiler tcc.exe.'
}
$includeArguments = @()
if (Test-Path -LiteralPath $WinApiInclude) { $includeArguments = @('-I', $WinApiInclude) }
& $Compiler @includeArguments -L $PSScriptRoot -Wall -Werror '-Wl,-subsystem=gui' -o $output $source -luser32 -lshell32
if ($LASTEXITCODE -ne 0) { throw 'Native compilation failed.' }
$test = Start-Process -FilePath $output -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
if ($test.ExitCode -ne 0) { throw "Native self-test failed: $($test.ExitCode)" }
Get-Item -LiteralPath $output | Select-Object FullName,Length
