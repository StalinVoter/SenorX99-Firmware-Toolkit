[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not (Get-Command WindowsSandbox.exe -ErrorAction SilentlyContinue)) {
    throw 'Windows Sandbox is not enabled on this computer. No Windows features were changed.'
}
$Reports = Join-Path $PSScriptRoot 'build\sandbox-results'
New-Item -ItemType Directory -Path $Reports -Force | Out-Null
$SourceXml = [Security.SecurityElement]::Escape($PSScriptRoot)
$ReportsXml = [Security.SecurityElement]::Escape($Reports)
$Configuration = Join-Path $PSScriptRoot 'build\runtime-test.wsb'
@"
<Configuration>
  <Networking>Disable</Networking>
  <MappedFolders>
    <MappedFolder><HostFolder>$SourceXml</HostFolder><SandboxFolder>C:\PatcherSource</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$ReportsXml</HostFolder><SandboxFolder>C:\PatcherReports</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\PatcherSource\TEST-PACKAGED-RUNTIME.ps1 -Distribution C:\PatcherSource\dist -Reports C:\PatcherReports</Command></LogonCommand>
</Configuration>
"@ | Set-Content -LiteralPath $Configuration -Encoding UTF8
Start-Process -FilePath 'WindowsSandbox.exe' -ArgumentList ('"{0}"' -f $Configuration)
Write-Host "Sandbox results will appear in: $Reports"
