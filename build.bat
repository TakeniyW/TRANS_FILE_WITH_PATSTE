@echo off
chcp 65001 >nul
setlocal EnableExtensions
pushd "%~dp0" >nul

echo ============================================
echo  ClipFile 打包脚本
echo  当前目录：%CD%
echo ============================================
echo.

rem ---------- 1. 探测 Python ----------
set "PY="
where py >nul 2>nul
if errorlevel 1 goto :try_python
py -3 --version >nul 2>nul
if errorlevel 1 goto :try_python
set "PY=py -3"
goto :check_python

:try_python
where python >nul 2>nul
if errorlevel 1 goto :no_python
python --version >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=python"

:check_python
echo [信息] Python 版本：
%PY% --version
%PY% -c "import sys; print(sys.executable)"
echo.

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :no_tkinter

if not exist "%CD%\clipfile\gui_sender.py" goto :no_source
if not exist "%CD%\clipfile\gui_receiver.py" goto :no_source
if not exist "%CD%\send_tool.py" goto :no_source
if not exist "%CD%\recv_tool.py" goto :no_source

rem ---------- 2. 检查 PyInstaller ----------
%PY% -m PyInstaller --version >nul 2>nul
if not errorlevel 1 goto :pyinstaller_ready

echo [1/4] 未检测到 PyInstaller，正在安装...
%PY% -m pip install pyinstaller
if errorlevel 1 goto :no_pyinstaller

:pyinstaller_ready
echo [信息] PyInstaller 版本：
%PY% -m PyInstaller --version
echo.

rem ---------- 3. 清理旧产物 ----------
echo [2/4] 清理旧产物...
if exist "%CD%\dist" rmdir /s /q "%CD%\dist"
if exist "%CD%\build" rmdir /s /q "%CD%\build"
if exist "%CD%\send_tool.spec" del /q "%CD%\send_tool.spec" >nul 2>nul
if exist "%CD%\recv_tool.spec" del /q "%CD%\recv_tool.spec" >nul 2>nul

rem ---------- 4. 打包发送端 ----------
echo.
echo [3/4] 打包发送端 dist\send_tool.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name send_tool ^
  --distpath "%CD%\dist" ^
  --workpath "%CD%\build\send" ^
  --specpath "%CD%\build" ^
  "%CD%\send_tool.py"
if errorlevel 1 goto :build_failed
if not exist "%CD%\dist\send_tool.exe" goto :missing_exe

rem ---------- 5. 打包接收端 ----------
echo.
echo [4/4] 打包接收端 dist\recv_tool.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name recv_tool ^
  --distpath "%CD%\dist" ^
  --workpath "%CD%\build\recv" ^
  --specpath "%CD%\build" ^
  "%CD%\recv_tool.py"
if errorlevel 1 goto :build_failed
if not exist "%CD%\dist\recv_tool.exe" goto :missing_exe

echo.
echo ============================================
echo  打包完成
echo  发送端：%CD%\dist\send_tool.exe
echo  接收端：%CD%\dist\recv_tool.exe
echo ============================================
dir /b "%CD%\dist"
echo.
pause
exit /b 0

rem ---------- 错误分支 ----------
:no_python
echo.
echo [失败] 未找到可用的 Python。
echo.
echo 请安装 Python 3.9 或更高版本，安装时务必勾选：
echo   Add Python to PATH
echo.
echo 安装后可执行以下命令自检：
echo   py -3 --version
echo   python --version
echo.
pause
exit /b 1

:no_tkinter
echo.
echo [失败] 当前 Python 缺少 tkinter，无法打包图形界面。
echo.
echo 请卸载后改用 python.org 的官方安装包重装 Python，
echo 并确保安装时包含 tcl/tk 组件（默认包含）。
echo.
pause
exit /b 1

:no_source
echo.
echo [失败] 项目文件不完整。
echo 请确认当前目录下存在以下内容：
echo   clipfile\gui_sender.py
echo   clipfile\gui_receiver.py
echo   send_tool.py
echo   recv_tool.py
echo.
pause
exit /b 1

:no_pyinstaller
echo.
echo [失败] PyInstaller 安装失败。
echo.
echo 常见原因：
echo   1. 无法访问 PyPI，请检查网络或配置国内镜像：
echo      py -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
echo   2. pip 版本过旧：
echo      py -m pip install --upgrade pip
echo   3. Python 版本过新或过旧，建议使用 Python 3.11 / 3.12。
echo.
pause
exit /b 1

:build_failed
echo.
echo [失败] PyInstaller 打包失败，退出码非 0。
echo.
echo 常见原因：
echo   1. Python 版本兼容问题，建议使用 Python 3.11 / 3.12 重试；
echo   2. 杀毒软件拦截了 PyInstaller 生成的临时文件；
echo   3. 项目路径包含特殊字符，可尝试移动到 D:\ClipFile 后重试。
echo.
echo 请把本窗口上方最后 20 行报错信息发给开发者。
echo.
pause
exit /b 1

:missing_exe
echo.
echo [失败] PyInstaller 报告成功，但没有找到目标 exe。
echo.
echo 可能被杀毒软件 / Windows Defender 直接删除。
echo 请检查：
echo   %CD%\dist
echo 并为该目录添加杀毒白名单后重试。
echo.
pause
exit /b 1
