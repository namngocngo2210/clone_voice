@echo off
set TORCH_LIB=venv\Lib\site-packages\torch\lib
set CTRANS_LIB=venv\Lib\site-packages\ctranslate2
set NVIDIA_CUBLAS_BIN=venv\Lib\site-packages\nvidia\cublas\bin
set DST=dist
set PYTHON_HOME=%LOCALAPPDATA%\Programs\Python\Python311
echo Copying ALL external DLLs to %DST%...

if not exist "%DST%" mkdir "%DST%"

rem Copy all DLLs from torch\lib
copy "%TORCH_LIB%\*.dll" "%DST%\"

rem Copy all DLLs from ctranslate2 (recursive to get all)
for /R "%CTRANS_LIB%" %%f in (*.dll) do copy "%%f" "%DST%\"

rem Copy CUDA 12 cuBLAS runtime DLLs required by newer ctranslate2 builds
if exist "%NVIDIA_CUBLAS_BIN%" copy "%NVIDIA_CUBLAS_BIN%\*.dll" "%DST%\"

rem Copy MSVC runtime DLLs required by torch on machines without VC++ redist
if exist "%PYTHON_HOME%\vcruntime140.dll" copy "%PYTHON_HOME%\vcruntime140.dll" "%DST%\"
if exist "%PYTHON_HOME%\vcruntime140_1.dll" copy "%PYTHON_HOME%\vcruntime140_1.dll" "%DST%\"
if exist "C:\Windows\System32\msvcp140.dll" copy "C:\Windows\System32\msvcp140.dll" "%DST%\"
if exist "C:\Windows\System32\msvcp140_1.dll" copy "C:\Windows\System32\msvcp140_1.dll" "%DST%\"
if exist "C:\Windows\System32\concrt140.dll" copy "C:\Windows\System32\concrt140.dll" "%DST%\"

echo Done.
