[CmdletBinding()]
param (
    [switch]$Create,
    [switch]$Delete,
    [switch]$RestartTask,
    [switch]$RestartDirect,
    [string]$PythonExe,
    [string]$TaskUserId,
    [string]$InvocationId,
    [switch]$Detached,
    [switch]$Elevated
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "growatt_monitor_webui"
$TaskPath = "\"
$PythonScript = "main.py"
$ScheduledTaskArg = "--scheduled-task"
$SleepMilliseconds = 200
$MaxWaitSeconds = 30
$DirectRestartDelayMs = 3000
try {
    $ScriptDir = [System.IO.Path]::GetFullPath($PSScriptRoot)
    $PythonScriptPath = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir $PythonScript))
    $CurrentExe = (Get-Process -Id $PID -ErrorAction Stop).Path
    $LogFile = [System.IO.Path]::ChangeExtension($PSCommandPath, ".log")
}
catch {
    [Console]::Error.WriteLine("ERROR: Failed to initialize task manager paths: $($_.Exception.Message)")
    exit 1
}

function Write-TaskError {
    param ([string]$Message)
    try {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [ERROR] $Message" |
            Out-File -LiteralPath $LogFile -Append -Encoding utf8
    }
    catch {}
    [Console]::Error.WriteLine("ERROR: $Message")
}

function Test-Admin {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-ProjectLeaf {
    param (
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Description
    )

    $Candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        $Path
    }
    else {
        Join-Path $ScriptDir $Path
    }
    $FullPath = [System.IO.Path]::GetFullPath($Candidate)
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        throw "$Description not found: $FullPath"
    }
    return (Resolve-Path -LiteralPath $FullPath -ErrorAction Stop).ProviderPath
}

function Get-RootTask {
    return @(
        Get-ScheduledTask -TaskPath $TaskPath -ErrorAction Stop |
            Where-Object { $_.TaskName -eq $TaskName }
    )
}

