# boot.py - Configuraciones iniciales de hardware y red
import gc
from machine import I2C, Pin
import ds3231

# Puesta a cero segura e inmediata para evitar transitorios al energizar
man = Pin(25, Pin.OUT, value=0)      # Rele de dosificación (Válvula)
bomba_rele = Pin(23, Pin.OUT, value=0) # Rele de la bomba
led1 = Pin(4, Pin.OUT, value=0)      # LED Azul (interno)
ref = Pin(2, Pin.OUT, value=0)       # LED Azul Tablero

# Inicialización I2C (Usamos SoftI2C para evitar cuelgues de hardware por interferencia electromagnética de los relés)
from machine import SoftI2C
i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000)
reloj = ds3231.DS3231(i2c)

import at24c32n
eeprom = at24c32n.AT24C32N(i2c, i2c_addr=0x57)

# Limpieza exhaustiva de RAM en el arranque
gc.collect()

# Exportar variables para main.py
global_vars = {
    'man': man,
    'bomba_rele': bomba_rele,
    'led1': led1,
    'ref': ref,
    'reloj': reloj,
    'eeprom': eeprom
}