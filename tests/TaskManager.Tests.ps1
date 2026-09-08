# Standalone regression checks; no scheduled tasks or service processes are changed.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$SourcePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'TaskManager.ps1'
$Tokens = $null
$Errors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw ($Errors | Out-String) }

function Assert-True($Condition, $Message) {
    if (-not $Condition) { throw $Message }
}

# Load only pure/helper functions, without running the script entry point.
$Functions = $Ast.FindAll({ param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($Function in $Functions) {
    . ([scriptblock]::Create($Function.Extent.Text))
}
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
Assert-True ((Get-AccountSid $Identity.Name) -eq $Identity.User.Value) 'Account name resolution failed'
Assert-True ((Get-AccountSid $Identity.User.Value) -eq $Identity.User.Value) 'SID resolution failed'

$Fixture = Join-Path ([IO.Path]::GetTempPath()) ('growatt-task-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Fixture | Out-Null
try {
    $ScriptDir = $Fixture
    $Leaf = Join-Path $Fixture "python's executable.exe"
    Set-Content -LiteralPath $Leaf -Value ''
    Assert-True ((Resolve-ProjectLeaf "python's executable.exe" 'test') -eq $Leaf) 'Relative path resolution failed'
    $MissingRejected = $false
    try { Resolve-ProjectLeaf 'missing.exe' 'test' | Out-Null } catch { $MissingRejected = $true }
    Assert-True $MissingRejected 'Missing executable was accepted'

    $Create = $true; $Delete = $false; $RestartTask = $false; $RestartDirect = $false
    $PythonExe = $Leaf; $TaskUserId = "test'user"; $InvocationId = 'test-invocation'
    $Command = New-SelfCommand
    $CommandAst = [System.Management.Automation.Language.Parser]::ParseInput($Command, [ref]$Tokens, [ref]$Errors)
    Assert-True ($Errors.Count -eq 0) 'Child command contains invalid quoting'
    $CommandNode = $CommandAst.Find({ param($Node) $Node -is [System.Management.Automation.Language.CommandAst] }, $true)
    $Values = @($CommandNode.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.StringConstantExpressionAst] } | ForEach-Object { $_.Value })
    Assert-True ($Values -contains $Leaf) 'Executable path was changed during quoting'
    Assert-True ($Values -contains $TaskUserId) 'Account was changed during quoting'

    $TaskUserId = $Identity.Name
    $TaskName = 'growatt_monitor_webui'; $TaskPath = '\'
    $ExpectedArguments = '"' + (Join-Path $Fixture 'main.py') + '" --scheduled-task'
    $RegisteredTask = [pscustomobject]@{
        TaskName = $TaskName
        Actions = @([pscustomobject]@{Execute=$Leaf; Arguments=$ExpectedArguments; WorkingDirectory=$Fixture})
        Principal = [pscustomobject]@{UserId=$Identity.User.Value; LogonType='S4U'; RunLevel='Limited'}
        Triggers = @([pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskBootTrigger'}})
    }
    function Get-ScheduledTask { return $RegisteredTask }
    Assert-RegisteredAction -ExpectedExecute $Leaf -ExpectedArguments $ExpectedArguments
    $RegisteredTask.Actions[0].WorkingDirectory = Join-Path $Fixture 'wrong-folder'
    $MismatchRejected = $false
    try { Assert-RegisteredAction -ExpectedExecute $Leaf -ExpectedArguments $ExpectedArguments }
    catch { $MismatchRejected = $true }
    Assert-True $MismatchRejected 'Registration verification accepted the wrong working directory'
    Write-Output 'PASS: registered action verification with SID and mismatched paths'

    # Exercise the real entry point in a child shell. Only privilege detection and
    # Windows side effects are stubbed; dispatch, validation and error handling run.
    $TestSource = Get-Content -LiteralPath $SourcePath -Raw
    $AdminFunction = $Functions | Where-Object Name -eq 'Test-Admin'
    $TestSource = $TestSource.Replace($AdminFunction.Extent.Text, 'function Test-Admin { return $true }')
    $FixtureScript = Join-Path $Fixture 'TaskManager.ps1'
    Set-Content -LiteralPath $FixtureScript -Value $TestSource
    $Runner = Join-Path $Fixture 'runner.ps1'
    $RunnerSource = @'
param([string]$Case)
$ErrorActionPreference = 'Stop'
$global:Events = [Collections.Generic.List[string]]::new()
$global:TaskState = if ($Case -eq 'delete') { 'Running' } else { 'Ready' }
function Get-ScheduledTask {
    if ($global:TaskState -ne 'Deleted') {
        [pscustomobject]@{TaskName='growatt_monitor_webui'; State=$global:TaskState}
    }
}
function Stop-ScheduledTask { $global:Events.Add('stop'); $global:TaskState = 'Ready' }
function Unregister-ScheduledTask { $global:Events.Add('delete'); $global:TaskState = 'Deleted' }
function Start-ScheduledTask { $global:Events.Add('start'); $global:TaskState = 'Running' }
function Start-Process { throw 'Unexpected process launch in test' }
function Invoke-CimMethod { throw 'Unexpected WMI launch in test' }
$Script = Join-Path $PSScriptRoot 'TaskManager.ps1'
switch ($Case) {
    'delete' { & $Script -Delete -Detached }
    'restart' { & $Script -RestartTask -Detached }
    'missing-python' { & $Script -Create -Detached }
    'invalid-flags' { & $Script -Create -Delete -Detached }
}
$Code = $LASTEXITCODE
'RESULT|' + (@{Code=$Code; Events=@($global:Events.ToArray())} | ConvertTo-Json -Compress)
'@
    Set-Content -LiteralPath $Runner -Value $RunnerSource
    $ShellExe = (Get-Process -Id $PID).Path
    foreach ($Case in @('delete', 'restart', 'missing-python', 'invalid-flags')) {
        # Delete/restart must work even if the project main.py has been removed.
        if ($Case -eq 'missing-python') { Set-Content -LiteralPath (Join-Path $Fixture 'main.py') -Value '' }
        $ErrorActionPreference = 'Continue' # Expected validation errors use stderr in Windows PowerShell.
        try { $Output = & $ShellExe -NoProfile -File $Runner $Case 2>&1 }
        finally { $ErrorActionPreference = 'Stop' }
        $ResultLine = @($Output | ForEach-Object { "$_" } | Where-Object { $_.StartsWith('RESULT|') })
        Assert-True ($ResultLine.Count -eq 1) "No test result for $Case"
        $Result = $ResultLine[0].Substring(7) | ConvertFrom-Json
        switch ($Case) {
            'delete' {
                Assert-True ($Result.Code -eq 0) 'Delete failed without main.py'
                Assert-True (($Result.Events -join ',') -eq 'stop,delete') 'Running task was not stopped before deletion'
            }
            'restart' {
                Assert-True ($Result.Code -eq 0) 'Restart failed without main.py'
                Assert-True (($Result.Events -join ',') -eq 'start') 'Restart did not start the task'
            }
            default {
                Assert-True ($Result.Code -eq 1) "Validation did not fail for $Case"
                Assert-True ($Result.Events.Count -eq 0) "Validation caused a task side effect for $Case"
            }
        }
        Write-Output "PASS: $Case"
    }
    Write-Output 'PASS: syntax, account/SID resolution, paths and command quoting'
}
finally {
    # Delete only this test's verified, uniquely named temporary directory.
    $ResolvedFixture = [IO.Path]::GetFullPath($Fixture)
    $TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($ResolvedFixture.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $ResolvedFixture -Leaf) -like 'growatt-task-tests-*') {
        Remove-Item -LiteralPath $ResolvedFixture -Recurse -Force
    }
}
