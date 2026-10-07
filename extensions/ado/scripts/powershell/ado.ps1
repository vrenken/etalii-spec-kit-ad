#!/usr/bin/env pwsh

# Thin launcher for the ado extension's Python helper.
#
# All behaviour lives in ../python/ado.py so the bash, PowerShell and Python
# entry points cannot drift apart. Usage: ./ado.ps1 <command> [options]

$helper = Join-Path (Join-Path (Join-Path $PSScriptRoot '..') 'python') 'ado.py'

foreach ($candidate in @('python3', 'python', 'py')) {
    $python = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source $helper @args
        exit $LASTEXITCODE
    }
}

[Console]::Error.WriteLine('{"error": "Python 3 is required for the ado extension but was not found on PATH."}')
exit 1
