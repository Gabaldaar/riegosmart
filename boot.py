# boot.py - Configuraciones iniciales de hardware seguras
import gc
from machine import Pin

# Puesta a cero segura e inmediata para evitar ráfagas transitorias al energizar
# Lógica inversa (1 = Apagado, 0 = Encendido)
MV_PIN = 19
ZONAS_PINS = [18, 23, 26, 27, 25, 32, 33, 14]

try:
    mv = Pin(MV_PIN, Pin.OUT, value=1)
    for p in ZONAS_PINS:
        Pin(p, Pin.OUT, value=1)
except Exception as e:
    print("Error init pines en boot:", e)

# Limpieza exhaustiva de RAM en el arranque
gc.collect()