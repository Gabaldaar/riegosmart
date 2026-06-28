# main.py - Lógica del dosificador con Control Proactivo de Bomba (Híbrido Nube/BLE) - version OK
import time
from boot import global_vars
import gc
import sys
import socket
import struct
from machine import WDT, ADC, Pin, Timer

class MockSocketModule:
    pass
mock_socket = MockSocketModule()
for k in dir(socket):
    setattr(mock_socket, k, getattr(socket, k))

_orig_socket = socket.socket
def custom_socket(*args, **kwargs):
    s = _orig_socket(*args, **kwargs)
    s.settimeout(10.0)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    except: pass
    return s
mock_socket.socket = custom_socket

sys.modules['usocket'] = mock_socket
sys.modules['socket'] = mock_socket
import json
import network
import urequests
import binascii
import machine
import os
import socket

# Inicialización de hardware básico
ref = global_vars['ref'] # LED indicador del tablero
ref.value(1) 
time.sleep(3) # Ventana para detener con Ctrl + C antes del WD
ref.value(0)

# --- INICIO FAIL-SAFE ---
try:
    import os
    with open("crash_count.txt", "r") as f:
        crash_count = int(f.read().strip())
except:
    crash_count = 0

if crash_count >= 3:
    print("FAIL-SAFE: 3 crasheos consecutivos detectados. Borrando Wi-Fi...")
    try: os.remove("wifi_config.json")
    except: pass
    try: os.remove("crash_count.txt")
    except: pass
    crash_count = 0
else:
    try:
        with open("crash_count.txt", "w") as f:
            f.write(str(crash_count + 1))
    except: pass
# --- FIN FAIL-SAFE ---

# Inicializar el Watchdog y su alimentador asíncrono
wdt = WDT(timeout=15000)
last_activity = time.time()

def wdt_feeder(t):
    # El timeout de TCP/TLS del ESP32 es de 120 seg. Si esperamos 150 seg, le damos tiempo a la red
    # para que tire un error de Timeout natural (OSError) sin matar violentamente a la placa.
    if time.time() - last_activity < 150:
        try: wdt.feed()
        except: pass

wdt_timer = Timer(0)
wdt_timer.init(period=2000, mode=Timer.PERIODIC, callback=wdt_feeder)

version = "V4.0_NUBE"
global tiempo_estado

# ======================================================================
# 📌 NOTA: DEFINIR EL PIN CORRECTO PARA EL RELÉ DE LA BOMBA AQUÍ
# ======================================================================
bomba_rele = Pin(23, Pin.OUT)
bomba_rele.value(0) # Asegurar bomba apagada al inicio

# Acceso a variables de hardware adicionales
man = global_vars['man']       
led1 = global_vars['led1']     
reloj = global_vars['reloj']   
eeprom = global_vars['eeprom'] 

# Sincronizar reloj interno del ESP32 con el DS3231 para que time.time() sea absoluto
try:
    t = reloj.get_time()
    machine.RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
except: pass

# Variables de estado del sistema
estado_dosificador = "inactivo"  
bomba_encendida_manual = False
bomba_encendida_por_dosis = False
tiempo_inicio_espera = 0
tiempo_inicio_dosis = 0
cronograma_modificado = True
timestamp_bomba_off = 0          
tiempo_estado = 0
ultima_verificacion = 0
ultimo_minuto_disparado = ""
mensaje_temporal = ""
tiempo_mensaje = 0
duracion_mensaje = 3
historial_modificado = False
redes_wifi = []

# ======================================================================
# 🌐 ID DE EQUIPO Y GESTIÓN WI-FI
# ======================================================================
id_equipo = "DOSIMAT_" + binascii.hexlify(machine.unique_id()).decode('utf-8').upper()
print("====================================")
print("ID EQUIPO:", id_equipo)
print("====================================")

wifi_conectado = False
FIREBASE_URL = "https://firestore.googleapis.com/v1/projects/dosimat-iot/databases/(default)/documents/equipos/" + id_equipo + "?key=AIzaSyCkkrfiHOcMG1_djAxg1G3ZzrD7F8SwcOY"

ssid_configurado = ""

