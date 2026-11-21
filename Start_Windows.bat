@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv .venv
if errorlevel 1 goto fail
goto install
:usepython
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m streamlit run app.py --server.address localhost --browser.gatherUsageStats false
if errorlevel 1 goto fail
exit /b
:fail
echo Install Python 3.12 from python.org and check your internet connection.
pause
