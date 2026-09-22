# Health Mirror, PC half.
#
# One-way copy of the owner's Personal Health Record folder into the same
# private Drive folder the server writes its snapshot to, so the Claude app
# on his phone can read the reports and past plans as well as the summary.
#
# ONE-WAY, and it never deletes: `rclone copy` only ever adds and updates at
# the destination and does not touch the source at all. It is deliberately
# NOT `rclone sync`, which would delete on Drive whatever had gone from the
# PC -- including anything he had tidied away locally but still wanted to be
# able to ask about.
#
# Personal record only. Nothing from the clinic.
#
#   powershell -ExecutionPolicy Bypass -File mirror_records.ps1
#       -Source  the folder to mirror       (default: his records folder)
#       -WhatIf  list what would be copied and copy nothing

[CmdletBinding()]
param(
  [string]$Source = 'D:\Downloads\dr manoj medical records',
  [string]$Remote = 'healthmirror',
  [string]$RemotePath = 'Health Mirror (for Claude)/records',
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$rclone = Join-Path $env:LOCALAPPDATA 'rclone\rclone.exe'
$logDir = Join-Path $env:LOCALAPPDATA 'HealthMirror'
$log = Join-Path $logDir 'mirror_records.log'
New-Item -ItemType Directory -Force $logDir | Out-Null

function Say($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Write-Output $line
  Add-Content -Path $log -Value $line -Encoding utf8
}

Say "---- records mirror starting ----"

if (-not (Test-Path $rclone)) {
  Say "FAILED: rclone is not installed at $rclone"
  exit 1
}
if (-not (Test-Path $Source)) {
  Say "FAILED: the records folder is not there: $Source"
  exit 1
}

$files = @(Get-ChildItem -Path $Source -Recurse -File -ErrorAction SilentlyContinue)
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
Say ("source: {0} file(s), {1:N0} bytes" -f $files.Count, $bytes)

# No configuration means his one Google sign-in has not happened yet. Say so
# plainly and stop -- do NOT report success, because a mirror that looks
# current and is not is the whole thing this design is trying to avoid.
$conf = Join-Path $env:APPDATA 'rclone\rclone.conf'
if (-not (Test-Path $conf)) {
  Say "STOPPING: rclone has no configuration yet, so nothing was uploaded."
  Say "  One sign-in is needed, once:"
  Say "    `"$rclone`" config create $Remote drive scope drive.file"
  exit 2
}

$args = @('copy', $Source, ("{0}:{1}" -f $Remote, $RemotePath),
          '--drive-use-trash=false', '--transfers', '2', '--checkers', '4',
          '--stats', '0', '--log-level', 'NOTICE')
if ($WhatIf) { $args += '--dry-run' }

Say ("rclone copy -> {0}:{1}{2}" -f $Remote, $RemotePath, $(if ($WhatIf) { '  (dry run)' } else { '' }))
& $rclone @args 2>&1 | ForEach-Object { Say "  $_" }
$code = $LASTEXITCODE

if ($code -ne 0) {
  Say "FAILED: rclone exited $code. The mirror is NOT current."
  exit $code
}

if (-not $WhatIf) {
  $stampPath = Join-Path $logDir 'records_last_success.json'
  $now = Get-Date
  $stamp = [ordered]@{
    at    = $now.ToString('s')
    epoch = [int][double]::Parse((Get-Date $now -UFormat %s))
    files = $files.Count
    bytes = $bytes
  }
  ($stamp | ConvertTo-Json -Compress) | Set-Content -Path $stampPath -Encoding utf8
  Say "records mirror done; marker written"
} else {
  Say "dry run finished; no marker written"
}
exit 0
