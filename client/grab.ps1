#Requires -Version 7
<#
grab.ps1 - take screenshots of the target PC from a USB HDMI/VGA capture stick (for example one based on the
MacroSilicon MS2109, whose DirectShow name is often "USB3. 0 capture") plugged into the controller PC. Needs ffmpeg
on PATH. Version 0.4.0.

Saves <OutDir>\shot-<yyyyMMdd-HHmmss>.png (default OutDir: .\shots) and keeps the newest -Keep (default 20). Prints
the file path. With -List it only lists the DirectShow devices and the capture stick's video modes. Only one program
can use the stick at a time: close OBS or any other viewer first.

  pwsh -File grab.ps1                              # one screenshot into .\shots
  pwsh -File grab.ps1 -OutDir D:\kvm\shots         # somewhere else
  pwsh -File grab.ps1 -List                        # devices and modes
  pwsh -File grab.ps1 -Size 1280x720               # ask the stick for this mode
  pwsh -File grab.ps1 -Every 2                     # one PNG every 2 s until Ctrl+C (fractions such as 0.5 work)
  pwsh -File grab.ps1 -Every 2 -View               # ... and show the live picture in a window
#>
param([string]$Device = 'USB3. 0 capture', [string]$Size = '', [int]$Skip = 10, [int]$Keep = 20, [switch]$List,
      [double]$Every = 0, [switch]$View, [string]$OutDir = '.\shots', [string]$Title = 'pico-kvm')
# -View (with -Every): also show the live picture in a window titled -Title, for a person to watch while an agent
# grabs - one ffmpeg reads the stick once and feeds both (most capture sticks cannot be shared between programs).
# -Every <s>: keep grabbing, one PNG every <s> seconds (ffmpeg stays open), newest $Keep kept, until Ctrl+C.
$ErrorActionPreference = 'Stop'
$ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ff) { Write-Host 'ERROR ffmpeg not found on PATH'; exit 2 }

if ($List) {
    & $ff -hide_banner -list_devices true -f dshow -i dummy 2>&1 | Select-String -Pattern '"' | ForEach-Object { $_.Line.Trim() }
    Write-Host "--- modes of $Device"
    & $ff -hide_banner -f dshow -list_options true -i "video=$Device" 2>&1 | Select-String -Pattern 'vcodec|pixel_format|min s=' | ForEach-Object { $_.Line.Trim() } | Select-Object -Unique
    exit 0
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$dir = (Resolve-Path $OutDir).Path
if ($Every -gt 0) {
    $minKeep = [int][Math]::Ceiling(90 / $Every)          # about 90 s of frames survive
    if ($Keep -lt $minKeep) { $Keep = $minKeep }
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $rate = (1.0 / $Every).ToString('0.######', $inv)    # ffmpeg fps value
    Write-Host "grabbing every $Every s into $dir (newest $Keep kept); Ctrl+C to stop"
    if ($Every -ge 1) {
        $pattern = Join-Path $dir 'shot-%Y%m%d-%H%M%S.png'
        $seq = @('-strftime', '1')
    } else {
        # strftime has 1 s resolution: below 1 s name by start time + frame number (frame n = start + n*Every s)
        $pattern = Join-Path $dir ("shot-{0}-%06d.png" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        $seq = @('-start_number', '0')
    }
    $a = @('-hide_banner', '-loglevel', 'fatal', '-f', 'dshow', '-rtbufsize', '64M')
    if ($Size) { $a += @('-video_size', $Size) }
    $a += @('-i', "video=$Device")
    if ($View) {
        $a += @('-filter_complex', "[0:v]split=2[s][v];[s]fps=$rate[shots];[v]format=yuv420p[live]",
                '-map', '[shots]', '-f', 'image2') + $seq + @('-y', $pattern,
                '-map', '[live]', '-c:v', 'rawvideo', '-f', 'sdl2', $Title)
    } else {
        $a += @('-vf', "fps=$rate", '-f', 'image2') + $seq + @('-y', $pattern)
    }
    # Start-Process joins the list with spaces without quoting: quote every argument that has a space
    $line = ($a | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $p = Start-Process -FilePath $ff -ArgumentList $line -NoNewWindow -PassThru
    try {
        while (-not $p.HasExited) {
            Start-Sleep -Seconds 5
            Get-ChildItem $dir -Filter 'shot-*.png' | Sort-Object Name -Descending | Select-Object -Skip $Keep | Remove-Item -Force -ErrorAction SilentlyContinue
        }
        Write-Host "ffmpeg stopped (exit $($p.ExitCode))"
    } finally { if (-not $p.HasExited) { $p.Kill() } }
    exit 0
}
$out = Join-Path $dir ("shot-{0}.png" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$fa = @('-hide_banner', '-loglevel', 'fatal', '-f', 'dshow', '-rtbufsize', '64M')   # fatal: hide empty first MJPEG frames
if ($Size) { $fa += @('-video_size', $Size) }
# skip the first frames: capture sticks often hand out a black or half-built frame first
$fa += @('-i', "video=$Device", '-vf', "select=gte(n\,$Skip)", '-frames:v', '1', '-y', $out)
& $ff @fa
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) { Write-Host "ERROR ffmpeg exit $LASTEXITCODE (another program holding the device? -List to check the name)"; exit 1 }
Get-ChildItem $dir -Filter 'shot-*.png' | Sort-Object Name -Descending | Select-Object -Skip $Keep | Remove-Item -Force
Write-Host $out
