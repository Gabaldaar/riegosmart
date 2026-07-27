# reset_fabrica.py
# Ejecutar en el ESP32 via Thonny o mpremote para dejar el equipo como nuevo.
# Borra: token de acceso, credenciales WiFi y logs.
# NO borra la configuracion de zonas/programas (solo el acceso y red).

import os
import json

ARCHIVOS_RED = ["wifi_config.json", "wifi_config.json.tmp"]
ARCHIVOS_LOG = ["sys_log.jsonl", "sys_log.old"]
CONFIG_RIEGO = "config_riego.json"

print("=== RESET DE FABRICA ===")

# 1. Borrar credenciales WiFi
for f in ARCHIVOS_RED:
    try:
        os.remove(f)
        print(f"[OK] Eliminado: {f}")
    except OSError:
        print(f"[--] No existe: {f}")

# 2. Borrar logs
for f in ARCHIVOS_LOG:
    try:
        os.remove(f)
        print(f"[OK] Eliminado: {f}")
    except OSError:
        print(f"[--] No existe: {f}")

# 3. Limpiar token_acceso en config_riego.json (mantiene zonas y programas)
try:
    with open(CONFIG_RIEGO, "r") as f:
        config = json.load(f)
    config["token_acceso"] = None
    config["config_version"] = 0
    with open(CONFIG_RIEGO, "w") as f:
        json.dump(config, f)
    print("[OK] token_acceso limpiado en config_riego.json")
except OSError:
    print("[--] config_riego.json no existe (se creara por defecto al arrancar)")

print("")
print("Listo. Reinicia el ESP32 para aplicar los cambios.")
print("El equipo arrancara en modo BLE esperando configuracion.")
