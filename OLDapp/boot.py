# boot.py - Configuraciones iniciales de hardware y red
import network
from machine import I2C, Pin
import ds3231
import at24c32n

# Configuración de pines GPIO
man = Pin(25, Pin.OUT)      # Rele de dosificación
led1 = Pin(4, Pin.OUT)      # LED Azul (interno)
ref = Pin(2, Pin.OUT)       # LED Azul Tablero
#bomba = Pin(34, Pin.IN)     # Entrada de estado de la bomba

# Inicialización I2C y periféricos
i2c = I2C(1, scl=Pin(22), sda=Pin(21), freq=400000)
reloj = ds3231.DS3231(i2c)
#reloj.halt(False)  # Asegurar que el RTC está funcionando
#eeprom = at24c32n.AT24C32N(i2c)
eeprom = at24c32n.AT24C32N(i2c, i2c_addr=0x57)

# Configuración del Access Point
'''def setup_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="DOSIMAT_IoT", password="12345678", authmode=network.AUTH_WPA_WPA2_PSK)
    print("Access Point configurado")
    print("IP:", ap.ifconfig()[0])'''
    
def setup_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)

    ap.config(
        essid="DOSIMAT_IoT",
        password="12345678",
        authmode=network.AUTH_WPA_WPA2_PSK,
        channel=6,  # canal seguro
        max_clients=4
    )

    # Configuración IP estable
    ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '8.8.8.8'))

    print("Access Point configurado")
    print("IP:", ap.ifconfig()[0])
   

# Puesta a cero segura de los pines
def safe_start():
    man.value(0)
    led1.value(0)
    ref.value(0)

# Ejecutar configuraciones iniciales
safe_start()
setup_ap()

# Exportar variables para main.py
global_vars = {
    'man': man,
    'led1': led1,
    'ref': ref,
    'reloj': reloj,
    'eeprom': eeprom,
    'i2c': i2c
}