import json
import os
import uasyncio as asyncio

LOG_FILE = "sys_log.jsonl"
MAX_LINES = 20
KEEP_LINES = 15

# Lock para evitar colisiones al escribir/leer
_log_lock = asyncio.Lock()

async def log_event(event_dict):
    """
    Registra un evento de forma asíncrona en formato JSONL.
    Rota el archivo si supera MAX_LINES.
    """
    import time
    event_dict["ts"] = time.time()
    
    async with _log_lock:
        try:
            lines = []
            try:
                with open(LOG_FILE, "r") as f:
                    lines = f.readlines()
            except OSError:
                pass

            if len(lines) >= MAX_LINES:
                lines = lines[-KEEP_LINES:]
                
            json_str = json.dumps(event_dict)
            lines.append(json_str + "\n")
            
            with open(LOG_FILE, "w") as f:
                for line in lines:
                    f.write(line)
        except Exception as e:
            print("SysLog Error:", e)

async def get_logs():
    """Devuelve la lista de eventos guardados."""
    async with _log_lock:
        logs = []
        try:
            with open(LOG_FILE, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            logs.append(json.loads(line))
                        except ValueError:
                            pass
        except OSError:
            pass
        return logs

async def limpiar_historial():
    """Borra el archivo de logs."""
    async with _log_lock:
        try:
            with open(LOG_FILE, "w") as f:
                f.write("")
        except Exception as e:
            print("SysLog Clear Error:", e)
