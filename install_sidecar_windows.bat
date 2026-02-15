@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Run from repo root regardless of current directory
cd /d "%~dp0"

if not exist "sidecar" (
  echo [ERROR] Cannot find "sidecar" folder. Please run this script from repo root.
  exit /b 1
)

set "PIP_CACHE_DIR=%cd%\_pip_cache"
set "TEMP=%cd%\_temp"
set "TMP=%cd%\_temp"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%TEMP%" mkdir "%TEMP%"

echo [1/7] Ensure eSpeak NG is installed...
if exist "C:\Program Files\eSpeak NG\espeak-ng.exe" (
  echo eSpeak NG already installed.
) else (
  winget install --id eSpeak-NG.eSpeak-NG --exact --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo [ERROR] Failed to install eSpeak NG via winget.
    exit /b 1
  )
)

echo [2/7] Create/refresh Python venv...
py -3.11 -m venv --clear sidecar\venv
if errorlevel 1 (
  echo [ERROR] Failed to create venv with Python 3.11.
  exit /b 1
)

echo [3/7] Upgrade pip tooling...
call sidecar\venv\Scripts\activate.bat
python -m pip install --upgrade pip wheel "setuptools==80.10.2"
if errorlevel 1 (
  echo [ERROR] Failed to bootstrap pip tooling.
  exit /b 1
)

echo [4/7] Install PyTorch CUDA 12.8 wheels...
python -m pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.10.0+cu128 torchaudio==2.10.0+cu128 torchvision==0.25.0+cu128
if errorlevel 1 (
  echo [ERROR] Failed to install torch/cu128 stack.
  exit /b 1
)

echo [5/7] Install model packages (no dependency solve)...
python -m pip install --no-deps chatterbox-tts==0.1.6 vieneu==1.1.7 neucodec==0.0.5
if errorlevel 1 (
  echo [ERROR] Failed to install model packages.
  exit /b 1
)

echo [6/7] Install curated runtime dependencies...
python -m pip install -r sidecar\requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install runtime dependencies.
  exit /b 1
)

echo [7/7] Verify runtime imports...
python -c "import torch, vieneu, chatterbox, neucodec, phonemizer; print('OK|torch', torch.__version__, '|cuda', torch.version.cuda, '|cuda_available', torch.cuda.is_available())"
if errorlevel 1 (
  echo [ERROR] Verification failed.
  exit /b 1
)

echo.
echo [DONE] Sidecar environment is ready.
echo Use: npm run tauri dev
exit /b 0
