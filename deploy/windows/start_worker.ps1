$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "C:\binance-captcha-analyzer\src"
$env:BINANCE_WORKER_BASE_DIR = "C:\binance-captcha-analyzer"
$env:BINANCE_CALLBACK_URL = "https://linux.example.com/api/worker/callback"
$env:BINANCE_WORKER_ID = "windows-01"
Set-Location "C:\binance-captcha-analyzer"
& ".\.venv\Scripts\python.exe" -m uvicorn binance_cloud.worker:app --host 0.0.0.0 --port 8100
