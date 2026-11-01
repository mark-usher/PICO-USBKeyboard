#Requires -Version 7
<#
set-token.ps1 - make this controller PC's token and store it in the Windows user environment variable
PICO_KVM_TOKEN (read by client\kvm.py). Version 0.1.0. Run once on each controller PC: each gets its own token, and
the Pico's settings.toml lists them all in KVM_TOKENS, so one PC can be revoked alone.

  pwsh -File set-token.ps1          # make a token if none is set; show it ONCE so you can put it in settings.toml
  pwsh -File set-token.ps1 -Force   # replace this PC's token (then update KVM_TOKENS on the Pico)

The token is shown once, on this console only: type or paste it into settings.toml on the Pico. Never put it in a
chat, a repository or a log. Open a new terminal afterwards so the variable is visible to new processes.
On Linux/macOS, generate one with e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` and export
PICO_KVM_TOKEN from your shell profile or a secrets manager.
#>
param([switch]$Force)
$ErrorActionPreference = 'Stop'
$name = 'PICO_KVM_TOKEN'
$old = [Environment]::GetEnvironmentVariable($name, 'User')
if ($old -and -not $Force) {
    Write-Host "$name is already set for $env:USERNAME on $env:COMPUTERNAME (value not shown). Use -Force to replace it."
    exit 0
}
$bytes = [byte[]]::new(24)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$prefix = $env:COMPUTERNAME.ToLower()      # names the controller in KVM_TOKENS, so it can be revoked alone
$token = $prefix + '-' + ([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_'))
[Environment]::SetEnvironmentVariable($name, $token, 'User')
Write-Host "$name set for $env:USERNAME on $env:COMPUTERNAME ($($token.Length) characters)."
Write-Host ""
Write-Host "Your token (shown once; copy it into KVM_TOKENS in settings.toml on the Pico, comma-separated with any others):"
Write-Host ""
Write-Host "    $token"
Write-Host ""
Write-Host "Clear this window afterwards (cls). Open a new terminal before using client\kvm.py."