def conectar_wifi():
    global wifi_conectado, ssid_configurado
    try:
        with open("wifi_config.json", "r") as f:
            cred = json.load(f)
    except Exception as e:
        print("Sin credenciales Wi-Fi (wifi_config.json no encontrado)")
        return False
    
    ssid = cred.get("ssid", "")
    password = cred.get("pass", "")
    if not ssid: return False
    
    ssid_configurado = ssid
    
    print(f"Conectando a Wi-Fi: {ssid}...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    
    start = time.time()
    while not wlan.isconnected() and time.time() - start < 15:
        global last_activity
        last_activity = time.time()
        time.sleep(0.5)
        
    wifi_conectado = wlan.isconnected()
    return wifi_conectado

if conectar_wifi():
    print("Red conectada. Arrancando en MODO NUBE (BLE apagado).")
    ble_server = None
else:
    print("Sin red. Arrancando en MODO BLE OFFLINE.")
    from ble_service import BLEService
    ble_server = BLEService(name="DosimatBLE")


# Inicialización de EEPROM con el nuevo mapa de variables simplificado
defaults = {
    'Fverano': '1030',
    'Finvierno': '0330',
    'Refuerzo': '0',
    'DosisNo': '0',             
    'Dosis': '30',              
    'DosisMin': '01',           
    'Espera': '30',             
    'EsperaMin': '01',          
    'Cronograma': '[]',         
    'TestigoFalla': '0'         
}

# Carga inicial de variables desde EEPROM 
try:
    Fverano = eeprom.read(0, 4).decode('utf-8')
    Finvierno = eeprom.read(5, 4).decode('utf-8')
    Refuerzo = int(eeprom.read(13, 1))
    DosisNo = int(eeprom.read(32, 1))
    DosisMin = int(eeprom.read(9, 2).decode('ascii').strip('\x00'))
    Dosis = int(eeprom.read(11, 2).decode('ascii').strip('\x00'))
    EsperaMin = int(eeprom.read(27, 2).decode('ascii').strip('\x00'))
    Espera = int(eeprom.read(30, 2).decode('ascii').strip('\x00'))
except Exception as e:
    print("Error leyendo variables fijas, aplicando defaults de emergencia", e)
    Fverano, Finvierno, Refuerzo, DosisNo = '1030', '0330', 0, 0
    DosisMin, Dosis, EsperaMin, Espera = 1, 30, 1, 30
    try:
        eeprom.write(0, b'1030')
        eeprom.write(5, b'0330')
        eeprom.write(13, b'0')
        eeprom.write(32, b'0')
        eeprom.write(9, b'01')
        eeprom.write(11, b'30')
        eeprom.write(27, b'01')
        eeprom.write(30, b'30')
        print("EEPROM grabada con éxito de fábrica.")
    except Exception as error_escr:
        print("Error crítico al escribir la configuración de fábrica:", error_escr)

# ======================================================================
# 💾 FUNCIONES SEGURAS DE EEPROM
# ======================================================================
def guardar_en_eeprom(direccion, obj, tamaño_maximo):
    try:
        json_str = json.dumps(obj).encode('utf-8')
        # Solo agregar un byte nulo al final para indicar el fin, evitar escritura masiva que corrompa la EEPROM
        json_str += b'\x00'
        # Podríamos limitar a tamaño_maximo si fuera necesario, pero json_str suele ser pequeño
        if len(json_str) > tamaño_maximo:
            json_str = json_str[:tamaño_maximo-1] + b'\x00'
        eeprom.write(direccion, json_str)
    except Exception as e:
        print(f"Error guardando en EEPROM dir {direccion}:", e)

def cargar_de_eeprom(direccion, tamaño_maximo):
    try:
        raw_bytes = eeprom.read(direccion, tamaño_maximo)
        raw_bytes = raw_bytes.replace(b'\xff', b'\x00')
        fin = raw_bytes.find(b'\x00')
        if fin != -1:
            raw_bytes = raw_bytes[:fin]
        if not raw_bytes:
            return []
        raw_str = raw_bytes.decode('utf-8', 'ignore').strip()
        if raw_str.startswith('[') or raw_str.startswith('{'):
            return json.loads(raw_str)
    except Exception as e:
        print(f"Error leyendo EEPROM dir {direccion}:", e)
    return []

cronograma = cargar_de_eeprom(100, 500)
if not cronograma:
    cronograma = [{"on": "2100", "duracion": 60, "dosis": 1, "dias": "0123456"}]

def cargar_historial():
    global historial_dosis
    try:
        raw = eeprom.read(600, 1000)
        data_str = raw.decode('utf-8', 'ignore').strip('\x00\xff').strip()
        if data_str:
            historial_dosis = json.loads(data_str)
            if not isinstance(historial_dosis, list): historial_dosis = []
        else:
            historial_dosis = []
    except Exception as e:
        print("Error leyendo EEPROM dir 600, auto-corrigiendo:", e)
        historial_dosis = []
        try:
            eeprom.write(600, b'[]\x00')
        except: pass
    return historial_dosis

# Chequeo de corte de luz y Dosis Perdidas en el arranque
try:
    raw_testigo = eeprom.read(39, 1)
    if raw_testigo == b'\xff': 
        eeprom.write(39, b'0')
        testigo_falla = '0'
    else:
        testigo_falla = raw_testigo.decode('utf-8')

    historial = cargar_historial()
    t = reloj.get_time()
    
    # 1. Registrar Inicio/Reinicio
    if testigo_falla == '1':
        print("DETECTADO: El equipo se reinició o sufrió un corte de luz durante una dosificación.")
        evento_str = "Reinicio/Corte"
    else:
        evento_str = "Inicio Sistema"
        
    registro_inicio = {
        "fecha": f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}",
        "segundos": 0,
        "temp": evento_str,
        "ref": 0,
        "tipo": "Sistema"
    }
    historial.insert(0, registro_inicio)

    # 2. Detección de Dosis Perdidas (hoy)
    hoy_str = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
    dosis_hoy = [h for h in historial if h["fecha"].startswith(hoy_str) and h.get("tipo", "") == "Programada"]
    horas_dosis_hoy = [int(h["fecha"][11:13])*60 + int(h["fecha"][14:16]) for h in dosis_hoy]
    hora_actual_m = t[3]*60 + t[4]
    
    for ev in cronograma:
        if ev.get("dosis") == 1:
            ev_m = int(ev["on"][:2])*60 + int(ev["on"][2:])
            if ev_m < hora_actual_m:
                encontrada = False
                for h_m in horas_dosis_hoy:
                    if abs(h_m - ev_m) < 60:
                        encontrada = True
                        break
                if not encontrada:
                    registro_perdida = {
                        "fecha": f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {ev['on'][:2]}:{ev['on'][2:]}",
                        "segundos": 0,
                        "temp": "Perdida (Apagado)",
                        "ref": 0,
                        "tipo": "Perdida"
                    }
                    historial.insert(0, registro_perdida)
                    
    historial = historial[:10]
    guardar_en_eeprom(600, historial, 1000)
    eeprom.write(39, b'0')
