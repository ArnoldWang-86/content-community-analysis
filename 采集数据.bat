@echo off
chcp 65001 >nul
title 内容社区数据采集
cd /d "D:\找工作\数据分析类\项目-内容社区分析"

REM ---------- 防重复运行检查 ----------
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*01_collect_search*' } | Measure-Object).Count"') do set RUNNING=%%i
if not "%RUNNING%"=="0" (
    echo.
    echo [警告] 采集已经在运行中，请不要重复启动！
    echo.
    echo 同时跑多个采集进程会触发 B站限流，
    echo 导致接口返回大量重复数据（实测重复率高达 96%%，数据会作废）。
    echo.
    echo 如果确认要强制重跑，请先在任务管理器结束 python.exe。
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo 内容社区数据采集（B站搜索接口，28个关键词 x 50页）
echo 预计耗时约 40 分钟
echo 支持断点续跑，中途关闭窗口不会丢数据
echo 数据落在 data\raw\search_raw.jsonl
echo ============================================================
echo.

python src\01_collect_search.py

echo.
echo ============================================================
echo 采集结束，按任意键关闭
echo ============================================================
pause
