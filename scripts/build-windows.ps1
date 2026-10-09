$ErrorActionPreference='Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m pip install -r windows-build-requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed' }
python -m PyInstaller --noconfirm --clean --onefile --name FamilyTradingBot --collect-all tzdata --collect-data certifi --add-data 'web;web' --add-data 'skills;skills' --add-data 'scanner.json;.' --add-data 'stock_universe.json;.' --add-data 'tradingview_template.pine;.' --add-data '.env.example;.' windows_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Windows executable build failed' }
python scripts/windows-manifest.py
if ($LASTEXITCODE -ne 0) { throw 'Update manifest build failed' }