except Exception as e:
    print("Error en chequeo inicial:", e)
    try: eeprom.write(39, b'0')
    except: pass

# ======================================================================
# 🔥 RECUPERACIÓN ANTE CUELGUES DE RED (STATE RECOVERY)
# ======================================================================
def guardar_estado_recuperacion():
    if estado_dosificador == "inactivo":
        datos = {"e": "inactivo"}
    else:
        ti = tiempo_inicio_espera if "esperando" in estado_dosificador else tiempo_inicio_dosis
        datos = {"e": estado_dosificador, "tb": timestamp_bomba_off, "ti": ti, "bpd": bomba_encendida_por_dosis}
    guardar_en_eeprom(500, datos, 90)

def cargar_estado_recuperacion():
    global estado_dosificador, timestamp_bomba_off, tiempo_inicio_espera, tiempo_inicio_dosis, bomba_encendida_por_dosis
    try:
        datos = cargar_de_eeprom(500, 90)
        if isinstance(datos, dict):
            e = datos.get("e", "inactivo")
            if e != "inactivo":
                ahora = time.time()
                tb = datos.get("tb", ahora)
                ti = datos.get("ti", ahora)
                bpd = datos.get("bpd", False)
                
                if e == "solo_bomba" and ahora >= tb: return
                if e in ["dosificando", "manual"] and ahora - ti >= calcular_dosis_total(): return
                if e in ["esperando_dosis", "esperando_manual"] and ahora - ti >= (EsperaMin * 60 + Espera):
                    ti = ahora 
                    e = "dosificando" if e == "esperando_dosis" else "manual"
                
                print("🔥 RECUPERANDO ESTADO TRAS REINICIO (Protección Anti-Cuelgue):", e)
                estado_dosificador = e
                timestamp_bomba_off = tb
                bomba_encendida_por_dosis = bpd
                
                if e == "solo_bomba":
                    bomba_rele.value(1)
                elif e in ["esperando_dosis", "esperando_manual"]:
                    tiempo_inicio_espera = ti
                    bomba_rele.value(1)
                elif e in ["dosificando", "manual"]:
                    tiempo_inicio_dosis = ti
                    man.value(1)
                    if e == "manual" and bpd: bomba_rele.value(1)
                    if e == "dosificando": bomba_rele.value(1)
    except: pass

cargar_estado_recuperacion()

# Configuración de patrones LED
LED_PATRONES = {
    'inactivo':             [(1, 200), (0, 5000)],
    'inactivo_refuerzo':    [(1, 200), (0, 200), (1, 200), (0, 5000)],
    'dosificando':          [(1, 1000), (0, 1000)],
    'dosificando_refuerzo': [(1, 5000), (0, 200)],
    'solo_bomba':           [(1, 500), (0, 500)],
    'solo_bomba_refuerzo':  [(1, 200), (0, 200), (1, 200), (0, 500)],
    'esperando_manual':     [(1, 1000), (0, 200)]
}

estado_led_actual = {
    'patron': LED_PATRONES['inactivo'],
    'indice': 0,
    'ultimo_cambio': time.ticks_ms()
}

def actualizar_led(t=None):
    global estado_led_actual, Refuerzo, estado_dosificador
    
    patron_sel = 'inactivo'
    if estado_dosificador == "dosificando" or estado_dosificador == "manual":
        patron_sel = 'dosificando_refuerzo' if Refuerzo == 1 else 'dosificando'
    elif estado_dosificador == "solo_bomba":
        patron_sel = 'solo_bomba_refuerzo' if Refuerzo == 1 else 'solo_bomba'
    elif estado_dosificador == "esperando_manual":
        patron_sel = 'esperando_manual'
    else:
        patron_sel = 'inactivo_refuerzo' if Refuerzo == 1 else 'inactivo'
        
    patron_esperado = LED_PATRONES[patron_sel]
        
    if estado_led_actual['patron'] != patron_esperado:
        estado_led_actual['patron'] = patron_esperado
        estado_led_actual['indice'] = 0
        estado_led_actual['ultimo_cambio'] = time.ticks_ms()
        ref.value(patron_esperado[0][0])
    
    ahora = time.ticks_ms()
    paso_actual = estado_led_actual['patron'][estado_led_actual['indice']]
    
    if time.ticks_diff(ahora, estado_led_actual['ultimo_cambio']) >= paso_actual[1]:
        estado_led_actual['indice'] = (estado_led_actual['indice'] + 1) % len(estado_led_actual['patron'])
        estado_led_actual['ultimo_cambio'] = ahora
        siguiente_paso = estado_led_actual['patron'][estado_led_actual['indice']]
        ref.value(siguiente_paso[0])

