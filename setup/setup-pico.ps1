#Requires -Version 7
<#
setup-pico.ps1 - put CircuitPython and the pico-usbkeyboard firmware onto a Raspberry Pi Pico W (Windows; run it on
the PC the Pico is plugged into by USB). Version 0.2.0. Later firmware updates go over the network with
client\kvm.py push.

Dry run by default (shows what it would download and copy); -Apply does it. Run it again after each stage: it finds
the Pico by its volume name, not its drive letter, and carries on from where the Pico is:
  RPI-RP2   (BOOTSEL)         -> copies the CircuitPython UF2; the Pico restarts as CIRCUITPY
  CIRCUITPY, no settings.toml -> copies lib\adafruit_hid, kvm_core.py, kvm_main.py, safemode.py, code.py and
                                 settings.toml.example, then stops: you write settings.toml on the Pico yourself
                                 (Wi-Fi, KVM_TOKENS)
  CIRCUITPY, settings.toml    -> copies boot.py LAST; after the next unplug/replug the CIRCUITPY drive is hidden
                                 (hold GP15 to GND while plugging in to get it back)
It never reads out, changes or prints the values in settings.toml (it only checks that the keys are set and are not
the placeholders).

Downloads (pinned, sha256 checked) go to ..\dist\ beside this script:
  CircuitPython 9.2.9 for Pico W (UF2)            1b22caf045ec88db2f45530f5b2aa35d81c0bdb1d69fa3dc2b3b6be1392004a1
  Adafruit CircuitPython Library Bundle 9.x mpy 20261016 (zip)
                                                  125ffab4c10f550d6c6eb8281f3db705db9da0dc69e6f6dabf670f3d37dc269c
(circuitpython.org publishes no hash for the UF2: this one was taken from a download; the bundle hash is GitHub's)

  pwsh -File setup-pico.ps1            # dry run
  pwsh -File setup-pico.ps1 -Apply
#>
param([switch]$Apply)
$ErrorActionPreference = 'Stop'

$Here   = $PSScriptRoot
$Pico   = Join-Path (Split-Path $Here -Parent) 'pico'
$Dist   = Join-Path (Split-Path $Here -Parent) 'dist'
$CpVer  = '9.2.9'
$Uf2    = @{ Name = "adafruit-circuitpython-raspberry_pi_pico_w-en_US-$CpVer.uf2"
             Url  = "https://downloads.circuitpython.org/bin/raspberry_pi_pico_w/en_US/adafruit-circuitpython-raspberry_pi_pico_w-en_US-$CpVer.uf2"
             Sha  = '1b22caf045ec88db2f45530f5b2aa35d81c0bdb1d69fa3dc2b3b6be1392004a1' }
$Bundle = @{ Name = 'adafruit-circuitpython-bundle-9.x-mpy-20261016.zip'
             Url  = 'https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/20261016/adafruit-circuitpython-bundle-9.x-mpy-20261016.zip'
             Sha  = '125ffab4c10f550d6c6eb8281f3db705db9da0dc69e6f6dabf670f3d37dc269c' }

function Say([string]$m) { Write-Host $m }
trap { Say "ERROR $($_.Exception.Message)"; exit 1 }
function Act([string]$what, [scriptblock]$action) {
    if ($Apply) { Say "DO   $what"; & $action } else { Say "WOULD $what" }
}
function Get-Drive([string]$label) {
    $v = Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.FileSystemLabel -eq $label -and $_.DriveLetter }
    if (@($v).Count -gt 1) { throw "more than one volume named ${label}: unplug the others" }
    if ($v) { return "$($v.DriveLetter):\" }
}
function Get-Pinned($f) {
    $p = Join-Path $Dist $f.Name
    if (-not (Test-Path $p)) {
        Act "download $($f.Url)" { New-Item -ItemType Directory -Force $Dist | Out-Null; Invoke-WebRequest -Uri $f.Url -OutFile $p }
        if (-not $Apply) { return $null }
    }
    $h = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $f.Sha) { Remove-Item $p; throw "sha256 of $($f.Name) is $h, expected $($f.Sha): file removed, nothing copied" }
    Say "OK   $($f.Name) sha256 matches"
    return $p
}

Say "setup-pico.ps1 0.2.0 at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  mode: $(if ($Apply) { 'APPLY' } else { 'dry run (add -Apply)' })"
$boot = Get-Drive 'RPI-RP2'
$cp   = Get-Drive 'CIRCUITPY'
if (-not $boot -and -not $cp) {
    Say "No Pico found. Hold BOOTSEL while plugging the Pico W in (drive RPI-RP2), or plug it in normally if CircuitPython is"
    Say "already on it (drive CIRCUITPY). If boot.py is already installed, hold GP15 to GND while plugging in."
    exit 1
}
if ($boot -and $cp) { throw "both RPI-RP2 and CIRCUITPY are present: plug in only one Pico" }

