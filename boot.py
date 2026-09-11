# boot.py - Configuraciones iniciales de hardware seguras
import gc
from machine import Pin

# Puesta a cero segura e inmediata para evitar ráfagas transitorias al energizar
# Válvula Maestra (MV): Lógica directa (0 = Apagado, 1 = Encendido)
# Zonas de riego: Lógica inversa (1 = Apagado, 0 = Encendido)
MV_PIN = 25
ZONAS_PINS = [18, 23, 26, 27, 19, 32, 33, 14]

try:
    mv = Pin(MV_PIN, Pin.OUT, value=0) # Apagar válvula maestra (lógica directa)
    for p in ZONAS_PINS:
        Pin(p, Pin.OUT, value=1) # Apagar zonas (lógica inversa)
except Exception as e:
    print("Error init pines en boot:", e)

# Limpieza exhaustiva de RAM en el arranque
gc.collect()