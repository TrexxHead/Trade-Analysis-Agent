@echo off
REM Launches the periodic scanner with no visible console window (via
REM pythonw) - meant to be pointed at by a Windows Task Scheduler task so
REM scanning keeps running in the background without a terminal open.
REM %~dp0 anchors to this file's own folder, so it works regardless of Task
REM Scheduler's default working directory.
cd /d %~dp0
pythonw scripts\run_scan_loop.py --interval-minutes 20 >> scan_loop.log 2>&1