function Get-AccountSid {
    param ([Parameter(Mandatory)] [string]$Account)
    if ($Account -match '^S-\d-') {
        return ([Security.Principal.SecurityIdentifier]::new($Account)).Value
    }
    return ([Security.Principal.NTAccount]::new($Account)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
}

function New-SelfCommand {
    param ([switch]$ForElevation)

    $EscapedScript = $PSCommandPath.Replace("'", "''")
    $ChildSwitch = if ($ForElevation) { "-Elevated" } else { "-Detached" }
    $Command = "& '$EscapedScript' $ChildSwitch"
    if ($Create) { $Command += " -Create" }
    if ($Delete) { $Command += " -Delete" }
    if ($RestartTask) { $Command += " -RestartTask" }
    if ($RestartDirect) { $Command += " -RestartDirect" }
    if ($PythonExe) {
        $EscapedPython = $PythonExe.Replace("'", "''")
        $Command += " -PythonExe '$EscapedPython'"
    }
    $EscapedTaskUser = $TaskUserId.Replace("'", "''")
    $Command += " -TaskUserId '$EscapedTaskUser'"
    $EscapedInvocation = $InvocationId.Replace("'", "''")
    $Command += " -InvocationId '$EscapedInvocation'"
    return $Command
}

function Invoke-ElevatedSelf {
    $Command = New-SelfCommand -ForElevation
    $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    $Process = Start-Process -FilePath $CurrentExe `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedCommand) `
        -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
    return $Process.ExitCode
}

function Invoke-DetachedSelf {
    param ([switch]$WaitForResult)

    $WorkerCommand = New-SelfCommand
    $WorkerEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($WorkerCommand))
    $ResultFile = $null

    if ($WaitForResult) {
        $ResultFile = Join-Path ([System.IO.Path]::GetTempPath()) `
            ("TaskManager_{0}_{1}.result" -f $TaskName, [Guid]::NewGuid().ToString("N"))
        $ResultTempFile = "$ResultFile.tmp"
        $WorkerPidFile = "$ResultFile.pid"
        $WorkerPidTempFile = "$WorkerPidFile.tmp"
        $EscapedExe = $CurrentExe.Replace("'", "''")
        $EscapedResult = $ResultFile.Replace("'", "''")
        $EscapedTempResult = $ResultTempFile.Replace("'", "''")
        $EscapedWorkerPid = $WorkerPidFile.Replace("'", "''")
        $EscapedWorkerPidTemp = $WorkerPidTempFile.Replace("'", "''")
        $WrapperCommand = "`$ExitCode = 1; `$Process = `$null; try { `$Process = Start-Process -FilePath '$EscapedExe' " +
            "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','$WorkerEncoded') " +
            "-WindowStyle Hidden -PassThru -ErrorAction Stop; " +
            "[System.IO.File]::WriteAllText('$EscapedWorkerPidTemp', [string]`$Process.Id); " +
            "Move-Item -LiteralPath '$EscapedWorkerPidTemp' -Destination '$EscapedWorkerPid' -Force -ErrorAction Stop; " +
            "`$Process.WaitForExit(); `$ExitCode = [int]`$Process.ExitCode } catch { " +
            "if (`$null -ne `$Process -and -not `$Process.HasExited) { " +
            "& taskkill.exe /PID `$Process.Id /T /F 2>`$null | Out-Null; `$Process.WaitForExit() }; `$ExitCode = 1 }; " +
            "[System.IO.File]::WriteAllText('$EscapedTempResult', [string]`$ExitCode); " +
            "Move-Item -LiteralPath '$EscapedTempResult' -Destination '$EscapedResult' -Force -ErrorAction Stop; " +
            "exit `$ExitCode"
        $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($WrapperCommand))
    }
    else {
        $EncodedCommand = $WorkerEncoded
    }

    $FullCommand = "`"$CurrentExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand $EncodedCommand"
    $Result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = $FullCommand } -ErrorAction Stop
    if ($Result.ReturnValue -ne 0) {
        throw "WMI Win32_Process returned error code $($Result.ReturnValue)."
    }

    if (-not $WaitForResult) {
        return
    }

    try {
        $Deadline = (Get-Date).AddSeconds(60)
        while (-not (Test-Path -LiteralPath $ResultFile -PathType Leaf)) {
            $Wrapper = Get-CimInstance Win32_Process -Filter "ProcessId = $($Result.ProcessId)" -ErrorAction Stop
            if ($null -eq $Wrapper) {
                if (Test-Path -LiteralPath $ResultFile -PathType Leaf) {
                    break
                }
                $OrphanedWorkers = @(
                    Get-CimInstance Win32_Process -ErrorAction Stop |
                        Where-Object { $_.CommandLine -like "*$WorkerEncoded*" }
                )
                foreach ($Orphan in $OrphanedWorkers) {
                    if (Test-Path -LiteralPath $ResultFile -PathType Leaf) { break }
                    & taskkill.exe /PID $Orphan.ProcessId /T /F 2>$null | Out-Null
                    Start-Sleep -Milliseconds 300
                    $StillAlive = Get-CimInstance Win32_Process -Filter "ProcessId = $($Orphan.ProcessId)" -ErrorAction Stop |
                        Where-Object { $_.CommandLine -like "*$WorkerEncoded*" }
                    if ($null -ne $StillAlive) {
                        throw "Detached WMI wrapper exited without a result, and orphaned worker termination could not be verified."
                    }
                }
                if (Test-Path -LiteralPath $ResultFile -PathType Leaf) { break }
                throw "Detached WMI wrapper exited without publishing a result."
            }
            if ((Get-Date) -ge $Deadline) {
                if (Test-Path -LiteralPath $ResultFile -PathType Leaf) {
                    break
                }
                $Wrapper = Get-CimInstance Win32_Process -Filter "ProcessId = $($Result.ProcessId)" -ErrorAction Stop
                if ($null -eq $Wrapper) {
                    if (Test-Path -LiteralPath $ResultFile -PathType Leaf) { break }
                    throw "Detached WMI wrapper exited without publishing a result."
                }
                if ($Wrapper.CommandLine -notlike "*$EncodedCommand*") {
                    throw "Timed out, but the WMI wrapper PID no longer identifies the expected command; termination was not attempted."
                }
                $WorkerPid = if (Test-Path -LiteralPath $WorkerPidFile -PathType Leaf) {
                    [int]((Get-Content -LiteralPath $WorkerPidFile -Raw -ErrorAction Stop).Trim())
                } else { 0 }
                & taskkill.exe /PID $Result.ProcessId /T /F 2>$null | Out-Null
                $KillExitCode = $LASTEXITCODE
                Start-Sleep -Milliseconds 300
                if (Test-Path -LiteralPath $ResultFile -PathType Leaf) { break }
                $WrapperStillAlive = Get-CimInstance Win32_Process -Filter "ProcessId = $($Result.ProcessId)" -ErrorAction Stop
                $WorkerStillAlive = Get-CimInstance Win32_Process -ErrorAction Stop |
                    Where-Object { $_.CommandLine -like "*$WorkerEncoded*" }
                if ($KillExitCode -ne 0 -or $null -ne $WrapperStillAlive -or $null -ne $WorkerStillAlive) {
                    throw "Timed out waiting for the detached WMI worker; process-tree termination could not be verified."
                }
                throw "Timed out waiting for the detached WMI worker; its verified process tree was terminated."
            }
            Start-Sleep -Milliseconds 200
        }
        return [int]((Get-Content -LiteralPath $ResultFile -Raw -ErrorAction Stop).Trim())
    }
    finally {
        Remove-Item -LiteralPath $ResultFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $ResultTempFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $WorkerPidFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $WorkerPidTempFile -Force -ErrorAction SilentlyContinue
    }
}

