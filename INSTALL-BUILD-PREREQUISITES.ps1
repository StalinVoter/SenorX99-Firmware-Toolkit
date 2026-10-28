[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = $PSScriptRoot
$LogPath = Join-Path $Root 'setup-build-SenorX99.log'
Start-Transcript -LiteralPath $LogPath -Force | Out-Null
$ExitCode = 1
try {
    $Version = '3.13.14'
    $ExpectedHash = 'c54d9b9bbb8a36e6489363ddd01139707fd781d72f1f9e90c7ec65d0061368e0'
    $InstallDir = Join-Path $env:LOCALAPPDATA 'X99-SecureBoot-Build\Python-3.13.14'
    $PythonExe = Join-Path $InstallDir 'python.exe'
    # Reuse an existing exact interpreter, but never its site-packages.
    foreach ($Candidate in @($PythonExe,
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
            (Join-Path $env:ProgramFiles 'Python313\python.exe'))) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            & $Candidate -c "import sys,struct; sys.exit(0 if sys.version_info[:3] == (3,13,14) and struct.calcsize('P') == 8 else 1)"
            if ($LASTEXITCODE -eq 0) { $PythonExe = $Candidate; break }
        }
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        $Temporary = Join-Path $env:TEMP ('X99-Build-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $Temporary | Out-Null
        try {
            $Installer = Join-Path $Temporary 'python-amd64.exe'
            $Url = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
            Write-Host "Downloading pinned Python $Version..."
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Installer
            if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
                throw 'Python installer SHA-256 verification failed.'
            }
            $Signature = Get-AuthenticodeSignature -LiteralPath $Installer
            if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw 'Python installer signature verification failed.'
            }
            $InstallArguments = @('/quiet','InstallAllUsers=0',
                ('TargetDir="{0}"' -f $InstallDir), 'Include_pip=1',
                'Include_launcher=0','Include_test=0','PrependPath=0',
                'AssociateFiles=0','Shortcuts=0')
            $InstallerProcess = Start-Process -FilePath $Installer -ArgumentList $InstallArguments -Wait -PassThru
            if ($InstallerProcess.ExitCode -notin @(0,3010)) { throw "Python installer exit code $($InstallerProcess.ExitCode)" }
        } finally { Remove-Item -LiteralPath $Temporary -Recurse -Force }
    }
    & $PythonExe -c "import sys,struct; assert sys.version_info[:3] == (3,13,14); assert struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Python 3.13.14 x64 is not available.' }
    $Venv = Join-Path $Root '.build-venv'
    Write-Host 'Creating a clean, isolated build environment...'
    & $PythonExe -m venv --clear $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    $BuildPython = Join-Path $Venv 'Scripts\python.exe'
    & $BuildPython -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r (Join-Path $Root 'requirements-build.lock')
    if ($LASTEXITCODE -ne 0) { throw 'Installing hash-locked build dependencies failed.' }
    & $BuildPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Build dependency compatibility check failed.' }
    Push-Location $Root
    try { & cmd.exe /d /c 'call build_exe.bat'; $ExitCode = $LASTEXITCODE }
    finally { Pop-Location }
    if ($ExitCode -ne 0) { throw "Build or packaged application validation failed: $ExitCode" }
    Write-Host 'Build, EXE validation and release packaging completed.' -ForegroundColor Green
} catch {
    $ExitCode = 1
    Write-Host $_.Exception.ToString() -ForegroundColor Red
    Write-Host "Build log: $LogPath"
} finally { Stop-Transcript | Out-Null }
exit $ExitCode
