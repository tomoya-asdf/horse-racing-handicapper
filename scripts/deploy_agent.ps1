<#
.SYNOPSIS
  ホスト側デプロイエージェント。WebUI からのアップデート依頼を受けて
  git pull + docker compose build + up -d を実行する。

.DESCRIPTION
  webui コンテナに docker.sock を渡さないため、デプロイはこのスクリプトが
  ホスト上で担当する。WebUI とは共有ボリューム ./data 上の JSON でやりとりする。

    - data/deploy_request.json : WebUI が書く依頼。本スクリプトが処理後に削除する。
    - data/deploy_status.json  : 本スクリプトが書く状態(現在バージョン/更新有無/進捗)。

  コンテナを丸ごと作り直しても、このスクリプトはホスト上で動き続けるため安全に
  全スタックを更新できる。git と docker(Docker Desktop)がホストで使えることが前提。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\deploy_agent.ps1

  ログオン時に自動起動したい場合は Windows タスクスケジューラに登録する(末尾の注記参照)。
#>
[CmdletBinding()]
param(
    # 更新確認の間隔(秒)。デプロイ依頼の検出もこの間隔でポーリングする。
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = "Stop"

# リポジトリのルート(このスクリプトの1つ上の階層)
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $RepoRoot "data"
$StatusFile = Join-Path $DataDir "deploy_status.json"
$RequestFile = Join-Path $DataDir "deploy_request.json"

if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }

function Now-Iso { (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz") }

function Invoke-Git {
    param([string[]]$GitArgs)
    # stderr も拾いつつ終了コードを見る
    $out = & git -C $RepoRoot @GitArgs 2>&1
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
}

# 直近のデプロイ結果(状態ファイルに引き継ぐ)
$lastDeployAt = $null
$lastDeployResult = $null
$lastMessage = $null

function Write-Status {
    param([string]$State, [bool]$UpdateAvailable, [string]$CurrentSha, [string]$CurrentRef, [string]$RemoteSha)
    $status = [ordered]@{
        current_sha        = $CurrentSha
        current_ref        = $CurrentRef
        remote_sha         = $RemoteSha
        update_available   = $UpdateAvailable
        last_checked_at    = Now-Iso
        state              = $State
        last_deploy_at     = $script:lastDeployAt
        last_deploy_result = $script:lastDeployResult
        message            = $script:lastMessage
        agent_seen_at      = Now-Iso
    }
    # 一時ファイル経由で原子的に置き換える(WebUI が読み取り中の破損を避ける)。
    # Python 側が json.loads できるよう、BOM なし UTF-8 で書く
    # (Windows PowerShell の Out-File -Encoding utf8 は BOM 付きになるため使わない)。
    $tmp = "$StatusFile.tmp"
    $json = $status | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
    Move-Item -Path $tmp -Destination $StatusFile -Force
}

function Run-Deploy {
    param([string]$Ref)
    $script:lastDeployAt = Now-Iso
    Write-Status -State "running" -UpdateAvailable $false -CurrentRef $Ref -CurrentSha (Get-LocalSha) -RemoteSha (Get-RemoteSha $Ref)

    $log = New-Object System.Collections.Generic.List[string]
    $steps = @(
        @{ Name = "git pull"; Action = { Invoke-Git @("pull", "--ff-only") } },
        @{ Name = "docker compose build"; Action = { $o = & docker compose -f (Join-Path $RepoRoot "docker-compose.yml") build 2>&1; [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($o -join "`n") } } },
        @{ Name = "docker compose up -d"; Action = { $o = & docker compose -f (Join-Path $RepoRoot "docker-compose.yml") up -d 2>&1; [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($o -join "`n") } } }
    )
    foreach ($step in $steps) {
        $log.Add("### $($step.Name)")
        $r = & $step.Action
        $log.Add($r.Out)
        if ($r.Code -ne 0) {
            $script:lastDeployResult = "failed"
            $script:lastMessage = "[$($step.Name)] 失敗:`n" + (($log | Select-Object -Last 40) -join "`n")
            Write-Status -State "failed" -UpdateAvailable $false -CurrentRef $Ref -CurrentSha (Get-LocalSha) -RemoteSha (Get-RemoteSha $Ref)
            return
        }
    }
    $script:lastDeployResult = "success"
    $script:lastMessage = "デプロイ成功:`n" + (($log | Select-Object -Last 20) -join "`n")
    Write-Status -State "success" -UpdateAvailable $false -CurrentRef $Ref -CurrentSha (Get-LocalSha) -RemoteSha (Get-RemoteSha $Ref)
}

function Get-LocalSha { (Invoke-Git @("rev-parse", "--short", "HEAD")).Out.Trim() }
function Get-Ref { (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")).Out.Trim() }
function Get-RemoteSha {
    param([string]$Ref)
    $r = Invoke-Git @("rev-parse", "--short", "@{u}")
    if ($r.Code -ne 0) { return $null }
    return $r.Out.Trim()
}

Write-Host "deploy agent started. repo=$RepoRoot interval=${IntervalSeconds}s"

while ($true) {
    try {
        $ref = Get-Ref
        # リモートの最新を取得して更新有無を判定(失敗しても継続)
        Invoke-Git @("fetch", "--quiet") | Out-Null
        $local = Get-LocalSha
        $remote = Get-RemoteSha $ref
        $updateAvailable = ($remote -and $local -and $remote -ne $local)

        if (Test-Path $RequestFile) {
            Remove-Item $RequestFile -Force
            Write-Host "$(Now-Iso) deploy requested -> running"
            Run-Deploy -Ref $ref
        }
        else {
            $state = if ($script:lastDeployResult) { $script:lastDeployResult } else { "idle" }
            Write-Status -State $state -UpdateAvailable $updateAvailable -CurrentRef $ref -CurrentSha $local -RemoteSha $remote
        }
    }
    catch {
        Write-Warning "agent loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}

# --- 自動起動(任意) ---------------------------------------------------------
# ログオン時に常駐させる例(管理者 PowerShell で1度だけ実行):
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#       -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PWD\scripts\deploy_agent.ps1`""
#   $trigger = New-ScheduledTaskTrigger -AtLogOn
#   Register-ScheduledTask -TaskName "horse-deploy-agent" -Action $action -Trigger $trigger
