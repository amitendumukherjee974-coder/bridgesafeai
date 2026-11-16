@echo off
title MineSafe-AI Bridge Dashboard Server
cd /d "c:\Users\AIML\OneDrive\Desktop\minesafe_bridge_dashboard"
echo ============================================================
echo   MineSafe-AI Bridge Structural Health Monitoring Server
echo ============================================================
echo Starting backend server on http://localhost:8000 ...
echo Press Ctrl+C in this window to stop the server.
echo.
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
pause
