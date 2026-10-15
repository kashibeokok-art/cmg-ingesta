@echo off
rem Abre CMG_Consulta desde una copia LOCAL, que se mantiene igual a la de la red.
rem
rem Por que: abierta desde la red, la app tarda ~1 minuto en aparecer (Windows lee sus
rem ~1.700 archivos uno por uno por la red); desde el disco local, ~2 segundos.
rem Este lanzador copia a %LOCALAPPDATA%\CMG_Consulta solo lo que cambio (la primera
rem vez, todo) y abre esa copia. Una version nueva en la red llega sola a cada usuario.
rem Si no se puede copiar, abre la de la red (lento, pero funciona).
setlocal
set "ORIGEN=%~dp0CMG_Consulta"
set "DESTINO=%LOCALAPPDATA%\CMG_Consulta"
if not "%CMG_DESTINO%"=="" set "DESTINO=%CMG_DESTINO%"

if not exist "%DESTINO%\CMG_Consulta.exe" (
    echo Preparando CMG_Consulta en este equipo. Solo pasa la primera vez y puede
    echo tardar 2 o 3 minutos. Las siguientes veces abre en pocos segundos.
)
robocopy "%ORIGEN%" "%DESTINO%" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
rem robocopy: 0 a 7 = bien (copio o no hacia falta); 8 o mas = fallo
if errorlevel 8 (
    start "" "%ORIGEN%\CMG_Consulta.exe"
) else (
    start "" "%DESTINO%\CMG_Consulta.exe"
)
endlocal
