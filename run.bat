@echo off
setlocal
cd /d "%~dp0"
rem Do not use chcp 65001: CP65001 is unstable with Python 3.8 on Windows 7.
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
python -u run.py
if errorlevel 1 (
  echo.
  echo ZJAI-Agent failed to start. Check the error above.
  pause
)
endlocal
