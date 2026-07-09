@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ======================================================== >> logs\cron_daily.log
echo [START] Running VN Quant Daily Pipeline at %date% %time% >> logs\cron_daily.log
echo ======================================================== >> logs\cron_daily.log

python run_daily.py >> logs\cron_daily.log 2>&1

echo [END] Finished at %date% %time% >> logs\cron_daily.log
echo. >> logs\cron_daily.log
