@echo off
REM Minimal wrapper for Task Scheduler. Absolute paths, no fancy tricks.

set "PROJECT_DIR=e:\OneDrive - 华兴科技公司\code space\trae\machinery-news-scraper"
set "LOG=%PROJECT_DIR%\logs\scraper.log"

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

echo.                                                                      >> "%LOG%"
echo ===== RUN START %DATE% %TIME% =====                                  >> "%LOG%"
echo cwd: %CD%                                                            >> "%LOG%"

cd /d "%PROJECT_DIR%"
echo after cd: %CD%                                                       >> "%LOG%"

REM Load env vars by sourcing .env (simple line-by-line)
for /f "eol=# tokens=1,* delims==" %%a in (.env) do call set "%%a=%%b"

echo WP_URL=%WP_URL%                                                       >> "%LOG%"
echo PYTHONPATH will be set to src                                         >> "%LOG%"
set PYTHONPATH=src

REM Run scraper
"%PROJECT_DIR%\.venv\Scripts\python.exe" -m machinery_news_scraper run --source all --ai-provider gemini --ai-model gemini-2.5-flash-lite --auto-publish-threshold 3 --ai-rpm 10 --map-categories >> "%LOG%" 2>&1

echo ===== RUN END   %DATE% %TIME% (exit %ERRORLEVEL%) =====              >> "%LOG%"
