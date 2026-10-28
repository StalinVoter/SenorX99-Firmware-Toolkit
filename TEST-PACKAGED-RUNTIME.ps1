[CmdletBinding()]
param(
    [string]$Distribution,
    [string]$Reports
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 -File does not reliably initialize script automatic
# variables during parameter default evaluation. Resolve defaults in the body.
$Root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = [IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
}
if ([string]::IsNullOrWhiteSpace($Distribution)) {
    $Distribution = Join-Path $Root 'dist'
}
if ([string]::IsNullOrWhiteSpace($Reports)) {
    $Reports = Join-Path $Root 'build'
}
# Resolve explicit relative paths before elevation can change the working folder.
$Distribution = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Distribution)
$Reports = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Reports)
# Windows-only packaged tests begin here.
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit PowerShell.' }
New-Item -ItemType Directory -Path $Reports -Force | Out-Null
Start-Transcript -LiteralPath (Join-Path $Reports 'windows-runtime-test.log') -Force | Out-Null
$Results = New-Object 'Collections.Generic.List[object]'
$Success = $false
$Scratch = Join-Path $env:TEMP ('X99-Runtime-Test-' + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $Scratch | Out-Null
    foreach ($CaseName in @('gui','cli','conflicting-dlls','helper-transport')) {
        $CaseDir = Join-Path $Scratch $CaseName
        New-Item -ItemType Directory -Path $CaseDir | Out-Null
        $Name = 'SenorX99.exe'
        if ($CaseName -eq 'cli') { $Name = 'SenorX99-CLI.exe' }
        $SourceExe = Join-Path $Distribution $Name
        $TargetExe = Join-Path $CaseDir $Name
        Copy-Item -LiteralPath $SourceExe -Destination $TargetExe
        if ($CaseName -eq 'helper-transport') {
            Copy-Item -LiteralPath (Join-Path $Distribution 'SenorX99-CLI.exe') -Destination $CaseDir
        }
        $Output = Join-Path $Reports ('smoke-' + $CaseName + '.json')
        Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue
        $StartInfo = New-Object Diagnostics.ProcessStartInfo
        $StartInfo.FileName = $TargetExe
        $StartInfo.Arguments = '--runtime-self-test --runtime-report "{0}"' -f $Output
        if ($CaseName -eq 'helper-transport') {
            $StartInfo.Arguments = '--bios-helper-self-test --runtime-report "{0}"' -f $Output
        }
        $StartInfo.UseShellExecute = $false
        $StartInfo.WorkingDirectory = $CaseDir
        $StartInfo.EnvironmentVariables['X99_TOOLKIT_HOME'] = $CaseDir
        if ($CaseName -eq 'conflicting-dlls') {
            foreach ($Dll in @('Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll',
                    'shiboken6.abi3.dll','pyside6.abi3.dll','msvcp140.dll',
                    'msvcp140_1.dll','msvcp140_2.dll','vcruntime140.dll','vcruntime140_1.dll')) {
                [IO.File]::WriteAllText((Join-Path $CaseDir $Dll), 'Deliberately invalid DLL for search-path test')
            }
            $StartInfo.EnvironmentVariables['PATH'] = $CaseDir + ';' + $env:PATH
            $StartInfo.EnvironmentVariables['QT_PLUGIN_PATH'] = $CaseDir
            $StartInfo.EnvironmentVariables['QT_QPA_PLATFORM_PLUGIN_PATH'] = $CaseDir
            $StartInfo.EnvironmentVariables['QT_QPA_PLATFORM'] = 'offscreen'
        }
        Write-Host "Starting actual packaged test: $CaseName"
        $Process = [Diagnostics.Process]::Start($StartInfo)
        if (-not $Process.WaitForExit(120000)) {
            $Process.Kill(); $Process.WaitForExit()
            throw "$CaseName packaged self-test timed out."
        }
        if ($Process.ExitCode -ne 0) { throw "$CaseName packaged self-test failed with exit code $($Process.ExitCode). Logs: $CaseDir" }
        if (-not (Test-Path -LiteralPath $Output)) { throw "$CaseName produced no JSON result." }
        $Result = Get-Content -LiteralPath $Output -Raw | ConvertFrom-Json
        if ($Result.success -ne $true -or $Result.version -ne '0.86') { throw "$CaseName reported failure." }
        if ($Result.exe_sha256 -ne (Get-FileHash -LiteralPath $SourceExe -Algorithm SHA256).Hash.ToLowerInvariant()) { throw 'Self-test EXE hash mismatch.' }
        if ($CaseName -in @('gui','conflicting-dlls') -and $Result.platform -ne 'windows') { throw 'The actual Windows Qt platform was not tested.' }
        if ($CaseName -in @('gui','conflicting-dlls') -and $Result.drag_drop_verified -ne $true) { throw 'The packaged GUI file-drop test did not pass.' }
        if ($CaseName -eq 'helper-transport' -and ($Result.result.transport_verified -ne $true -or $Result.hardware_access -ne $false)) { throw 'The packaged helper exchange did not pass.' }
        $Results.Add([pscustomobject]@{case=$CaseName; success=$true; exe_sha256=$Result.exe_sha256})
        $Logs = Join-Path $CaseDir 'LOGS'
        if (Test-Path -LiteralPath $Logs) {
            Copy-Item -LiteralPath $Logs -Destination (Join-Path $Reports ('logs-' + $CaseName)) -Recurse -Force
        }
    }
    $Success = $true
} catch {
    Write-Host $_.Exception.ToString() -ForegroundColor Red
    Write-Host "Test files retained at: $Scratch"
} finally {
    [pscustomobject]@{success=$Success; cases=@($Results.ToArray()); scratch=$Scratch} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Reports 'windows-runtime-tests.json') -Encoding UTF8
    Stop-Transcript | Out-Null
}
if ($Success) {
    Remove-Item -LiteralPath $Scratch -Recurse -Force
    Write-Host 'All packaged runtime tests passed.' -ForegroundColor Green
    exit 0
}
exit 1
