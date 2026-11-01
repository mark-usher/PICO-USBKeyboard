#Requires -Version 7
<#
watch.ps1 - a window that shows the newest screenshot written by grab.ps1 -Every and refreshes itself, so a person
can watch along while an agent drives the keyboard. It only reads the PNGs; it never opens the capture stick, so it
works whoever runs the capture (the folder may also be a synced or shared folder). Windows only. Version 0.1.0.

  pwsh -File watch.ps1                         # newest of .\shots, every 1.5 s
  pwsh -File watch.ps1 -Dir \\server\kvm\shots # another folder
#>
param([string]$Dir = '.\shots', [int]$Ms = 1500, [string]$Title = 'pico-kvm')
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$form = [Windows.Forms.Form]::new()
$form.Text = "$Title (waiting for screenshots)"; $form.Width = 1000; $form.Height = 600; $form.BackColor = 'Black'
$pic = [Windows.Forms.PictureBox]::new(); $pic.Dock = 'Fill'; $pic.SizeMode = 'Zoom'
$form.Controls.Add($pic)
$script:last = ''
$timer = [Windows.Forms.Timer]::new(); $timer.Interval = $Ms
$timer.add_Tick({
    try {
        $f = Get-ChildItem $Dir -Filter 'shot-*.png' -ErrorAction Stop | Sort-Object Name -Descending | Select-Object -First 1
        if ($f -and $f.FullName -ne $script:last) {
            $bytes = [IO.File]::ReadAllBytes($f.FullName)          # read into memory: never lock the file
            $img = [Drawing.Image]::FromStream([IO.MemoryStream]::new($bytes))
            $old = $pic.Image; $pic.Image = $img; if ($old) { $old.Dispose() }
            $script:last = $f.FullName
            $age = [int]((Get-Date) - $f.LastWriteTime).TotalSeconds
            $form.Text = "$Title  $($f.Name)  ($age s old)"
        } elseif ($f) {
            $form.Text = "$Title  $($f.Name)  ($([int]((Get-Date) - $f.LastWriteTime).TotalSeconds) s old)"
        }
    } catch { }   # a half-written PNG: try again next tick
})
$timer.Start()
[void]$form.ShowDialog()
$timer.Stop()
