$ErrorActionPreference = 'Stop'
try {
  Checkpoint-VM -Name HomeAssistant -SnapshotName 'pre-severity-beacon-addon' -Passthru |
    Select-Object Name, SnapshotType, CreationTime |
    Format-List
  Write-Host 'CHECKPOINT_OK'
} catch {
  Write-Host ('CHECKPOINT_FAIL: ' + $_.Exception.Message)
}
Get-VMSnapshot -VMName HomeAssistant |
  Select-Object Name, CreationTime |
  Format-Table -AutoSize
