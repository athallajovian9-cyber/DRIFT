$ErrorActionPreference = 'SilentlyContinue'
$log = 'Microsoft-Windows-Diagnostics-Performance/Operational'

# ---- 1. did the slowness come from the kernel path or from the desktop phase?
$boots = Get-WinEvent -LogName $log -FilterXPath "*[System[(EventID=100)]]"
$rows = @()
foreach ($ev in $boots) {
    [xml]$x = $ev.ToXml()
    $d = @{}
    foreach ($n in $x.Event.EventData.Data) { $d[$n.Name] = $n.'#text' }
    if ($d.ContainsKey('BootTime') -and $d['BootTime']) {
        $rows += [pscustomobject]@{
            Date  = $ev.TimeCreated
            Main  = [int]$d['MainPathBootTime'] / 1000.0
            Post  = [int]$d['BootPostBootTime'] / 1000.0
        }
    }
}
$rows = $rows | Sort-Object Date
Write-Output ("boots measured: " + $rows.Count)

$old = @($rows | Select-Object -First 6)
$new = @($rows | Select-Object -Last 6)
$oM = [double]($old | Measure-Object -Property Main -Average).Average
$oP = [double]($old | Measure-Object -Property Post -Average).Average
$nM = [double]($new | Measure-Object -Property Main -Average).Average
$nP = [double]($new | Measure-Object -Property Post -Average).Average

Write-Output ""
Write-Output "                            total     kernel-path    desktop-phase"
Write-Output ("  OLDEST 6 boots   {0,10:N1} s {1,10:N1} s {2,10:N1} s" -f ($oM + $oP), $oM, $oP)
Write-Output ("  NEWEST 6 boots   {0,10:N1} s {1,10:N1} s {2,10:N1} s" -f ($nM + $nP), $nM, $nP)
Write-Output ("  change           {0,10:N1} s {1,10:N1} s {2,10:N1} s" -f (($nM + $nP) - ($oM + $oP)), ($nM - $oM), ($nP - $oP))

# ---- 2. what does Windows itself blame?
$causes = Get-WinEvent -LogName $log -MaxEvents 500 |
    Where-Object { $_.Id -in @(101, 102, 103, 106, 109, 110) }

$items = @()
foreach ($ev in $causes) {
    [xml]$x = $ev.ToXml()
    $d = @{}
    foreach ($n in $x.Event.EventData.Data) { $d[$n.Name] = $n.'#text' }
    $nm = $null
    foreach ($k in @('FileName', 'ServiceName', 'FriendlyName', 'DeviceName', 'Name')) {
        if ($d.ContainsKey($k) -and $d[$k]) { $nm = $d[$k]; break }
    }
    if (-not $nm) { continue }
    $ms = 0
    if ($d.ContainsKey('TimeTaken') -and $d['TimeTaken']) { $ms = [int]$d['TimeTaken'] }
    $items += [pscustomobject]@{ Id = $ev.Id; Name = $nm; Ms = $ms; Date = $ev.TimeCreated }
}

Write-Output ""
Write-Output ("=== what Windows blames, all " + $items.Count + " cause events on record ===")
$latest = ($items | Sort-Object Date | Select-Object -Last 1).Date
if ($latest) { Write-Output ("  (most recent cause event: " + $latest.ToString('yyyy-MM-dd') + ")") }
Write-Output ""
$items |
    Group-Object Id, Name |
    Sort-Object { [double](($_.Group | Measure-Object Ms -Average).Average) } -Descending |
    Select-Object -First 14 |
    ForEach-Object {
        $a = [double]($_.Group | Measure-Object Ms -Average).Average
        Write-Output ("  id {0}  {1,-44} avg {2,7:N0} ms  seen {3}x" -f $_.Group[0].Id, $_.Name, $a, $_.Count)
    }

# ---- 3. what is set to start with Windows right now?
Write-Output ""
Write-Output "=== things registered to start with Windows ==="
$run = @()
foreach ($hive in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run')) {
    $k = Get-Item $hive
    if ($k) {
        foreach ($p in $k.Property) {
            $run += [pscustomobject]@{ Hive = $hive.Split('\')[0]; Name = $p; Cmd = $k.GetValue($p) }
        }
    }
}
Write-Output ("  Run keys: " + $run.Count + " entries")
foreach ($r in $run) {
    $short = $r.Cmd
    if ($short.Length -gt 76) { $short = $short.Substring(0, 73) + '...' }
    Write-Output ("    " + $r.Name.PadRight(28) + " " + $short)
}

$svc = Get-Service | Where-Object { $_.StartType -eq 'Automatic' }
Write-Output ("  services set to Automatic: " + $svc.Count)
