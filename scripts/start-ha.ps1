$ErrorActionPreference = 'Stop'
Start-VM -Name HomeAssistant
$deadline = (Get-Date).AddMinutes(5)
do {
  Start-Sleep -Seconds 3
  $vm = Get-VM -Name HomeAssistant
  Write-Host ("State=" + $vm.State)
} while ($vm.State -ne 'Running' -and (Get-Date) -lt $deadline)
if ((Get-VM -Name HomeAssistant).State -ne 'Running') {
  throw 'VM did not reach Running state'
}
Write-Host 'STARTED_OK'
