@echo off
REM Re-trains (calibrates) every Kora setup on the last 180 days of H1
REM history from MetaApi. Run monthly, or point a weekly Task Scheduler task at it.
cd /d %~dp0
python scripts\train_kora.py --days 180 >> train_kora.log 2>&1
