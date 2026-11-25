@echo off
REM Launches Kora - the market-analysis agent (live feed + scheduled multi-
REM timeframe analysis + alerts) - with no visible console window, for a
REM Windows Task Scheduler logon task. Kora never places orders.
REM %~dp0 anchors to this file's folder regardless of the working directory.
cd /d %~dp0
pythonw scripts\run_kora.py >> kora.log 2>&1
