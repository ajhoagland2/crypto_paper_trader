@echo off
cd /d "%~dp0"
echo Starting Crypto Research web app...
echo.
echo Open http://localhost:8766 in your browser.
echo Press Ctrl+C in this window to stop the local app.
echo.
python -m src.web_server
pause
