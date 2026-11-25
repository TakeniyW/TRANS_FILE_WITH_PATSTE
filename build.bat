@echo off
setlocal EnableExtensions
pushd "%~dp0" >nul

echo ============================================
echo  ClipFile build script
echo  Directory: %CD%
echo ============================================
echo.

rem ---------- 1. Detect Python ----------
set "PY="
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 --version >nul 2>nul
if errorlevel 1 goto try_python
set "PY=py -3"
goto check_python

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python --version >nul 2>nul
if errorlevel 1 goto no_python
set "PY=python"

:check_python
echo [INFO] Python:
%PY% --version
%PY% -c "import sys; print(sys.executable)"
echo.

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto no_tkinter

if not exist "%CD%\clipfile\gui_sender.py" goto no_source
if not exist "%CD%\clipfile\gui_receiver.py" goto no_source
if not exist "%CD%\send_tool.py" goto no_source
if not exist "%CD%\recv_tool.py" goto no_source

rem ---------- 2. Check PyInstaller ----------
%PY% -m PyInstaller --version >nul 2>nul
if not errorlevel 1 goto pyinstaller_ready
echo [1/4] PyInstaller not found, installing...
%PY% -m pip install pyinstaller
if errorlevel 1 goto no_pyinstaller

:pyinstaller_ready
echo [INFO] PyInstaller:
%PY% -m PyInstaller --version
echo.

rem ---------- 3. Clean old output ----------
echo [2/4] Cleaning old output...
if exist "%CD%\dist" rmdir /s /q "%CD%\dist"
if exist "%CD%\build" rmdir /s /q "%CD%\build"
if exist "%CD%\send_tool.spec" del /q "%CD%\send_tool.spec" >nul 2>nul
if exist "%CD%\recv_tool.spec" del /q "%CD%\recv_tool.spec" >nul 2>nul

rem ---------- 4. Build sender ----------
echo.
echo [3/4] Building dist\send_tool.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name send_tool --distpath "%CD%\dist" --workpath "%CD%\build\send" --specpath "%CD%\build" "%CD%\send_tool.py"
if errorlevel 1 goto build_failed
if not exist "%CD%\dist\send_tool.exe" goto missing_exe

rem ---------- 5. Build receiver ----------
echo.
echo [4/4] Building dist\recv_tool.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name recv_tool --distpath "%CD%\dist" --workpath "%CD%\build\recv" --specpath "%CD%\build" "%CD%\recv_tool.py"
if errorlevel 1 goto build_failed
if not exist "%CD%\dist\recv_tool.exe" goto missing_exe

echo.
echo ============================================
echo  BUILD OK
echo  Sender:   %CD%\dist\send_tool.exe
echo  Receiver: %CD%\dist\recv_tool.exe
echo ============================================
dir /b "%CD%\dist"
echo.
pause
exit /b 0

rem ---------- Error branches ----------
:no_python
echo.
echo [ERROR] No usable Python found.
echo.
echo Install Python 3.9+ from https://www.python.org/downloads/
echo During installation, enable "Add Python to PATH".
echo.
echo Then verify with:
echo   py -3 --version
echo   python --version
echo.
pause
exit /b 1

:no_tkinter
echo.
echo [ERROR] This Python does not include tkinter.
echo.
echo Reinstall Python from python.org and keep the tcl/tk component
echo (it is included by default in the official installer).
echo.
pause
exit /b 1

:no_source
echo.
echo [ERROR] Project files are incomplete.
echo Run this script from the repository root. Required files:
echo   clipfile\gui_sender.py
echo   clipfile\gui_receiver.py
echo   send_tool.py
echo   recv_tool.py
echo.
pause
exit /b 1

:no_pyinstaller
echo.
echo [ERROR] Failed to install PyInstaller.
echo.
echo Try one of the following:
echo   1. Check network access, or use a mirror:
echo      py -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
echo   2. Upgrade pip:
echo      py -m pip install --upgrade pip
echo   3. Use Python 3.11 or 3.12 if the current version is too new.
echo.
pause
exit /b 1

:build_failed
echo.
echo [ERROR] PyInstaller exited with a non-zero code.
echo.
echo Common causes:
echo   1. Unsupported Python version. Try Python 3.11 or 3.12.
echo   2. Antivirus blocked PyInstaller temporary files.
echo   3. Special characters in the project path. Try D:\ClipFile.
echo.
echo Send the last 20 lines above to the developer for diagnosis.
echo.
pause
exit /b 1

:missing_exe
echo.
echo [ERROR] PyInstaller reported success but the exe is missing.
echo.
echo It was probably deleted by antivirus / Windows Defender.
echo Add this folder to the antivirus whitelist and retry:
echo   %CD%\dist
echo.
pause
exit /b 1