timer_led = Timer(1)
timer_led.init(period=50, mode=Timer.PERIODIC, callback=actualizar_led)

# ======================================================================
# ⏳ FUNCIONES DE TIEMPO Y CÁLCULOS
# ======================================================================
def esta_en_temporada_verano():
    t = reloj.get_time()
    fecha_actual = f"{t[1]:02d}{t[2]:02d}" 
    if Finvierno < Fverano: en_invierno = Finvierno <= fecha_actual < Fverano
    else: en_invierno = fecha_actual >= Finvierno or fecha_actual < Fverano
    return not en_invierno

def calcular_dosis_total():
    m_ver = 2 if esta_en_temporada_verano() else 1
    m_ref = 2 if Refuerzo == 1 else 1
    return (DosisMin * 60 + Dosis) * m_ver * m_ref

def calcular_delta_minutos(hora_desde, hora_hasta):
    min_desde = int(hora_desde[:2]) * 60 + int(hora_desde[2:])
    min_hasta = int(hora_hasta[:2]) * 60 + int(hora_hasta[2:])
    if min_hasta < min_desde: min_hasta += 1440 
    return min_hasta - min_desde

def registrar_dosificacion_exitosa(duracion_aplicada, tipo="Programada"):
    global historial_modificado
    try:
        historial = cargar_historial()
        t = reloj.get_time()
        nuevo_registro = {
            "fecha": f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}",
            "segundos": duracion_aplicada,
            "temp": "Verano" if esta_en_temporada_verano() else "Mantenimiento",
            "ref": Refuerzo,
            "tipo": tipo
        }
        historial.insert(0, nuevo_registro)
        historial = historial[:10]
        guardar_en_eeprom(600, historial, 1000)
        historial_modificado = True
        print("Historial actualizado en EEPROM.")
    except Exception as e:
        print("Error al escribir historial:", e)

# ======================================================================
# ⚙️ MÁQUINA DE ESTADOS - CONTROL PROACTIVO
# ======================================================================
def verificar_dosificacion():
    global estado_dosificador, evento_activo_index, timestamp_bomba_off
    global tiempo_inicio_espera, tiempo_inicio_dosis, ultima_verificacion
    global DosisNo, Refuerzo, man, bomba_rele
    global tiempo_estado, ultimo_minuto_disparado
    global bomba_encendida_manual, bomba_encendida_por_dosis
    
    if estado_dosificador in ["inactivo", "solo_bomba"]:
        tiempo_estado = 0

    ahora = time.time()
    if ahora - ultima_verificacion < 1.0: return
    ultima_verificacion = ahora

    t_rtc = reloj.get_time()
    hora_actual_str = f"{t_rtc[3]:02d}{t_rtc[4]:02d}"
    minuto_actual_str = f"{t_rtc[2]:02d}-{t_rtc[3]:02d}:{t_rtc[4]:02d}" 
    dia_actual_str = str(t_rtc[6])

    if estado_dosificador == "inactivo":
        for idx, evento in enumerate(cronograma):
            dias_permitidos = str(evento.get("dias", "0123456"))
            if evento.get("on") == hora_actual_str and ultimo_minuto_disparado != minuto_actual_str and dia_actual_str in dias_permitidos:
                dur_min = int(evento.get("duracion", 0))
                if dur_min <= 0: continue 
                
                ultimo_minuto_disparado = minuto_actual_str
                evento_activo_index = idx
                bomba_rele.value(1) 
                print(f"Cronograma: Iniciando evento {idx}. Bomba ON.")
                timestamp_bomba_off = ahora + (dur_min * 60)
                
                if evento.get("dosis") == 1:
                    if DosisNo == 0:
                        t_req = (EsperaMin * 60 + Espera) + calcular_dosis_total()
                        if (dur_min * 60) < t_req:
                            timestamp_bomba_off = ahora + t_req
                        estado_dosificador = "esperando_dosis"
                        tiempo_inicio_espera = ahora
                    else:
                        DosisNo -= 1
                        eeprom.write(32, str(DosisNo).encode())
                        estado_dosificador = "solo_bomba"
                        print(f"Dosis Anulada. Restantes: {DosisNo}")
                else:
                    estado_dosificador = "solo_bomba"
                break

    elif estado_dosificador == "solo_bomba":
        if not bomba_encendida_manual and ahora >= timestamp_bomba_off:
            bomba_rele.value(0)
            estado_dosificador = "inactivo"
            evento_activo_index = -1
            print("Cronograma: Fin de evento (Solo Bomba). Bomba OFF.")

    elif estado_dosificador == "esperando_dosis":
        tiempo_estado = int(ahora - tiempo_inicio_espera) 
        if ahora - tiempo_inicio_espera >= (EsperaMin * 60 + Espera):
            estado_dosificador = "dosificando"
            tiempo_inicio_dosis = ahora
            tiempo_estado = 0
            eeprom.write(39, b'1') 
            man.value(1) 
            print("Secuencia: Tiempo de espera concluido. Válvula ABIERTA.")

    elif estado_dosificador == "esperando_manual":
        tiempo_estado = int(ahora - tiempo_inicio_espera)
        if ahora - tiempo_inicio_espera >= (EsperaMin * 60 + Espera):
            estado_dosificador = "manual"
            tiempo_inicio_dosis = ahora
            tiempo_estado = 0
            man.value(1)
            print("Manual: Tiempo de espera concluido. Válvula ABIERTA.")

    elif estado_dosificador == "dosificando":
        tiempo_estado = int(ahora - tiempo_inicio_dosis) 
        dosis_total = calcular_dosis_total()
        if ahora - tiempo_inicio_dosis >= dosis_total:
            man.value(0) 
            print("Secuencia: Dosis completada. Válvula CERRADA.")
            eeprom.write(39, b'0') 
            registrar_dosificacion_exitosa(dosis_total)
            
            if ahora < timestamp_bomba_off: estado_dosificador = "solo_bomba"
            else:
                bomba_rele.value(0)
                estado_dosificador = "inactivo"
                evento_activo_index = -1
                print("Cronograma: Fin de evento inmediato tras dosis. Bomba OFF.")
                
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo = 0

    elif estado_dosificador == "manual":
        dosis_total = calcular_dosis_total()
        if ahora - tiempo_inicio_dosis >= dosis_total:
            man.value(0)
            estado_dosificador = "solo_bomba"
            if bomba_encendida_por_dosis:
                timestamp_bomba_off = ahora + 1800 # 30 min extra
                print("Manual: Fin de dosificación. Bomba continuará 30 min.")
            else:
                timestamp_bomba_off = ahora
                print("Manual: Fin de dosificación. Bomba continúa su ciclo anterior.")
                
            registrar_dosificacion_exitosa(dosis_total, tipo="Manual")
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo = 0

