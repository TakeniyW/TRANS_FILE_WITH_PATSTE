@echo off
setlocal
cd /d "%~dp0"

echo [1/3] 安装 / 升级 PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :failed

echo [2/3] 打包发送端 send_tool.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name send_tool send_tool.py
if errorlevel 1 goto :failed

echo [3/3] 打包接收端 recv_tool.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name recv_tool recv_tool.py
if errorlevel 1 goto :failed

echo.
echo 打包完成：dist\send_tool.exe 与 dist\recv_tool.exe
pause
exit /b 0

:failed
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