function Assert-RegisteredAction {
    param (
        [Parameter(Mandatory)] [string]$ExpectedExecute,
        [Parameter(Mandatory)] [string]$ExpectedArguments
    )

    $Registered = @(Get-RootTask)
    if ($Registered.Count -ne 1) {
        throw "Registration verification failed: expected one root task, found $($Registered.Count)."
    }
    $Actions = @($Registered[0].Actions)
    if ($Actions.Count -ne 1) {
        throw "Registration verification failed: expected one action, found $($Actions.Count)."
    }
    $Actual = $Actions[0]
    if (-not [System.IO.Path]::IsPathRooted($Actual.Execute) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath($Actual.Execute), $ExpectedExecute, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals($Actual.Arguments, $ExpectedArguments, [StringComparison]::Ordinal) -or
        -not [System.IO.Path]::IsPathRooted($Actual.WorkingDirectory) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath($Actual.WorkingDirectory), $ScriptDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Registration verification failed: the stored action does not match the requested absolute paths."
    }
    $ActualPrincipal = $Registered[0].Principal
    if ((Get-AccountSid -Account $ActualPrincipal.UserId) -ne (Get-AccountSid -Account $TaskUserId) -or
        $ActualPrincipal.LogonType -ne "S4U" -or $ActualPrincipal.RunLevel -ne "Limited") {
        throw "Registration verification failed: the stored principal does not match the requested user/S4U/Limited settings."
    }
    $Triggers = @($Registered[0].Triggers)
    if ($Triggers.Count -ne 1 -or $Triggers[0].CimClass.CimClassName -ne "MSFT_TaskBootTrigger") {
        throw "Registration verification failed: expected exactly one AtStartup trigger."
    }
}

function Wait-TaskStopped {
    $Deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    while ((Get-Date) -lt $Deadline) {
        $Tasks = @(Get-RootTask)
        if ($Tasks.Count -eq 0 -or $Tasks[0].State -ne "Running") {
            return
        }
        Start-Sleep -Milliseconds $SleepMilliseconds
    }
    throw "Timeout: task '$TaskName' is still running after $MaxWaitSeconds seconds."
}

