"""
MineSafe-AI Bridge Dashboard - Serial & UDP Ingestion Interface
Reads newline-delimited JSON over USB Serial and UDP Wi-Fi socket.
Handles automatic port scanning, reconnects on cable unplug, and skips malformed packets.
"""

import json
import time
import socket
import threading
from typing import Dict, Any, List, Optional
import serial
import serial.tools.list_ports
from backend.data_manager import DataManager


class HardwareInterface:
    def __init__(self, config: Dict[str, Any], data_manager: DataManager):
        self.config = config
        self.dm = data_manager

        self.serial_port_name = config.get("serial", {}).get("port", "AUTO")
        self.baudrate = int(config.get("serial", {}).get("baudrate", 115200))
        self.serial_timeout = float(config.get("serial", {}).get("timeout_sec", 1.0))
        self.reconnect_interval = float(config.get("serial", {}).get("reconnect_interval_sec", 2.0))

        self.udp_enabled = bool(config.get("network", {}).get("udp_enabled", True))
        self.udp_host = config.get("network", {}).get("udp_host", "0.0.0.0")
        self.udp_port = int(config.get("network", {}).get("udp_port", 5005))

        self.serial_conn: Optional[serial.Serial] = None
        self.running = False
        self.serial_thread: Optional[threading.Thread] = None
        self.udp_thread: Optional[threading.Thread] = None

        self.active_port: Optional[str] = None
        self.malformed_packet_count = 0

    @staticmethod
    def list_available_ports() -> List[Dict[str, str]]:
        """
        List connected serial COM/tty ports with descriptions.
        """
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid
            })
        return ports

    def start(self):
        """
        Start serial reader thread and UDP listener thread.
        """
        self.running = True

        self.serial_thread = threading.Thread(target=self._serial_worker, daemon=True)
        self.serial_thread.start()

        if self.udp_enabled:
            self.udp_thread = threading.Thread(target=self._udp_worker, daemon=True)
            self.udp_thread.start()

        print("[HardwareInterface] Ingestion threads started.")

    def stop(self):
        """
        Stop all readers.
        """
        self.running = False
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass

    def set_port(self, port_name: str, baudrate: Optional[int] = None):
        """
        Dynamically update serial target port and reconnect.
        """
        self.serial_port_name = port_name
        if baudrate:
            self.baudrate = baudrate
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        self.active_port = None
        print(f"[HardwareInterface] Serial target changed to {port_name} @ {self.baudrate}")

    def _auto_detect_port(self) -> Optional[str]:
        """
        Find an ESP32 or USB-to-UART bridge port automatically.
        """
        ports = serial.tools.list_ports.comports()
        if not ports:
            return None

        # Prioritize Silicon Labs CP210x, CH340, FTDI, or USB Serial
        for p in ports:
            desc = p.description.lower()
            if any(k in desc for k in ["cp210", "ch340", "ftdi", "usb serial", "esp", "uart"]):
                return p.device

        # Fallback to first available port
        return ports[0].device

    def _serial_worker(self):
        """
        Continuous serial reading loop with automatic reconnect.
        """
        while self.running:
            target_port = self.serial_port_name
            if target_port == "AUTO":
                detected = self._auto_detect_port()
                if not detected:
                    time.sleep(self.reconnect_interval)
                    continue
                target_port = detected

            try:
                print(f"[HardwareInterface] Attempting to open serial port {target_port} @ {self.baudrate}...")
                self.serial_conn = serial.Serial(
                    port=target_port,
                    baudrate=self.baudrate,
                    timeout=self.serial_timeout
                )
                self.active_port = target_port
                print(f"[HardwareInterface] Connected to {target_port}")
                self.dm.log_event("INFO", "Serial", f"Connected to {target_port} @ {self.baudrate}")

                # Read lines until disconnected
                while self.running and self.serial_conn.is_open:
                    line_bytes = self.serial_conn.readline()
                    if not line_bytes:
                        continue
                    try:
                        line_str = line_bytes.decode("utf-8", errors="replace").strip()
                        if line_str:
                            self._process_line(line_str)
                    except Exception as e:
                        self.malformed_packet_count += 1
                        # Malformed lines logged, skipped without crashing
                        continue

            except serial.SerialException as se:
                self.active_port = None
                if self.serial_conn:
                    try:
                        self.serial_conn.close()
                    except Exception:
                        pass
                    self.serial_conn = None
                time.sleep(self.reconnect_interval)
            except Exception as ex:
                self.active_port = None
                time.sleep(self.reconnect_interval)

    def _udp_worker(self):
        """
        UDP socket listener accepting Wi-Fi JSON packets.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.udp_host, self.udp_port))
            sock.settimeout(1.0)
            print(f"[HardwareInterface] UDP listening on {self.udp_host}:{self.udp_port}")
        except Exception as e:
            print(f"[HardwareInterface] Failed to bind UDP socket: {e}")
            return

        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
                line_str = data.decode("utf-8", errors="replace").strip()
                if line_str:
                    self._process_line(line_str)
            except socket.timeout:
                continue
            except Exception as e:
                continue

        try:
            sock.close()
        except Exception:
            pass

    def _process_line(self, line: str):
        """
        Parse JSON line and dispatch to DataManager.
        Malformed lines are logged, counted, and skipped.
        """
        if not (line.startswith("{") and line.endswith("}")):
            return

        try:
            packet = json.loads(line)
            ptype = packet.get("type")
            if ptype == "raw":
                self.dm.ingest_raw_packet(packet)
            elif ptype == "summary":
                self.dm.ingest_summary_packet(packet)
            else:
                self.malformed_packet_count += 1
        except json.JSONDecodeError:
            self.malformed_packet_count += 1
        except Exception as e:
            self.malformed_packet_count += 1
