# Mirror of C:\Users\KOTI\Documents\homeass\Start-HomeAssistant.ps1
# Run from elevated PowerShell on Windows:
#   powershell -ExecutionPolicy Bypass -File C:\Users\KOTI\Documents\homeass\Start-HomeAssistant.ps1
$ErrorActionPreference = "Stop"
$vmName = "HomeAssistant"
$vm = Get-VM -Name $vmName
if ($vm.State -ne "Running") {
  Write-Host "Starting $vmName..."
  Start-VM -Name $vmName
  Start-Sleep -Seconds 8
}
$vm = Get-VM -Name $vmName
Write-Host ("State: " + $vm.State)
Write-Host ("Memory: " + [math]::Round($vm.MemoryAssigned/1GB,2) + " GB assigned / startup " + [math]::Round($vm.MemoryStartup/1GB,2) + " GB")
$ips = @()
for ($i = 0; $i -lt 30; $i++) {
  $ips = Get-VMNetworkAdapter -VMName $vmName | Select-Object -ExpandProperty IPAddresses | Where-Object { $_ -match "^\d+\.\d+\.\d+\.\d+$" }
  if ($ips) { break }
  Start-Sleep -Seconds 2
}
Write-Host ""
Write-Host "Open Home Assistant at:"
Write-Host "  http://homeassistant.local:8123"
if ($ips) {
  foreach ($ip in $ips) { Write-Host ("  http://" + $ip + ":8123") }
} else {
  Write-Host "  (IP not reported yet — wait 1-2 minutes for first boot, or check Hyper-V console)"
}
Write-Host ""
Write-Host "Note: Hyper-V has no USB passthrough for Zigbee/Z-Wave sticks."
