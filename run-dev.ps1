#!/usr/bin/env pwsh
# Simple dev launcher for Windows PowerShell that runs the Python dev watcher.
Set-StrictMode -Version Latest
$python = "$PSScriptRoot\\.venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python "dev_tools\\dev_watcher.py"