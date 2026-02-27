param (
    [switch]$Create,
    [switch]$Delete,
    [switch]$RestartTask,
    [switch]$RestartDirect,

    [string]$PythonExe,      # Required for -Create and -RestartDirect

    [switch]$Detached        # Internal flag - do not use manually
)


# --- CONFIGURATION ---
$PythonScript = "main.py"
$ScheduledTaskArg = "--scheduled-task"
$CurrentExe = (Get-Process -Id $PID).Path

$SleepMilliseconds = 200
$MaxWaitSeconds = 10
$MaxCycles = ($MaxWaitSeconds * 1000) / $SleepMilliseconds

$DirectRestartDelayMs = 1500


# --- DERIVED PATHS ---
$ScriptDir = $PSScriptRoot
$TaskName = "growatt_monitor_webui"
$PythonScriptPath = Join-Path $ScriptDir $PythonScript
$LogFile = [System.IO.Path]::ChangeExtension($PSCommandPath, ".log")
$WriteInfoLog = $false


# --- LOGGING ---
function Write-ErrorLog {
    param ([string]$Message)
    try {
        $TimeStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $LogEntry = "$TimeStamp [ERROR] $Message"
        $LogEntry | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
    catch {
        # Logging failed, nothing to do
    }
}

function Write-InfoLog {
    param ([string]$Message)
    try {
        if (-not $WriteInfoLog) { return }
        $TimeStamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $LogEntry = "$TimeStamp [INFO] $Message"
        $LogEntry | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
    catch {}
}


# --- LAUNCHER (Auto-detach) ---
function Test-Admin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $Detached) {
    $CommandCount = @($Create, $Delete, $RestartTask, $RestartDirect).Where({ $_ }).Count
    if ($CommandCount -eq 0) {
        Write-Host "ERROR: No command specified. Use one of: -Create, -Delete, -RestartTask, -RestartDirect" -ForegroundColor Red
        exit 1
    }
    
    if ($CommandCount -gt 1) {
        Write-Host "ERROR: Only one command can be specified at a time." -ForegroundColor Red
        exit 1
    }

    if (($Create -or $RestartDirect) -and [string]::IsNullOrWhiteSpace($PythonExe)) {
        Write-Host "ERROR: -PythonExe parameter is required for -Create and -RestartDirect commands." -ForegroundColor Red
        exit 1
    }

    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $PythonExe = [System.IO.Path]::GetFullPath($PythonExe)
    }

    if (($Create -or $Delete) -and -not (Test-Admin)) {
        Write-Host "Administrator privileges required for managing scheduled tasks. Elevating..." -ForegroundColor Yellow
        $elevatedArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        $argList = @()
        if ($Create) { $argList += "-Create" }
        if ($Delete) { $argList += "-Delete" }
        if ($RestartTask) { $argList += "-RestartTask" }
        if ($RestartDirect) { $argList += "-RestartDirect" }
        if ($PythonExe) { $argList += "-PythonExe `"$PythonExe`"" }
        $elevatedArgs += " " + ($argList -join " ")
        Start-Process -FilePath $CurrentExe -ArgumentList $elevatedArgs -Verb RunAs -Wait
        exit
    }

    $CurrentScript = $PSCommandPath
    $CommandArgs = "-Detached"
    
    if ($Create)        { $CommandArgs += " -Create" }
    if ($Delete)        { $CommandArgs += " -Delete" }
    if ($RestartTask)   { $CommandArgs += " -RestartTask" }
    if ($RestartDirect) { $CommandArgs += " -RestartDirect" }
    
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $CommandArgs += " -PythonExe `"$PythonExe`""
    }

    $FullCommand = "`"$CurrentExe`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$CurrentScript`" $CommandArgs"

    try {
        $ProcessInfo = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $FullCommand
        }

        if ($ProcessInfo.ReturnValue -ne 0) {
            throw "WMI Win32_Process returned error code: $($ProcessInfo.ReturnValue)"
        }

        Write-Host "Command dispatched successfully (detached)." -ForegroundColor Green
    }
    catch {
        Write-Host "ERROR: Failed to launch detached process. $_" -ForegroundColor Red
        Write-ErrorLog "Failed to launch detached process. Command: $CommandArgs. Error: $_"
        exit 1
    }

    exit 0
}