try {
    $CommandCount = @($Create, $Delete, $RestartTask, $RestartDirect).Where({ $_ }).Count
    if ($CommandCount -ne 1) {
        throw "Specify exactly one command: -Create, -Delete, -RestartTask or -RestartDirect."
    }
    if ([string]::IsNullOrWhiteSpace($TaskUserId)) {
        $TaskUserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    }
    if ([string]::IsNullOrWhiteSpace($InvocationId)) {
        $InvocationId = [Guid]::NewGuid().ToString("N")
    }

    if ($Create -or $RestartDirect) {
        $PythonScriptPath = Resolve-ProjectLeaf -Path $PythonScriptPath -Description "Python script"
        if ([string]::IsNullOrWhiteSpace($PythonExe)) {
            throw "-PythonExe is required for -Create and -RestartDirect."
        }
        $PythonExe = Resolve-ProjectLeaf -Path $PythonExe -Description "Python executable"
    }

    if (($Create -or $Delete) -and -not (Test-Admin)) {
        if ($Elevated) {
            throw "Elevation completed without administrator privileges."
        }
        exit (Invoke-ElevatedSelf)
    }

    if (-not $Detached) {
        if ($Create -or $Delete) {
            $WorkerExitCode = Invoke-DetachedSelf -WaitForResult
            if ($WorkerExitCode -ne 0) {
                throw "Detached WMI worker failed with exit code $WorkerExitCode."
            }
            Write-Host "Command completed successfully in detached WMI worker." -ForegroundColor Green
            exit 0
        }

        Invoke-DetachedSelf
        Write-Host "Command dispatched successfully (detached WMI worker)." -ForegroundColor Green
        exit 0
    }

    if ($Create) {
        $ExpectedArguments = "`"$PythonScriptPath`" $ScheduledTaskArg"
        $Principal = New-ScheduledTaskPrincipal `
            -UserId $TaskUserId `
            -LogonType S4U -RunLevel Limited
        $Trigger = New-ScheduledTaskTrigger -AtStartup
        $Action = New-ScheduledTaskAction `
            -Execute $PythonExe -Argument $ExpectedArguments -WorkingDirectory $ScriptDir
        $Settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -DontStopOnIdleEnd `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) `
            -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath `
            -Principal $Principal -Trigger $Trigger -Action $Action -Settings $Settings `
            -Force -ErrorAction Stop | Out-Null
        Assert-RegisteredAction -ExpectedExecute $PythonExe -ExpectedArguments $ExpectedArguments
        Write-Host "SUCCESS: Task '$TaskName' registered with absolute paths." -ForegroundColor Green
        exit 0
    }

    if ($Delete) {
        $Existing = @(Get-RootTask)
        if ($Existing.Count -eq 0) {
            Write-Host "Task '$TaskName' does not exist. Nothing to delete." -ForegroundColor Yellow
            exit 0
        }
        if ($Existing.Count -ne 1) {
            throw "Expected one root task named '$TaskName', found $($Existing.Count)."
        }
        if ($Existing[0].State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop
            Wait-TaskStopped
        }
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false -ErrorAction Stop
        if (@(Get-RootTask).Count -ne 0) {
            throw "Deletion verification failed: task '$TaskName' is still registered."
        }
        Write-Host "SUCCESS: Task '$TaskName' deleted." -ForegroundColor Green
        exit 0
    }

    if ($RestartTask) {
        Wait-TaskStopped
        if (@(Get-RootTask).Count -ne 1) {
            throw "Task '$TaskName' is not registered."
        }
        Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop
        $Deadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds $SleepMilliseconds
            $StartedTask = @(Get-RootTask)
        } while ($StartedTask.Count -eq 1 -and $StartedTask[0].State -ne "Running" -and (Get-Date) -lt $Deadline)
        if ($StartedTask.Count -ne 1 -or $StartedTask[0].State -ne "Running") {
            $LastResult = (Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop).LastTaskResult
            throw "Task '$TaskName' did not reach Running state after start (LastTaskResult=$LastResult)."
        }
        Write-Host "SUCCESS: Task '$TaskName' started." -ForegroundColor Green
        exit 0
    }

    Start-Sleep -Milliseconds $DirectRestartDelayMs
    $Process = Start-Process -FilePath $PythonExe `
        -ArgumentList "`"$PythonScriptPath`"" -WorkingDirectory $ScriptDir `
        -WindowStyle Hidden -PassThru -ErrorAction Stop
    Write-Host "SUCCESS: Python started with PID $($Process.Id)." -ForegroundColor Green
    exit 0
}
catch {
    Write-TaskError -Message $_.Exception.Message
    exit 1
}