if ($boot) {
    Say "Found RPI-RP2 (BOOTSEL) on $boot"
    $u = Get-Pinned $Uf2
    Act "copy $($Uf2.Name) to $boot (the Pico restarts as CIRCUITPY)" { Copy-Item $u $boot }
    if ($Apply) {
        Say "Waiting up to 60 s for CIRCUITPY ..."
        for ($i = 0; $i -lt 60 -and -not ($cp = Get-Drive 'CIRCUITPY'); $i++) { Start-Sleep -Seconds 1 }
        if (-not $cp) { Say "CIRCUITPY did not appear yet: run this script again once it has."; exit 0 }
    } else { exit 0 }
}

Say "Found CIRCUITPY on $cp"
$bo = Join-Path $cp 'boot_out.txt'
if (Test-Path $bo) {
    $first = (Get-Content $bo -TotalCount 1)
    Say "     $first"
    if ($first -notmatch [regex]::Escape("CircuitPython $CpVer") -or $first -notmatch 'Pico W') {
        Say "WARN not CircuitPython $CpVer for Pico W: to reflash, hold BOOTSEL while plugging in and run again"
    }
}

$z = Get-Pinned $Bundle
if ($z) {
    Act "extract lib\adafruit_hid from the bundle to $($cp)lib\adafruit_hid" {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $tmp = Join-Path $Dist 'adafruit_hid'
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        $zip = [IO.Compression.ZipFile]::OpenRead($z)
        try {
            $entries = $zip.Entries | Where-Object { $_.FullName -match '/lib/adafruit_hid/.+' -and $_.Name }
            if (-not $entries) { throw "adafruit_hid not found in $($Bundle.Name)" }
            foreach ($e in $entries) {
                $rel = $e.FullName -replace '^.*/lib/adafruit_hid/', ''
                $dst = Join-Path $tmp $rel
                New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dst, $true)
            }
        } finally { $zip.Dispose() }
        New-Item -ItemType Directory -Force (Join-Path $cp 'lib') | Out-Null
        Copy-Item -Recurse -Force $tmp (Join-Path $cp 'lib')
    }
}
foreach ($f in 'kvm_core.py', 'kvm_main.py', 'safemode.py', 'code.py') {   # code.py is the launcher
    Act "copy pico\$f to $cp" { Copy-Item (Join-Path $Pico $f) $cp -Force }
}

$st = Join-Path $cp 'settings.toml'
if (-not (Test-Path $st)) {
    Act "copy settings.toml.example to $cp (a template, not used by the Pico)" { Copy-Item (Join-Path $Pico 'settings.toml.example') $cp -Force }
    Say ""
    Say "NEXT (you): copy $(Join-Path $cp 'settings.toml.example') to $st and edit it there (Wi-Fi SSID and password, KVM_TOKENS)."
    Say "Then run this script again: it installs boot.py last."
    exit 0
}

$txt = [string](Get-Content $st -Raw)    # an empty file gives $null: make it ''
$need = @('CIRCUITPY_WIFI_SSID', 'CIRCUITPY_WIFI_PASSWORD', 'KVM_TOKENS')
$missing = $need | Where-Object { $txt -notmatch "(?m)^\s*$_\s*=\s*""[^""]+""" }
if ($missing) { Say "STOP settings.toml lacks: $($missing -join ', ') (boot.py NOT installed). Edit it (template pico\settings.toml.example), then run this script again."; exit 2 }
if ($txt -match 'YOUR-SSID|YOUR-WIFI-PASSWORD|CHANGE-ME|"<|,<|> *"') { Say "STOP settings.toml still has template placeholders (boot.py NOT installed)"; exit 2 }
Say "OK   settings.toml has the Wi-Fi and KVM_TOKENS keys (values not shown)"

Act "copy pico\boot.py to $cp (LAST: after the next replug the CIRCUITPY drive is hidden; GP15 to GND brings it back)" {
    Copy-Item (Join-Path $Pico 'boot.py') $cp -Force
}
if ($Apply) {
    Say ""
    Say "DONE. Eject CIRCUITPY, unplug the Pico and plug it into the target PC. It now shows only as a USB keyboard."
    Say "Find its IP address in your router's DHCP client list, then test from the controller PC:"
    Say "  python client\kvm.py --host <pico-address> status"
    Say "Later firmware updates need no jumper: python client\kvm.py --host <pico-address> push pico\kvm_main.py pico\kvm_core.py"
}
