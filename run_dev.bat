@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m github_fix gui