# --- WORKER (Detached Process) ---

# Helper: wait for task to stop running
function Wait-TaskStopped {
    $CurrentCycle = 0
    
    do {
        try {
            $TaskObj = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            $State = $TaskObj.State
        }
        catch {
            # Task doesn't exist, nothing to wait for
            return $true
        }

        if ($State -eq 'Running') {
            if ($CurrentCycle -ge $MaxCycles) {
                Write-ErrorLog "Timeout reached ($MaxWaitSeconds seconds). Task '$TaskName' is still Running."
                return $false
            }
            Start-Sleep -Milliseconds $SleepMilliseconds
            $CurrentCycle++
        }
    } while ($State -eq 'Running')

    return $true
}

# COMMAND: -Create
if ($Create) {
    Write-InfoLog "Creating scheduled task '$TaskName' with Python: $PythonExe"

    try {
        $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($ExistingTask) {
            Write-InfoLog "Task '$TaskName' already exists. It will be overwritten."
        }

        # Principal: current user, S4U logon
        $Principal = New-ScheduledTaskPrincipal `
            -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
            -LogonType S4U `
            -RunLevel Limited

        $Trigger = New-ScheduledTaskTrigger -AtStartup

        $Action = New-ScheduledTaskAction `
            -Execute $PythonExe `
            -Argument "`"$PythonScriptPath`" $ScheduledTaskArg" `
            -WorkingDirectory $ScriptDir

        # Settings
        $Settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -DontStopOnIdleEnd `
            -ExecutionTimeLimit ([TimeSpan]::Zero)

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Principal $Principal `
            -Trigger $Trigger `
            -Action $Action `
            -Settings $Settings `
            -Force `
            -ErrorAction Stop | Out-Null

        Write-InfoLog "Task '$TaskName' created successfully."
    }
    catch {
        Write-ErrorLog "Failed to create task '$TaskName'. Error: $_"
    }

    exit
}

# COMMAND: -Delete
if ($Delete) {
    Write-InfoLog "Deleting scheduled task '$TaskName'"

    try {
        $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $ExistingTask) {
            Write-InfoLog "Task '$TaskName' does not exist. Nothing to delete."
            exit
        }

        if ($ExistingTask.State -eq 'Running') {
            Write-ErrorLog "Task '$TaskName' is running. Delete aborted - task must be stopped before deletion."
            Write-Host "ERROR: Task '$TaskName' is running. Cannot delete a running task." -ForegroundColor Red
            exit 1
        }

        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-InfoLog "Task '$TaskName' deleted successfully."
    }
    catch {
        Write-ErrorLog "Failed to delete task '$TaskName'. Error: $_"
    }

    exit
}

# COMMAND: -RestartTask
if ($RestartTask) {
    Write-InfoLog "Restarting scheduled task '$TaskName'"

    # Wait for the task to stop (Python should be shutting down)
    $Stopped = Wait-TaskStopped
    if (-not $Stopped) {
        Write-ErrorLog "Aborting restart - task did not stop in time."
        exit
    }

    try {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-InfoLog "Task '$TaskName' started successfully."
    }
    catch {
        Write-ErrorLog "Failed to start task '$TaskName'. Error: $_"
    }

    exit
}

# COMMAND: -RestartDirect
if ($RestartDirect) {
    Write-InfoLog "Restarting Python directly: $PythonExe"

    Start-Sleep -Milliseconds $DirectRestartDelayMs

    try {
        Start-Process `
            -FilePath $PythonExe `
            -ArgumentList "`"$PythonScriptPath`"" `
            -WorkingDirectory $ScriptDir `
            -ErrorAction Stop

        Write-InfoLog "Python process started successfully."
    }
    catch {
        Write-ErrorLog "Failed to start Python process. Error: $_"
    }

    exit
}
