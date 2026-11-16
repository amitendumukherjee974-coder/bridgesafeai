"""
MineSafe-AI Bridge Health Monitoring - FastAPI Server & WebSocket Hub
Provides real-time telemetry streaming, calibration controls, replay, and report generation.
"""

import os
import sys
import yaml
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.data_manager import DataManager
from backend.serial_reader import HardwareInterface

# Project root paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)


# Initialize App & Services
config = load_config()
dm = DataManager(config=config, base_dir=BASE_DIR)
hw = HardwareInterface(config=config, data_manager=dm)

app = FastAPI(
    title="MineSafe-AI Bridge Dashboard",
    description="Real-Time Structural Health Monitoring for Bridges - College Innovation Demo",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def startup_event():
    hw.start()
    print("[Server] Started Hardware Ingestion Interface.")


@app.on_event("shutdown")
async def shutdown_event():
    hw.stop()
    dm.stop_recording()
    dm.stop_replay()
    print("[Server] Stopped all services.")


# --------------------------------------------------------------------------
# Frontend Page
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Dashboard HTML not found. Build in progress.</h1>", status_code=404)


# --------------------------------------------------------------------------
# REST API Endpoints
# --------------------------------------------------------------------------
@app.get("/api/status")
async def get_status():
    dm.check_connection_watchdog()
    snapshot = dm.get_ui_snapshot()
    snapshot["active_port"] = hw.active_port or "Disconnected"
    return snapshot


@app.get("/api/ports")
async def get_serial_ports():
    return {
        "ports": hw.list_available_ports(),
        "active_port": hw.active_port,
        "configured_port": hw.serial_port_name,
        "baudrate": hw.baudrate
    }


class ConnectRequest(BaseModel):
    port: str
    baudrate: Optional[int] = 115200


@app.post("/api/connect")
async def connect_serial(req: ConnectRequest):
    hw.set_port(req.port, req.baudrate)
    return {"status": "ok", "port": req.port, "baudrate": req.baudrate}


@app.post("/api/calibrate")
async def start_calibration(duration: Optional[float] = 30.0):
    dm.calibration_engine.start_calibration(duration or 30.0)
    dm.log_event("INFO", "Operator", f"Started {duration}s baseline calibration.")
    return {"status": "started", "duration_sec": duration}


@app.post("/api/recording/start")
async def start_recording():
    filename = dm.start_recording()
    return {"status": "recording", "filename": filename}


@app.post("/api/recording/stop")
async def stop_recording():
    fname = dm.stop_recording()
    return {"status": "stopped", "filename": fname}


@app.get("/api/recordings")
async def list_recordings():
    return {"recordings": dm.list_recordings()}


class ReplayRequest(BaseModel):
    filename: str


@app.post("/api/replay/start")
async def start_replay(req: ReplayRequest):
    success = dm.start_replay(req.filename)
    if not success:
        raise HTTPException(status_code=404, detail="Recording file not found")
    return {"status": "replaying", "filename": req.filename}


@app.post("/api/replay/stop")
async def stop_replay():
    dm.stop_replay()
    return {"status": "stopped"}


class MarkEventRequest(BaseModel):
    note: str
    level: Optional[str] = "NOTE"


@app.post("/api/mark_event")
async def mark_event(req: MarkEventRequest):
    dm.log_event(level=req.level or "NOTE", source="Operator", message=req.note, raw_value="Manual Annotation")
    return {"status": "logged", "note": req.note}


@app.get("/api/config")
async def get_configuration():
    return load_config()


@app.post("/api/config")
async def update_configuration(new_cfg: Dict[str, Any] = Body(...)):
    global config
    save_config(new_cfg)
    config = new_cfg
    dm.config = new_cfg
    dm.risk_engine.config = new_cfg
    dm.risk_engine.thresholds = new_cfg.get("thresholds", {})
    return {"status": "updated"}


@app.get("/api/report/export", response_class=HTMLResponse)
async def export_html_report():
    html_content = dm.generate_html_report()
    return HTMLResponse(content=html_content)


# --------------------------------------------------------------------------
# High-Speed WebSocket Streaming (20 Hz)
# --------------------------------------------------------------------------
@app.websocket("/ws/live")
async def websocket_live_stream(websocket: WebSocket):
    await websocket.accept()
    print("[WebSocket] Client connected to live stream.")
    try:
        while True:
            dm.check_connection_watchdog()
            snapshot = dm.get_ui_snapshot()
            snapshot["active_port"] = hw.active_port or "Disconnected"
            await websocket.send_json(snapshot)
            # Stream at 20 Hz (50 ms intervals)
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        print("[WebSocket] Client disconnected.")
    except Exception as e:
        print(f"[WebSocket] Stream error: {e}")
