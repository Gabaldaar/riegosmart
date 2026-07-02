# boot.py - Configuraciones iniciales de hardware y red
import gc
from machine import I2C, Pin
import ds3231

# Puesta a cero segura e inmediata para evitar transitorios al energizar
man = Pin(25, Pin.OUT, value=0)      # Rele de dosificación (Válvula)
bomba_rele = Pin(23, Pin.OUT, value=0) # Rele de la bomba
led1 = Pin(4, Pin.OUT, value=0)      # LED Azul (interno)
ref = Pin(2, Pin.OUT, value=0)       # LED Azul Tablero

# Inicialización I2C y periférico (RTC Físico)
i2c = I2C(1, scl=Pin(22), sda=Pin(21), freq=400000)
reloj = ds3231.DS3231(i2c)

# Limpieza exhaustiva de RAM en el arranque
gc.collect()

# Exportar variables para main.py
global_vars = {
    'man': man,
    'bomba_rele': bomba_rele,
    'led1': led1,
    'ref': ref,
    'reloj': reloj
}