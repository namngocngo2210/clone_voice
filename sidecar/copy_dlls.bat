@echo off
set TORCH_LIB=venv\Lib\site-packages\torch\lib
set CTRANS_LIB=venv\Lib\site-packages\ctranslate2
set DST=dist
echo Copying ALL external DLLs to %DST%...

if not exist "%DST%" mkdir "%DST%"

rem Copy all DLLs from torch\lib
copy "%TORCH_LIB%\*.dll" "%DST%\"

rem Copy all DLLs from ctranslate2 (recursive to get all)
for /R "%CTRANS_LIB%" %%f in (*.dll) do copy "%%f" "%DST%\"

echo Done.
