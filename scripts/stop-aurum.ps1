<#
.SYNOPSIS
    Stop the Aurum cage broker and Hermes gateway.
#>
param()

function Stop-ProcessOnPort([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
            Write-Host "[aurum] Stopped PID $($c.OwningProcess) (port $port)"
        } catch {
            Write-Warning "[aurum] Could not stop PID $($c.OwningProcess): $_"
        }
    }
}

# Persistent agent container (stopped before the broker so in-flight turns drain first)
$null = docker rm -f aurum-agent-persistent 2>$null
Write-Host '[aurum] Stopped persistent container (if running)'

# Cage broker (port 8900)
Stop-ProcessOnPort 8900

# Gateway - find pythonw processes running hermes_cli.main gateway run
Get-WmiObject Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*hermes_cli.main*gateway*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "[aurum] Stopped gateway PID $($_.ProcessId)"
    }

Write-Host '[aurum] Done.'
