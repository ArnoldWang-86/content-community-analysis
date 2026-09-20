@echo off
chcp 65001 >nul
title 采集进度查看
cd /d "D:\找工作\数据分析类\项目-内容社区分析"

echo ============================================================
echo 采集进度
echo ============================================================
echo.

REM 是否还在跑
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" ^| Where-Object { $_.CommandLine -like '*01_collect_search*' } ^| Measure-Object).Count"') do set RUNNING=%%i
if "%RUNNING%"=="0" (
    echo 状态: 采集已结束或未运行
) else (
    echo 状态: 采集中... ^(进程数 %RUNNING%^)
)
echo.

python src\progress.py

echo.
echo ============================================================
pause
