import json
import os
import uasyncio as asyncio

LOG_FILE = "sys_log.jsonl"
LOG_FILE_OLD = "sys_log.old"
MAX_FILE_SIZE_BYTES = 4096  # Límite de 4 KB para evitar sobrecarga de Flash y RAM

# Lock para evitar colisiones al escribir/leer
_log_lock = asyncio.Lock()

# Buffer circular en RAM para logs en modo offline/BLE-only
logs_ram = []
MAX_RAM_LOGS = 3

async def log_event(event_dict, wifi_activo=None):
    """
    Registra un evento de forma asíncrona.
    Si wifi_activo es False (o la conexión WiFi está inactiva), guarda en RAM.
    Si wifi_activo es True, hace append en flash con rotación segura.
    """
    import time
    event_dict["ts"] = time.time()
    
    if wifi_activo is None:
        try:
            import network
            wlan = network.WLAN(network.STA_IF)
            wifi_activo = wlan.active() and wlan.isconnected()
        except:
            wifi_activo = True

    if not wifi_activo:
        # Buffer circular en RAM (BLE-only / Offline) para evitar fragmentación e impacto en flash
        global logs_ram
        logs_ram.append(event_dict)
        if len(logs_ram) > MAX_RAM_LOGS:
            logs_ram.pop(0)
        print("[LOG_RAM]", event_dict)
        return

    async with _log_lock:
        try:
            # 1. Verificar tamaño físico del archivo antes de escribir
            try:
                stat = os.stat(LOG_FILE)
                if stat[6] >= MAX_FILE_SIZE_BYTES:
                    # Rotación atómica simple
                    try:
                        os.remove(LOG_FILE_OLD)
                    except OSError:
                        pass
                    os.rename(LOG_FILE, LOG_FILE_OLD)
                    print("[LOG] Archivo rotado exitosamente.")
            except OSError:
                # El archivo no existe aún, se creará al abrir en modo append
                pass

            # 2. Append directo sin cargar todo el archivo a RAM
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(event_dict) + "\n")
        except Exception as e:
            print("SysLog Error:", e)

async def get_logs(incluir_ram=True):
    """Devuelve la lista de eventos guardados (combina RAM y Flash si se requiere)."""
    logs = []
    if incluir_ram:
        logs.extend(logs_ram)
        
    async with _log_lock:
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
    """Borra el archivo de logs y limpia el buffer en RAM."""
    global logs_ram
    logs_ram = []
    async with _log_lock:
        try:
            with open(LOG_FILE, "w") as f:
                f.write("")
            try:
                os.remove(LOG_FILE_OLD)
            except OSError:
                pass
        except Exception as e:
            print("SysLog Clear Error:", e)