# ======================================================================
# 🔵 PROCESADOR DE COMANDOS (NUBE + BLE)
# ======================================================================
def procesar_comando(data):
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, Espera, EsperaMin
    global estado_dosificador, tiempo_inicio_dosis, tiempo_inicio_espera, Dosis, DosisNo, DosisMin
    global Finvierno, Fverano, cronograma, timestamp_bomba_off, redes_wifi
    global bomba_encendida_manual, bomba_encendida_por_dosis, cronograma_modificado, historial_modificado
    try:
        comando = data.get("comando", "")
        
        if comando == "escanear_wifi":
            try:
                wlan = network.WLAN(network.STA_IF)
                wlan.active(True)
                redes = wlan.scan()
                redes_wifi = [r[0].decode('utf-8') for r in redes if r[0]]
                mensaje_temporal = "Escaneo completado"
                tiempo_mensaje = time.time()
                print("Redes encontradas:", redes_wifi)
            except Exception as e:
                mensaje_temporal = "Error escaneando"
                tiempo_mensaje = time.time()
                
        elif comando == "config_wifi":
            ssid = data.get("ssid", "")
            pwd = data.get("pass", data.get("pwd", ""))
            with open("wifi_config.json", "w") as f:
                json.dump({"ssid": ssid, "pass": pwd}, f)
            print("Wi-Fi guardado. Reiniciando equipo...")
            time.sleep(1)
            machine.reset()
            
        elif comando == "bombasi":
            estado_dosificador = "solo_bomba"
            bomba_rele.value(1)
            bomba_encendida_manual = True
            timestamp_bomba_off = time.time() + 31536000 # 1 año, evita error inf a int
            mensaje_temporal = "Bomba Encendida"
            tiempo_mensaje = time.time()
            print("Bomba encendida manualmente.")
        elif comando == "bombano":
            bomba_encendida_manual = False
            estado_dosificador = "inactivo"
            bomba_rele.value(0)
            tiempo_mensaje = time.time()
            print("Bomba apagada manualmente.")
            mensaje_temporal = "Bomba apagada"
        elif comando == "manualsi":
            if estado_dosificador == "inactivo":
                tiempo_inicio_espera = time.time()
                estado_dosificador = "esperando_manual"
                bomba_rele.value(1)
                bomba_encendida_por_dosis = True
            elif estado_dosificador == "solo_bomba":
                estado_dosificador = "manual"
                tiempo_inicio_dosis = time.time()
                bomba_encendida_por_dosis = False
                man.value(1)
            tiempo_mensaje = time.time()
            print("Iniciando dosificación manual/espera.")
            mensaje_temporal = "Dosis manual iniciada"
        elif comando in ("manualno", "cancelar_dosis"):
            man.value(0)
            bomba_rele.value(0)
            estado_dosificador = "inactivo"
            mensaje_temporal = "Cancelado"
            tiempo_mensaje = time.time()
            print("Cancelación manual. Todo apagado.")
        elif comando == "refuerzosi":
            Refuerzo = 1
            eeprom.write(13, b"1")
            mensaje_temporal = "Refuerzo Activado"
            tiempo_mensaje = time.time()
        elif comando == "refuerzono":
            Refuerzo = 0
            eeprom.write(13, b"0")
            mensaje_temporal = "Refuerzo Desactivado"
            tiempo_mensaje = time.time()
        elif comando == "config_general":
            if "Dosis" in data:
                Dosis = max(0, min(int(data.get("Dosis", Dosis)), 59))
                DosisMin = max(0, min(int(data.get("DosisMin", DosisMin)), 15))
                eeprom.write(11, f"{Dosis:02d}".encode('ascii'))
                eeprom.write(9, f"{DosisMin:02d}".encode('ascii'))
            if "Espera" in data:
                Espera = max(0, min(int(data.get("Espera", Espera)), 59))
                EsperaMin = max(0, min(int(data.get("EsperaMin", EsperaMin)), 30))
                eeprom.write(30, f"{Espera:02d}".encode('ascii'))
                eeprom.write(27, f"{EsperaMin:02d}".encode('ascii'))
            if "Fverano" in data:
                Fverano = data.get("Fverano", Fverano)
                Finvierno = data.get("Finvierno", Finvierno)
                eeprom.write(0, Fverano.encode())
                eeprom.write(5, Finvierno.encode())
            mensaje_temporal = "Config guardada"
            tiempo_mensaje = time.time()
            cronograma_modificado = True

        elif comando == "config_anular":
            DosisNo = int(data.get("DosisNo", DosisNo))
            if 0 <= DosisNo <= 9: eeprom.write(32, str(DosisNo).encode())
            mensaje_temporal = "Anulación guardada"
            tiempo_mensaje = time.time()
        elif comando == "config_cronograma":
            nuevos_horarios = data.get("cronograma", [])
            if isinstance(nuevos_horarios, list) and len(nuevos_horarios) <= 10:
                cronograma = nuevos_horarios
                guardar_en_eeprom(100, cronograma, 500)
                print("Nuevo cronograma guardado con éxito.")
                mensaje_temporal = "Cronograma guardado"
                tiempo_mensaje = time.time()
                cronograma_modificado = True
        elif comando == "borrar_historial":
            historial_dosis = []
            guardar_en_eeprom(600, historial_dosis, 1000)
            historial_modificado = True
            print("Historial borrado.")
            mensaje_temporal = "Historial borrado"

        elif comando == "reset_fabrica":
            try: os.remove("wifi_config.json")
            except: pass
            
            # Borrar EEPROM: llenamos con ceros las variables vitales y el cronograma para forzar defaults
            try: eeprom.write(0, b'\x00' * 32)
            except: pass
            try: eeprom.write(100, b'\x00' * 32)
            except: pass
            
            # Limpiamos explícitamente el historial guardando una lista vacía
            historial_dosis = []
            try: guardar_en_eeprom(600, historial_dosis, 1000)
            except: pass
            
            print("Reseteo de fabrica completado. Reiniciando...")
            machine.reset()
            tiempo_mensaje = time.time()
            print("Historial borrado.")
        elif comando == "sync_rtc":
            fecha = data.get("fecha", "")  
            hora = data.get("hora", "")    
            if fecha and hora:
                year = int(fecha[:4]); month = int(fecha[5:7]); day = int(fecha[8:10])
                hour = int(hora[:2]); minute = int(hora[3:5])
                m_t = month; y_t = year
                if m_t < 3: m_t += 12; y_t -= 1
                q = day; K = y_t % 100; J = y_t // 100
                weekday = (((q + 13*(m_t + 1)//5 + K + K//4 + J//4 + 5*J) % 7) + 5) % 7
                reloj.set_time((year, month, day, hour, minute, 0, weekday, 0))
                mensaje_temporal = "Reloj sincronizado"
                tiempo_mensaje = time.time()
                print("RTC Sincronizado.")
    except Exception as e:
        print("Error procesando comando:", e)

def procesar_comando_ble(datos_str):
    try: procesar_comando(json.loads(datos_str))
    except: pass

if ble_server:
    ble_server.on_write(procesar_comando_ble)

# ======================================================================
# ☁️ FUNCIONES NUBE (FIRESTORE API REST)
# ======================================================================
def to_firestore(v):
    if isinstance(v, str): return {"stringValue": v}
    elif isinstance(v, bool): return {"booleanValue": v}
    elif isinstance(v, int): return {"integerValue": str(v)}
    elif isinstance(v, float): return {"doubleValue": v}
    elif isinstance(v, list): return {"arrayValue": {"values": [to_firestore(x) for x in v]}}
    elif isinstance(v, dict): return {"mapValue": {"fields": {k: to_firestore(x) for k, x in v.items()}}}
    else: return {"nullValue": None}

ultima_telemetria_enviada = {}
ultimo_intento_telemetria = 0
cola_telemetria = {}

def procesar_telemetria_pendiente():
    global cola_telemetria, ultima_telemetria_enviada, ultimo_intento_telemetria
    if not cola_telemetria: return
    
    # IMPORTANTE: Firestore tiene un límite de 1 escritura por segundo por documento.
    # Espaciamos los envíos de los chunks (y después de borrar comandos) al menos 2 segundos.
    if time.time() - ultimo_intento_telemetria < 2.0:
        return
        
    ultimo_intento_telemetria = time.time()
    keys = list(cola_telemetria.keys()) # Enviamos TODO de un solo golpe para evitar retrasos
    payload = {"fields": {k: to_firestore(cola_telemetria[k]) for k in keys}}
    url = FIREBASE_URL
    for k in keys:
        url += f"&updateMask.fieldPaths={k}"
    
    # Restringir la respuesta al minimo posible para evitar desbordar el buffer SSL del ESP32 (error -116)
    url += "&mask.fieldPaths=estado"
    
    res = None
    try:
        gc.collect()
        res = urequests.patch(url, json=payload)
        if res.status_code == 200:
            for k in keys:
                ultima_telemetria_enviada[k] = cola_telemetria[k]
        else:
            print("Error HTTP Patch:", res.status_code, res.text)
    except Exception as e:
        print("Error Patch Exception:", e)
    finally:
        if res:
            try: res.close()
            except: pass
        # Siempre borramos las llaves procesadas para no atascar la cola
        for k in keys:
            if k in cola_telemetria: del cola_telemetria[k]
        if res:
            try: res.close()
            except: pass

def encolar_telemetria(datos):
    global cola_telemetria, ultima_telemetria_enviada
    for k, v in datos.items():
        if k not in ultima_telemetria_enviada or ultima_telemetria_enviada[k] != v:
            cola_telemetria[k] = v

def obtener_fecha_hora_rtc():
    t = reloj.get_time()
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

def registrar_evento_nube(coleccion, datos):
    if not wifi_conectado: return
    try:
        url = f"https://firestore.googleapis.com/v1/projects/dosimat-iot/databases/(default)/documents/equipos/{id_equipo}/{coleccion}?key=AIzaSyCkkrfiHOcMG1_djAxg1G3ZzrD7F8SwcOY"
        if "fecha" not in datos: datos["fecha"] = obtener_fecha_hora_rtc()
        payload = {"fields": {k: to_firestore(v) for k, v in datos.items()}}
        gc.collect()
        res = urequests.post(url, json=payload)
        if res:
            try: res.close()
            except: pass
    except: pass

def leer_comandos_firestore():
    global ultimo_intento_telemetria
    try:
        print("[Nube] Consultando comandos pendientes...")
        res = urequests.get(FIREBASE_URL + "&mask.fieldPaths=comando_pendiente")
        print("[Nube] Consulta completada.")
        
        # Si llegamos aquí sin que se cuelgue, reseteamos el Fail-Safe
        try: os.remove("crash_count.txt")
        except: pass
        
        json_str = None
        try:
            if res.status_code == 200:
                data = res.json()
                if "fields" in data and "comando_pendiente" in data["fields"]:
                    val = data["fields"]["comando_pendiente"]
                    if "stringValue" in val:
                        json_str = val["stringValue"]
        finally:
            res.close()
            
        if json_str:
            print("Nube: Comando recibido:", json_str)
            # Encolar el borrado del comando directamente para saltar la validación de duplicados
            print("[Nube] Encolando borrado de comando...")
            cola_telemetria["comando_pendiente"] = ""
            
            try:
                cmd_dict = json.loads(json_str)
                procesar_comando(cmd_dict)
                return True
            except Exception as ex:
                print("Error parseando comando nube:", ex)
                
    except Exception as e:
        pass
    return False


# ======================================================================
# 🔄 BUCLE PRINCIPAL (MAIN LOOP)
# ======================================================================
def run_main_loop():
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, estado_dosificador
    global Espera, EsperaMin, Dosis, DosisMin, DosisNo, cronograma_modificado, tiempo_estado
    
    gc.collect()
    print("Servidor Iniciado:", version)

    ultimo_envio_telemetria = 0 # Forzamos envio inmediato
    ultima_consulta_comandos = 0
    ultimo_cambio_dia = 0
    estado_anterior = "" # Para forzar envío en Nube si cambia el estado
    estado_anterior_recuperacion = estado_dosificador
    
    registrar_evento_nube("sys_log", {"evento": "Sistema Iniciado", "version": version})

    while True:
        try:
            ahora = time.time()
            global last_activity
            last_activity = ahora
            verificar_dosificacion()
            
            if estado_dosificador != estado_anterior_recuperacion:
                guardar_estado_recuperacion()
                registrar_evento_nube("sys_log", {"evento": f"Cambio de estado: {estado_dosificador}"})
                estado_anterior_recuperacion = estado_dosificador
            
            if mensaje_temporal and (ahora - tiempo_mensaje > duracion_mensaje): mensaje_temporal = ""
                
            t_rtc = reloj.get_time()
            if t_rtc[3] == 0 and t_rtc[4] == 0 and ultimo_cambio_dia != t_rtc[2]:
                ultimo_cambio_dia = t_rtc[2]
            
            # Recalcular tiempo_estado antes de enviarlo por si cambió internamente
            if estado_dosificador in ("esperando_dosis", "esperando_manual"):
                tiempo_estado = int(ahora - tiempo_inicio_espera)
            elif estado_dosificador in ("dosificando", "manual"):
                tiempo_estado = int(ahora - tiempo_inicio_dosis)
            elif estado_dosificador == "solo_bomba":
                tiempo_estado = 0
            elif estado_dosificador == "inactivo":
                tiempo_estado = int(ahora - timestamp_bomba_off) if not bomba_encendida_manual else 0
            
            # --- TAREAS DE RED (BLE o WI-FI) ---
            if wifi_conectado:
                comando_procesado = False
                # 1. Chequear comandos pendientes cada 10 seg
                if ahora - ultima_consulta_comandos >= 10.0:
                    if leer_comandos_firestore():
                        comando_procesado = True
                    ultima_consulta_comandos = ahora
                
                # 2. Enviar telemetría si pasaron 900 seg (o 10 seg si está activo), si cambió el estado, o si procesamos una orden
                if (ahora - ultimo_envio_telemetria >= 900.0) or (estado_dosificador != estado_anterior) or comando_procesado:
                    estado_anterior = estado_dosificador
                    fecha_str = f"{t_rtc[0]:04d}-{t_rtc[1]:02d}-{t_rtc[2]:02d}"
                    hora_str = f"{t_rtc[3]:02d}:{t_rtc[4]:02d}:{t_rtc[5]:02d}"
                    t_bomba_off_seg = max(0, int(timestamp_bomba_off - ahora)) if estado_dosificador == "solo_bomba" else 0
                    
                    telemetria = {
                        "id_equipo": id_equipo,
                        "version": version,
                        "estado": estado_dosificador,
                        "t_estado": tiempo_estado,
                        "t_bomba_off_seg": t_bomba_off_seg,
                        "mensaje": mensaje_temporal,
                        "bomba": bomba_rele.value() == 1, 
                        "temporada": "Verano" if esta_en_temporada_verano() else "Mantenimiento",
                        "Refuerzo": Refuerzo == 1,
                        "DosisNo": DosisNo,
                        "Dosis": Dosis,
                        "DosisMin": DosisMin,
                        "Espera": Espera,
                        "EsperaMin": EsperaMin,
                        "Fverano": Fverano,
                        "Finvierno": Finvierno,
                        "wifi_ssid": ssid_configurado,
                        "rtc_fecha": fecha_str,
                        "rtc_hora": hora_str,
                        "dosis_total_seg": calcular_dosis_total(),
                        "temp_rtc": reloj.temperature()
                    }
                    if redes_wifi: telemetria["redes_wifi"] = redes_wifi
                    if cronograma_modificado or historial_modificado or ultimo_envio_telemetria == 0:
                        telemetria["cronograma"] = cronograma
                        telemetria["historial"] = cargar_historial()
                        cronograma_modificado = False
                        historial_modificado = False
                    
                    encolar_telemetria(telemetria)
                    gc.collect()
                    ultimo_envio_telemetria = ahora
                
                procesar_telemetria_pendiente()
                    
            else:
                # Modo BLE Clásico: Enviar telemetría cada 2 seg a todos los clientes conectados
                if ahora - ultimo_envio_telemetria >= 2.0:
                    fecha_str = f"{t_rtc[0]:04d}-{t_rtc[1]:02d}-{t_rtc[2]:02d}"
                    hora_str = f"{t_rtc[3]:02d}:{t_rtc[4]:02d}:{t_rtc[5]:02d}"
                    telemetria = {
                        "id_equipo": id_equipo,
                        "version": version,
                        "estado": estado_dosificador,
                        "t_estado": tiempo_estado,
                        "mensaje": mensaje_temporal,
                        "bomba": bomba_rele.value(), 
                        "temporada": "Verano" if esta_en_temporada_verano() else "Mantenimiento",
                        "Refuerzo": Refuerzo,
                        "DosisNo": DosisNo,
                        "Dosis": Dosis,
                        "DosisMin": DosisMin,
                        "Espera": Espera,
                        "EsperaMin": EsperaMin,
                        "Fverano": Fverano,
                        "Finvierno": Finvierno,
                        "wifi_ssid": ssid_configurado,
                        "dosis_total_seg": calcular_dosis_total(),
                        "cronograma": cronograma,
                        "redes_wifi": redes_wifi,
                        "historial": cargar_historial(),
                        "rtc_fecha": fecha_str,
                        "rtc_hora": hora_str,
                        "dosis_total_seg": calcular_dosis_total(),
                        "temp_rtc": reloj.temperature()
                    }
                    if ble_server:
                        ble_server.send_json(telemetria)
                    gc.collect()
                    ultimo_envio_telemetria = ahora
                    
        except Exception as e:
            print(f"Error crítico en run_main_loop: {e}")
        
        time.sleep(0.1)

run_main_loop()
