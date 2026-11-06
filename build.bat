@echo off
REM Build single-file exe. Requires: pip install pyinstaller
pyinstaller --onefile --windowed --noconfirm --clean --name PowerShare ^
  --hidden-import display_ctl --hidden-import power_fault_view ^
  share_edition.pyw
echo.
echo Output: dist\PowerShare.exe
pause
