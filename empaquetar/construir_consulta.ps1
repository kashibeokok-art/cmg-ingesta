# Arma la carpeta CMG_Consulta (ejecutable + LEEME + conexion.ini de ejemplo).
# Uso, desde la carpeta cmg-ingesta:   powershell -File empaquetar/construir_consulta.ps1
# Resultado: empaquetar/dist/ con "Abrir CMG_Consulta.cmd" y la carpeta CMG_Consulta.
# Se copia el CONTENIDO de empaquetar/dist a la carpeta compartida (no build/).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$excluir = @(
    "duckdb", "pyarrow", "curl_cffi", "bs4", "typer", "httpx", "requests",
    "pydantic", "pydantic_settings", "matplotlib", "IPython", "pytest"
) | ForEach-Object { "--exclude-module", $_ }

uv run --no-sync pyinstaller empaquetar/cmg_consulta.py `
    --name CMG_Consulta --onedir --windowed --noconfirm --clean `
    --distpath empaquetar/dist --workpath empaquetar/build --specpath empaquetar/build `
    --paths src `
    --collect-all psycopg_binary --collect-data tzdata `
    @excluir
# un programa externo que falla NO detiene PowerShell: hay que mirar su codigo de salida
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo (codigo $LASTEXITCODE): no se armo nada nuevo." }

$destino = "empaquetar/dist/CMG_Consulta"
Copy-Item empaquetar/LEEME.txt $destino -Force
Copy-Item empaquetar/conexion.ini.example $destino -Force
# el lanzador va AL LADO de la carpeta: copia la app al equipo y abre esa copia
Copy-Item "empaquetar/Abrir CMG_Consulta.cmd" empaquetar/dist -Force
Copy-Item empaquetar/LEEME.txt empaquetar/dist -Force
Write-Host "Listo: empaquetar/dist (Abrir CMG_Consulta.cmd + CMG_Consulta/)"
