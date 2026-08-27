$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "C:\binance-captcha-analyzer\src"
$env:BINANCE_WORKER_BASE_DIR = "C:\binance-captcha-analyzer"
Set-Location "C:\binance-captcha-analyzer"
& ".\.venv\Scripts\python.exe" -m uvicorn binance_cloud.windows.worker:app --host 0.0.0.0 --port 8100
