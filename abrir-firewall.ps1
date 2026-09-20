# Abrir el puerto 8000 en el Firewall de Windows.
#
# Necesario para acceder al servicio desde el movil u otro equipo de la red.
# Hay que ejecutarlo COMO ADMINISTRADOR:
#   clic derecho sobre este archivo -> "Ejecutar con PowerShell"
# o desde una consola elevada:
#   powershell -ExecutionPolicy Bypass -File .\abrir-firewall.ps1

$ErrorActionPreference = "Stop"

$esAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $esAdmin) {
    Write-Host "Este script necesita permisos de administrador." -ForegroundColor Yellow
    Write-Host "Cierra esta ventana y ejecutalo de nuevo con clic derecho >" -ForegroundColor Yellow
    Write-Host "'Ejecutar con PowerShell' eligiendo la opcion de administrador." -ForegroundColor Yellow
    Read-Host "Pulsa Enter para salir"
    exit 1
}

$puerto = 8000
$nombre = "Descargador Web (puerto $puerto)"

$existente = Get-NetFirewallRule -DisplayName $nombre -ErrorAction SilentlyContinue
if ($existente) {
    Write-Host "La regla '$nombre' ya existe. No se hace nada." -ForegroundColor Green
} else {
    New-NetFirewallRule `
        -DisplayName $nombre `
        -Description "Permite acceder al servicio de descargas desde la red local." `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $puerto `
        -Action Allow `
        -Profile Any | Out-Null
    Write-Host "Regla creada: se permite el trafico entrante al puerto $puerto." -ForegroundColor Green
}

Write-Host ""
Write-Host "Direcciones para acceder desde el movil:" -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254\.)" } |
    ForEach-Object { Write-Host ("  http://{0}:{1}" -f $_.IPAddress, $puerto) }

Write-Host ""
Write-Host "Para quitar la regla mas adelante:" -ForegroundColor DarkGray
Write-Host "  Remove-NetFirewallRule -DisplayName '$nombre'" -ForegroundColor DarkGray
Read-Host "Pulsa Enter para cerrar"
