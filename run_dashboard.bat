@echo off
REM Launches the dashboard with no visible console window (via pythonw) -
REM meant to be pointed at by a Windows Task Scheduler task so the
REM dashboard starts automatically and keeps running without you having to
REM keep a terminal open. %~dp0 anchors to this file's own folder, so it
REM works regardless of Task Scheduler's default working directory.
cd /d %~dp0
pythonw scripts\run_dashboard.py >> dashboard.log 2>&1
