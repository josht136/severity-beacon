$ErrorActionPreference = 'Stop'
Stop-VM -Name HomeAssistant -Force
$deadline = (Get-Date).AddMinutes(3)
do {
  Start-Sleep -Seconds 2
  $vm = Get-VM -Name HomeAssistant
  Write-Host ("State=" + $vm.State)
} while ($vm.State -ne 'Off' -and (Get-Date) -lt $deadline)
if ((Get-VM -Name HomeAssistant).State -ne 'Off') {
  throw 'VM did not reach Off state'
}
Write-Host 'STOPPED_OK'
