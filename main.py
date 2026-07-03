#ESTE ES EL CORREGIDO POR COPILOT
import machine
import time
import os
import gc
import json
import network
import ntptime
import binascii
from boot import global_vars
import uasyncio as asyncio
# ======================================================================
# COLAS COMPATIBLES CON MICROPYTHON (SIN uasyncio.Queue)
# ======================================================================




# ======================================================================
# INICIALIZACIÓN DE HARDWARE Y VARIABLES GLOBALES
# ======================================================================
man = global_vars['man']
bomba_rele = global_vars['bomba_rele']
led1 = global_vars['led1']
ref = global_vars['ref']
reloj = global_vars['reloj']
eeprom = global_vars['eeprom']
telemetria_en_progreso = False
telemetria_pendiente = False
# Variables globales
wifi_conectado = False
cronograma_modificado = False
historial_pedido = False

# Locks para protección de recursos compartidos
flash_lock = asyncio.Lock()
i2c_lock = asyncio.Lock()

async def get_time_async():
    async with i2c_lock:
        try:
            return reloj.get_time()
        except Exception as e:
            print("Error I2C al leer RTC:", e)
            return (2000, 1, 1, 0, 0, 0, 0)

async def set_time_async(t):
    async with i2c_lock:
        try:
            reloj.set_time(t)
        except Exception as e:
            print("Error I2C al guardar RTC:", e)


version = "V4.0_ASYNC"
id_equipo = "DOSIMAT_" + binascii.hexlify(machine.unique_id()).decode('utf-8').upper()
FIREBASE_BASE_URL = f"https://dosimat-iot-default-rtdb.firebaseio.com/equipos/{id_equipo}"

# Variables de Estado
estado_dosificador = "inactivo"
bomba_encendida_manual = False
bomba_encendida_por_dosis = False
tiempo_inicio_espera = 0
tiempo_inicio_dosis = 0
timestamp_bomba_off = 0
tiempo_estado = 0
ultimo_minuto_disparado = ""
mensaje_temporal = ""
tiempo_mensaje = 0
duracion_mensaje = 3
redes_wifi = []
tiempo_inicio_mantenimiento = 0
wifi_conectado = False
ssid_configurado = ""
cronograma_modificado = True
_cron_buffer = None  # Buffer temporal para recepción de cronograma por partes (BLE)

# RAM DB (reemplaza EEPROM)
config_data = {
    'Fverano': '1030',
    'Finvierno': '0330',
    'Refuerzo': 0,
    'DosisNo': 0,
    'Dosis': 30,
    'DosisMin': 1,
    'Espera': 30,
    'EsperaMin': 1,
    'Cronograma': [{"on": "2100", "duracion": 60, "dosis": 1, "dias": "0123456"}],
    'historial_dosis': [],
    'ultimo_timestamp_dosis': 0,
    'PausarProg': 0
}

# ======================================================================
# TAREA A: PERSISTENCIA LOCAL EN FLASH
# ======================================================================
def cargar_configuracion():
    global config_data
    try:
        with open("config_cloro.json", "r") as f:
            datos_leidos = json.load(f)
            config_data.update(datos_leidos)
            print("Configuración cargada desde Flash.")
    except Exception as e:
        print("Archivo de configuración no encontrado o corrupto, creando defaults...", e)
        guardar_configuracion_sync()

def guardar_configuracion_sync():
    try:
        with open("config_cloro.json", "w") as f:
            json.dump(config_data, f)
    except Exception as e:
        print("Error guardando config:", e)

async def guardar_configuracion_async():
    async with flash_lock:
        try:
            with open("config_cloro.tmp", "w") as f:
                json.dump(config_data, f)
            try:
                os.remove("config_cloro.json")
            except OSError:
                pass
            os.rename("config_cloro.tmp", "config_cloro.json")
        except Exception as e:
            print("Error atómico guardando config:", e)


# ======================================================================
# UTILIDADES MQTT — VERSIÓN ROBUSTA
# ======================================================================

try:
    from umqtt.simple import MQTTClient
except ImportError:
    from simple import MQTTClient

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
mqtt_client = None


def mqtt_callback(topic, msg):
    try:
        topic_str = topic.decode('utf-8')
        payload = json.loads(msg.decode('utf-8'))

        if topic_str.endswith("/comandos"):
            cmd = payload.get("comando")
            if cmd and cmd != "ninguna":
                asyncio.create_task(procesar_comando(payload))

    except Exception as e:
        print("MQTT Callback Error:", e)


def conectar_mqtt():
    global mqtt_client

    if not wifi_conectado:
        print("MQTT: Wi-Fi no disponible")
        return False

    # Cerrar cliente previo si existe
    try:
        if mqtt_client:
            mqtt_client.disconnect()
    except:
        pass

    try:
        print(f"Conectando a MQTT Broker: {MQTT_BROKER}...")

        import urandom
        client_id = f"dosimat_{id_equipo}_{urandom.getrandbits(16)}"

        mqtt_client = MQTTClient(
            client_id,
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60
        )

        mqtt_client.set_callback(mqtt_callback)
        mqtt_client.connect(clean_session=True)
        mqtt_client.subscribe(f"dosimat/{id_equipo}/comandos")

        print("MQTT Conectado y Suscrito.")
        return True

    except Exception as e:
        print("Error conectando MQTT:", e)
        mqtt_client = None
        return False


# ======================================================================
# TAREA B: CONECTIVIDAD HÍBRIDA INTELIGENTE (WI-FI + BLE) — VERSIÓN ROBUSTA
# ======================================================================

# ======================================================================
# CONTROL DE BLE PARA EVITAR CONFLICTO CON WIFI
# ======================================================================

def ble_stop_advertising():
    try:
        import bluetooth
        bt = bluetooth.BLE()
        bt.active(False)
        print("BLE: Advertising detenido temporalmente.")
    except Exception as e:
        print("BLE stop error:", e)


def ble_start_advertising():
    try:
        import bluetooth
        bt = bluetooth.BLE()
        bt.active(True)
        # Tu advertising original:
        nombre = id_equipo
        bt.gap_advertise(100, b'\x02\x01\x06' + bytes([len(nombre)+1, 0x09]) + nombre.encode())
        print("BLE: Advertising reactivado.")
    except Exception as e:
        print("BLE start error:", e)
#------------------------------------------------------------

async def conectar_wifi():
    global wifi_conectado, ssid_configurado

    try:
        with open("wifi_config.json", "r") as f:
            cred = json.load(f)
            ssid = cred.get("ssid", "")
            password = cred.get("pass", "")
    except:
        print("Sin archivo wifi_config.json")
        return False

    if not ssid:
        print("SSID vacío, no se puede conectar")
        return False

    ssid_configurado = ssid
    print(f"Conectando a Wi-Fi: {ssid}...")

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    # Limpieza profunda del módem según Propuesta II para evitar bloqueos del ESP32
    try:
        wlan.disconnect()
    except:
        pass
    await asyncio.sleep(0.2)
    
    print(f"Conectando a Wi-Fi: {ssid}...")
    wlan.connect(ssid, password)

    # Timeout de 15 segundos (30 * 0.5) para no mantener apagado el BLE demasiado tiempo
    for _ in range(30):
        if wlan.isconnected():
            wifi_conectado = True
            print("Wi-Fi Conectado.", wlan.ifconfig())
            return True
        await asyncio.sleep(0.5)
        
    print("Timeout Wi-Fi.")
    return False


async def mantener_conexion_wifi():
    global wifi_conectado, mqtt_client

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    from ble_service import start_ble_service, stop_ble_service, is_ble_connected
    ble_activo = True # Viene activo del arranque

    while True:
        if wifi_conectado and not wlan.isconnected():
            print("Wi-Fi se perdió, marcando desconectado.")
            wifi_conectado = False
            mqtt_client = None

        if not wifi_conectado:
            # Si el usuario está usando el BLE (ej. configurando), no lo desconectamos para buscar Wi-Fi
            if ble_activo and is_ble_connected():
                print("BLE en uso activo, posponiendo reconexión Wi-Fi...")
                await asyncio.sleep(10)
                continue

            # Apagamos BLE ANTES de intentar conectar al Wi-Fi (Evita contención de radio/cuelgues)
            if ble_activo:
                print("Pausando BLE para reconexión Wi-Fi limpia...")
                await stop_ble_service()
                ble_activo = False
                
            print("Intentando reconectar Wi-Fi...")
            if await conectar_wifi():
                asyncio.create_task(sincronizar_hora_ntp_async())
                conectar_mqtt()
            else:
                # Si falló, reactivamos el BLE para que el usuario pueda configurarlo
                print("Wi-Fi desconectado. Activando BLE...")
                await start_ble_service(id_equipo[:15])
                ble_activo = True
        else:
            if mqtt_client is None:
                conectar_mqtt()
            if ble_activo:
                print("Wi-Fi conectado. Desactivando BLE...")
                await stop_ble_service()
                ble_activo = False

        # Si no hay wifi, esperamos más para dar tiempo al BLE de ser útil (ej 15 seg)
        await asyncio.sleep(15 if not wifi_conectado else 10)



async def sincronizar_hora_ntp_async():
    if not wifi_conectado:
        return

    try:
        print("Sincronizando reloj con NTP...")
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        tm = time.time() - 10800
        t = time.localtime(tm)
        await set_time_async(t)
        print("Hora NTP ajustada.")
    except Exception as e:
        print("Error NTP:", e)


async def sincronizacion_ntp_diaria():
    t_init = await get_time_async()
    ultimo_dia = t_init[2]
    while True:
        t = await get_time_async()
        if t[3] == 0 and t[4] == 0 and ultimo_dia != t[2]:
            ultimo_dia = t[2]
            await sincronizar_hora_ntp_async()
        await asyncio.sleep(30)


async def escuchar_comandos_mqtt():
    global mqtt_client
    last_ping = time.time()
    while True:
        if wifi_conectado and mqtt_client:
            try:
                mqtt_client.check_msg()
                
                if time.time() - last_ping >= 30:
                    mqtt_client.ping()
                    last_ping = time.time()
            except OSError as e:
                err_code = e.args[0] if e.args else None
                if err_code not in (-1, 11, 110, 115, 116):
                    print("Error MQTT check_msg OSError:", e)
                    mqtt_client = None
                    await asyncio.sleep(5)
            except Exception as e:
                print("Error General MQTT check_msg:", e)
                mqtt_client = None
                await asyncio.sleep(5)

        await asyncio.sleep(0.5)



# ======================================================================
# TAREA C: MÁQUINA DE ESTADOS NO BLOQUEANTE Y STATE RECOVERY
# ======================================================================
def esta_en_temporada_verano(t):
    fecha_actual = f"{t[1]:02d}{t[2]:02d}" 
    f_inv = config_data['Finvierno']
    f_ver = config_data['Fverano']
    if f_inv < f_ver:
        en_invierno = f_inv <= fecha_actual < f_ver
    else:
        en_invierno = fecha_actual >= f_inv or fecha_actual < f_ver
    return not en_invierno

def calcular_dosis_total(t):
    m_ver = 2 if esta_en_temporada_verano(t) else 1
    m_ref = 2 if config_data['Refuerzo'] == 1 else 1
    return (config_data['DosisMin'] * 60 + config_data['Dosis']) * m_ver * m_ref

ESTADOS_EEPROM = {"inactivo": 0, "solo_bomba": 1, "esperando_dosis": 2, "esperando_manual": 3, "dosificando": 4, "manual": 5, "mantenimiento_valvula": 6}
ESTADOS_INV = {v: k for k, v in ESTADOS_EEPROM.items()}

async def guardar_estado_recuperacion(estado_dict):
    estado_str = estado_dict.get("e", "inactivo")
    
    # Filtro: NO guardar estados manuales que no tengan fin (bomba manual infinita)
    if estado_str == "solo_bomba" and not estado_dict.get("bpd", False):
        estado_str = "inactivo"
        
    cod = ESTADOS_EEPROM.get(estado_str, 0)
    
    ahora = int(time.time())
    if estado_str != "inactivo":
        tb = int(estado_dict.get("tb", ahora))
        ti = int(estado_dict.get("ti", ahora))
        bpd = 1 if estado_dict.get("bpd", False) else 0
        
        if "esperando" in estado_str:
            es_min = config_data['EsperaMin'] * 60 + config_data['Espera']
            t_restante = max(0, int(es_min - (ahora - ti)))
        elif estado_str == "solo_bomba":
            t_restante = max(0, int(tb - ahora))
        elif estado_str in ["dosificando", "manual"]:
            if "tr" in estado_dict:
                t_restante = estado_dict["tr"]
            else:
                try:
                    t_rtc = await get_time_async()
                    d_tot = calcular_dosis_total(t_rtc)
                    t_restante = max(0, int(d_tot - (ahora - ti)))
                except:
                    t_restante = 0
        else:
            t_restante = 0
    else:
        t_restante = 0
        bpd = 0
        
    b_arr = bytearray(7)
    b_arr[0] = cod
    b_arr[1] = (t_restante >> 24) & 0xFF
    b_arr[2] = (t_restante >> 16) & 0xFF
    b_arr[3] = (t_restante >> 8) & 0xFF
    b_arr[4] = t_restante & 0xFF
    b_arr[5] = bpd
    b_arr[6] = sum(b_arr[0:6]) % 256
    
    async with i2c_lock:
        try:
            eeprom.write(0, b_arr)
            await asyncio.sleep_ms(10) # 10ms ciclo escritura EEPROM
        except Exception as e:
            print("Error EEPROM write:", e)

def cargar_estado_recuperacion():
    global estado_dosificador, timestamp_bomba_off, tiempo_inicio_espera, tiempo_inicio_dosis, bomba_encendida_por_dosis
    try:
        try:
            t_rtc = reloj.get_time()
        except:
            t_rtc = (2000, 1, 1, 0, 0, 0, 0)
            
        b_arr = eeprom.read(0, 7)
        if b_arr is None or len(b_arr) < 7: return
        chk = sum(b_arr[0:6]) % 256
        if b_arr[6] != chk: return
            
        cod = b_arr[0]
        if cod == 0 or cod not in ESTADOS_INV: return
            
        e = ESTADOS_INV[cod]
        t_restante = (b_arr[1] << 24) | (b_arr[2] << 16) | (b_arr[3] << 8) | b_arr[4]
        bpd = True if b_arr[5] == 1 else False
        
        ahora = time.time()
        
        if "esperando" in e:
            es_min = config_data['EsperaMin'] * 60 + config_data['Espera']
            ti = ahora - (es_min - t_restante)
            tb = ahora 
        elif e == "solo_bomba":
            tb = ahora + t_restante
            ti = ahora 
        elif e in ["dosificando", "manual"]:
            d_tot = calcular_dosis_total(t_rtc)
            ti = ahora - (d_tot - t_restante)
            tb = ahora 
        else:
            return
            
        if e == "solo_bomba" and ahora >= tb: return
        if e in ["dosificando", "manual"] and ahora - ti >= calcular_dosis_total(t_rtc): return
        
        es_min = config_data['EsperaMin'] * 60 + config_data['Espera']
        if e in ["esperando_dosis", "esperando_manual"] and ahora - ti >= es_min:
            ti = ahora 
            e = "dosificando" if e == "esperando_dosis" else "manual"
            
        print("🔥 RECUPERANDO ESTADO DESDE EEPROM:", e)
        estado_dosificador = e
        timestamp_bomba_off = tb
        bomba_encendida_por_dosis = bpd
        if "esperando" in e: tiempo_inicio_espera = ti
        else: tiempo_inicio_dosis = ti
            
        if e in ["dosificando", "manual"]:
            bomba_rele.value(1)
            man.value(1)
        elif e == "solo_bomba":
            bomba_rele.value(1)
    except Exception as ex:
        print("Error leyendo EEPROM:", ex)

async def registrar_dosificacion_exitosa(duracion_aplicada, tipo="Programada"):
    t = await get_time_async()
    config_data['ultimo_timestamp_dosis'] = time.time()
    nuevo_registro = {
        "fecha": f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}",
        "segundos": duracion_aplicada,
        "temp": "T.Alta" if esta_en_temporada_verano(t) else "T.Baja",
        "ref": config_data['Refuerzo'],
        "tipo": tipo
    }
    config_data['historial_dosis'].insert(0, nuevo_registro)
    config_data['historial_dosis'] = config_data['historial_dosis'][:10]
    await guardar_configuracion_async()

async def chequear_dosis_perdidas():
    try:
        t = await get_time_async()
        hoy_str = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
        dosis_hoy = [h for h in config_data['historial_dosis'] if h["fecha"].startswith(hoy_str) and h.get("tipo", "") == "Programada"]
        horas_dosis_hoy = [int(h["fecha"][11:13])*60 + int(h["fecha"][14:16]) for h in dosis_hoy]
        hora_actual_m = t[3]*60 + t[4]
        
        hubo_cambios = False
        for ev in config_data['Cronograma']:
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
                            "temp": "T.Alta" if esta_en_temporada_verano(t) else "T.Baja",
                            "ref": 0,
                            "tipo": "Perdida"
                        }
                        config_data['historial_dosis'].insert(0, registro_perdida)
                        hubo_cambios = True
                        
        if hubo_cambios:
            config_data['historial_dosis'] = config_data['historial_dosis'][:10]
            await guardar_configuracion_async()
    except Exception as e:
        print("Error Dosis Perdidas:", e)

async def maquina_de_estados_cloro():
    global estado_dosificador, timestamp_bomba_off, tiempo_inicio_espera, tiempo_inicio_dosis
    global ultimo_minuto_disparado, bomba_encendida_manual, bomba_encendida_por_dosis, tiempo_estado
    global tiempo_inicio_mantenimiento
    
    estado_anterior = estado_dosificador
    
    while True:
        ahora = time.time()
        t_rtc = await get_time_async()
        hora_actual_str = f"{t_rtc[3]:02d}{t_rtc[4]:02d}"
        minuto_actual_str = f"{t_rtc[2]:02d}-{t_rtc[3]:02d}:{t_rtc[4]:02d}" 
        dia_actual_str = str(t_rtc[6])

        if estado_dosificador in ["inactivo", "solo_bomba"]:
            tiempo_estado = 0
            
        if estado_dosificador == "inactivo":
            if ahora - config_data['ultimo_timestamp_dosis'] >= 90000: # 25 horas
                estado_dosificador = "mantenimiento_valvula"
                tiempo_inicio_mantenimiento = ahora
                man.value(1)
                print("Mantenimiento: Iniciando anti-atasco de valvula.")
                
            elif config_data['PausarProg'] == 0:
                for idx, evento in enumerate(config_data['Cronograma']):
                    dias_permitidos = str(evento.get("dias", "0123456"))
                    if evento.get("on") == hora_actual_str and ultimo_minuto_disparado != minuto_actual_str and dia_actual_str in dias_permitidos:
                        dur_min = int(evento.get("duracion", 0))
                        if dur_min <= 0: continue 
                        
                        ultimo_minuto_disparado = minuto_actual_str
                        bomba_rele.value(1) 
                        timestamp_bomba_off = ahora + (dur_min * 60)
                        
                        if evento.get("dosis") == 1:
                            if config_data['DosisNo'] == 0:
                                t_req = (config_data['EsperaMin'] * 60 + config_data['Espera']) + calcular_dosis_total(t_rtc)
                                if (dur_min * 60) < t_req: timestamp_bomba_off = ahora + t_req
                                estado_dosificador = "esperando_dosis"
                                tiempo_inicio_espera = ahora
                            else:
                                config_data['DosisNo'] -= 1
                                estado_dosificador = "solo_bomba"
                                await guardar_configuracion_async()
                        else:
                            estado_dosificador = "solo_bomba"
                        break

        elif estado_dosificador == "solo_bomba":
            if not bomba_encendida_manual and ahora >= timestamp_bomba_off:
                bomba_rele.value(0)
                estado_dosificador = "inactivo"
                print("Cronograma: Fin de evento. Bomba OFF.")

        elif estado_dosificador == "esperando_dosis":
            tiempo_estado = int(ahora - tiempo_inicio_espera) 
            es_min = config_data['EsperaMin'] * 60 + config_data['Espera']
            if ahora - tiempo_inicio_espera >= es_min:
                estado_dosificador = "dosificando"
                tiempo_inicio_dosis = ahora
                tiempo_estado = 0
                man.value(1) 
                print("Secuencia: Espera concluida. Válvula ABIERTA.")

        elif estado_dosificador == "esperando_manual":
            tiempo_estado = int(ahora - tiempo_inicio_espera)
            es_min = config_data['EsperaMin'] * 60 + config_data['Espera']
            if ahora - tiempo_inicio_espera >= es_min:
                estado_dosificador = "manual"
                tiempo_inicio_dosis = ahora
                tiempo_estado = 0
                man.value(1)

        elif estado_dosificador == "dosificando":
            tiempo_estado = int(ahora - tiempo_inicio_dosis) 
            dosis_total = calcular_dosis_total(t_rtc)
            if ahora - tiempo_inicio_dosis >= dosis_total:
                man.value(0) 
                await registrar_dosificacion_exitosa(dosis_total)
                if ahora < timestamp_bomba_off: estado_dosificador = "solo_bomba"
                else:
                    bomba_rele.value(0)
                    estado_dosificador = "inactivo"
                
                if config_data['Refuerzo'] == 1:
                    config_data['Refuerzo'] = 0
                    await guardar_configuracion_async()

        elif estado_dosificador == "manual":
            tiempo_estado = int(ahora - tiempo_inicio_dosis) 
            dosis_total = calcular_dosis_total(t_rtc)
            if ahora - tiempo_inicio_dosis >= dosis_total:
                man.value(0)
                estado_dosificador = "solo_bomba"
                if bomba_encendida_por_dosis: timestamp_bomba_off = ahora + 1800
                else: timestamp_bomba_off = ahora
                await registrar_dosificacion_exitosa(dosis_total, tipo="Manual")
                if config_data['Refuerzo'] == 1:
                    config_data['Refuerzo'] = 0
                    await guardar_configuracion_async()

        elif estado_dosificador == "mantenimiento_valvula":
            if ahora - tiempo_inicio_mantenimiento >= 3:
                man.value(0)
                estado_dosificador = "inactivo"
                # Registrar en historial (aparece en rojo en la app por tipo="Anti-atasco")
                await registrar_dosificacion_exitosa(3, tipo="Anti-atasco")
                print("Mantenimiento: Fin de anti-atasco.")

        # Guardar estado de recuperación si cambia o continuo cada 10s
        ti = tiempo_inicio_espera if "esperando" in estado_dosificador else tiempo_inicio_dosis
        datos_recup = {"e": estado_dosificador, "tb": timestamp_bomba_off, "ti": ti, "bpd": bomba_encendida_por_dosis}
        
        if estado_dosificador in ["dosificando", "manual"]:
            try:
                d_tot = calcular_dosis_total(t_rtc)
                datos_recup["tr"] = max(0, int(d_tot - (ahora - ti)))
            except: pass
            
        if estado_dosificador != estado_anterior:
            if estado_dosificador != "inactivo":
                await guardar_estado_recuperacion(datos_recup)
            
            # Enviar log inmediato
            asyncio.create_task(enviar_log_nube({"evento": f"Cambio estado: {estado_dosificador}"}))
            # Forzar telemetría
            asyncio.create_task(enviar_telemetria())
            estado_anterior = estado_dosificador
        else:
            # Guardado continuo cada 10 segundos
            if estado_dosificador != "inactivo":
                asyncio.create_task(guardar_estado_recuperacion(datos_recup))
                
        await asyncio.sleep(10) # Evaluar cada 10s

# ======================================================================
# TAREA D: PROCESADOR DE COMANDOS ASÍNCRONO
# ======================================================================
async def procesar_comando(data):
    global mensaje_temporal, tiempo_mensaje, estado_dosificador, tiempo_inicio_dosis, tiempo_inicio_espera
    global timestamp_bomba_off, bomba_encendida_manual, bomba_encendida_por_dosis, cronograma_modificado
    global config_data, redes_wifi, _cron_buffer
    
    comando = data.get("comando", "")
    print(f"Procesando comando: {comando}")
    
    if comando == "escanear_wifi":
        async def task_escanear():
            global redes_wifi, mensaje_temporal, tiempo_mensaje
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            redes = wlan.scan()
            redes_wifi = [r[0].decode('utf-8') for r in redes if r[0]]
            mensaje_temporal = "Escaneo completado"
            tiempo_mensaje = time.time()
        asyncio.create_task(task_escanear())
        
    elif comando == "config_wifi":
        with open("wifi_config.json", "w") as f:
            json.dump({"ssid": data.get("ssid", ""), "pass": data.get("pass", "")}, f)
        time.sleep(1)
        machine.reset()
        
    elif comando == "bombasi":
        estado_dosificador = "solo_bomba"
        bomba_rele.value(1)
        bomba_encendida_manual = True
        timestamp_bomba_off = time.time() + 31536000
        # Guardar como inactivo en la recuperación: si hay un corte, la bomba NO
        # se restaura automáticamente (el usuario no sabe que quedó encendida).
        await guardar_estado_recuperacion({"e": "inactivo"})
        mensaje_temporal = "Bomba Encendida"; tiempo_mensaje = time.time()
        
    elif comando == "bombano":
        bomba_encendida_manual = False
        estado_dosificador = "inactivo"
        bomba_rele.value(0)
        mensaje_temporal = "Bomba Apagada"; tiempo_mensaje = time.time()
        
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
        mensaje_temporal = "Dosis manual iniciada"; tiempo_mensaje = time.time()
        
    elif comando in ("manualno", "cancelar_dosis"):
        man.value(0)
        bomba_rele.value(0)
        estado_dosificador = "inactivo"
        mensaje_temporal = "Cancelado"; tiempo_mensaje = time.time()
        
    elif comando == "refuerzosi":
        config_data['Refuerzo'] = 1
        await guardar_configuracion_async()
        mensaje_temporal = "Refuerzo Activado"; tiempo_mensaje = time.time()
        
    elif comando == "refuerzono":
        config_data['Refuerzo'] = 0
        await guardar_configuracion_async()
        mensaje_temporal = "Refuerzo Desactivado"; tiempo_mensaje = time.time()
        
    elif comando == "pausarprogsi":
        config_data['PausarProg'] = 1
        await guardar_configuracion_async()
        mensaje_temporal = "Mantenimiento Activado"; tiempo_mensaje = time.time()
        
    elif comando == "pausarprogno":
        config_data['PausarProg'] = 0
        await guardar_configuracion_async()
        mensaje_temporal = "Mantenimiento Desactivado"; tiempo_mensaje = time.time()
        
    elif comando == "config_general":
        config_data['Dosis'] = max(0, min(int(data.get("Dosis", config_data['Dosis'])), 59))
        config_data['DosisMin'] = max(0, min(int(data.get("DosisMin", config_data['DosisMin'])), 15))
        config_data['Espera'] = max(0, min(int(data.get("Espera", config_data['Espera'])), 59))
        config_data['EsperaMin'] = max(0, min(int(data.get("EsperaMin", config_data['EsperaMin'])), 30))
        config_data['Fverano'] = data.get("Fverano", config_data['Fverano'])
        config_data['Finvierno'] = data.get("Finvierno", config_data['Finvierno'])
        await guardar_configuracion_async()
        cronograma_modificado = True
        mensaje_temporal = "Config guardada"; tiempo_mensaje = time.time()
        
    elif comando == "config_anular":
        config_data['DosisNo'] = int(data.get("DosisNo", config_data['DosisNo']))
        await guardar_configuracion_async()
        mensaje_temporal = "Anulación guardada"; tiempo_mensaje = time.time()
        
    elif comando == "config_cronograma":
        nuevos_horarios = data.get("cronograma", [])
        if isinstance(nuevos_horarios, list) and len(nuevos_horarios) <= 10:
            config_data['Cronograma'] = nuevos_horarios
            await guardar_configuracion_async()
            cronograma_modificado = True
            mensaje_temporal = "Cronograma guardado"; tiempo_mensaje = time.time()
            
    elif comando == "borrar_historial":
        config_data['historial_dosis'] = []
        await guardar_configuracion_async()
        mensaje_temporal = "Historial borrado"; tiempo_mensaje = time.time()
        
    elif comando == "reset_fabrica":
        try: os.remove("wifi_config.json")
        except: pass
        try: os.remove("config_cloro.json")
        except: pass
        machine.reset()
        
    elif comando == "sync_rtc":
        fecha = data.get("fecha", ""); hora = data.get("hora", "")
        if fecha and hora:
            year = int(fecha[:4]); month = int(fecha[5:7]); day = int(fecha[8:10])
            hour = int(hora[:2]); minute = int(hora[3:5])
            m_t = month; y_t = year
            if m_t < 3: m_t += 12; y_t -= 1
            q = day; K = y_t % 100; J = y_t // 100
            weekday = (((q + 13*(m_t + 1)//5 + K + K//4 + J//4 + 5*J) % 7) + 5) % 7
            asyncio.create_task(set_time_async((year, month, day, hour, minute, 0, weekday, 0)))
            mensaje_temporal = "Reloj sincronizado"; tiempo_mensaje = time.time()
            cronograma_modificado = True # Forzar envío del cronograma al conectar

    elif comando in ("pedir_historial", "ping"):
        # La app envía este comando al conectar para forzar una telemetría completa
        global historial_pedido
        cronograma_modificado = True
        historial_pedido = True

    # ------------------------------------------------------------------
    # Protocolo chunked para cronograma (usado por BLE para evitar
    # enviar JSONs grandes que se pueden perder en fragmentación).
    # La app envía: cron_start → N × cron_add → cron_commit.
    # Los comandos intermedios retornan SIN enviar telemetría para no
    # saturar el canal BLE mientras se sigue recibiendo el cronograma.
    # ------------------------------------------------------------------
    elif comando == "cron_start":
        _cron_buffer = []
        print(f"Cronograma BLE: inicio, total esperado={data.get('total',0)}")
        return  # Sin telemetría intermedia

    elif comando == "cron_add":
        if _cron_buffer is not None:
            entry = {
                "on":      str(data.get("on", "0000")),
                "duracion": int(data.get("duracion", 0)),
                "dosis":   int(data.get("dosis", 0)),
                "dias":    str(data.get("dias", "0123456"))
            }
            _cron_buffer.append(entry)
            print(f"Cronograma BLE: entrada {data.get('idx','')} recibida ({entry['on']})")
        return  # Sin telemetría intermedia

    elif comando == "cron_commit":
        if _cron_buffer is not None and 0 < len(_cron_buffer) <= 10:
            config_data['Cronograma'] = _cron_buffer
            await guardar_configuracion_async()
            cronograma_modificado = True
            n = len(_cron_buffer)
            mensaje_temporal = f"Cronograma guardado ({n} prog.)"; tiempo_mensaje = time.time()
            print(f"Cronograma BLE: guardado {n} entradas OK")
        else:
            print(f"Cronograma BLE: commit fallido (buffer={_cron_buffer})")
        _cron_buffer = None
        # Cae al enviar_telemetría de abajo → la app recibe confirmación con el nuevo cronograma

    # Forzar envío de telemetría por cambio tras comando
    global telemetria_pendiente
    telemetria_pendiente = True

async def procesar_cola_ble(rx_queue):
    while True:
        try:
            cmd_dict = await rx_queue.get()

            if not isinstance(cmd_dict, dict):
                print("Comando BLE inválido:", cmd_dict)
                continue

            try:
                await procesar_comando(cmd_dict)
            except Exception as e:
                print("Error procesando comando BLE:", e)

        except Exception as e:
            print("Error en tarea BLE:", e)
            await asyncio.sleep(0.1)




# ======================================================================
# TAREA E: TELEMETRÍA UNIFICADA Y LOGS
# ======================================================================
async def publish_async(client, topic, msg):
    # Constructor manual del paquete MQTT Publish (QoS 0) para evitar usar el método bloqueante
    pkt = bytearray()
    pkt.append(0x30)
    topic_b = topic.encode("utf-8")
    msg_b = msg.encode("utf-8")
    sz = len(topic_b) + 2 + len(msg_b)
    while sz > 0x7F:
        pkt.append((sz & 0x7F) | 0x80)
        sz >>= 7
    pkt.append(sz)
    pkt.append(len(topic_b) >> 8)
    pkt.append(len(topic_b) & 0xFF)
    pkt.extend(topic_b)
    pkt.extend(msg_b)
    
    # Escritura NO bloqueante para no colgar el ESP32
    client.sock.setblocking(False)
    bytes_escritos = 0
    timeout = 0
    try:
        while bytes_escritos < len(pkt):
            try:
                res = client.sock.write(pkt[bytes_escritos:])
                if res is not None and res > 0:
                    bytes_escritos += res
            except OSError as e:
                if e.args[0] == 11: # EAGAIN (Buffer lleno, esperar)
                    pass
                else:
                    raise
            await asyncio.sleep(0.05)
            timeout += 0.05
            if timeout > 15.0: # Timeout largo de 15s antes de rendirnos
                raise OSError("Timeout TCP en Publish_Async")
    finally:
        client.sock.setblocking(True)

async def enviar_log_nube(evento):
    if not wifi_conectado or not mqtt_client: return
    t = await get_time_async()
    evento["fecha"] = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
    topic = f"dosimat/{id_equipo}/sys_log"
    try:
        await publish_async(mqtt_client, topic, json.dumps(evento))
    except Exception as e:
        print("MQTT Publish Log Error:", e)

async def enviar_telemetria():
    global wifi_conectado, mqtt_client, historial_pedido, cronograma_modificado, telemetria_en_progreso

    if telemetria_en_progreso:
        print("telemetria_en_progreso TRABADO! Ignorando este ciclo.")
        return
    
    gc.collect() # Limpieza agresiva de memoria antes de armar el paquete JSON pesado
    telemetria_en_progreso = True
    try:
        t_rtc = await get_time_async()
            
        ahora = time.time()
        
        t_bomba_off_seg = max(0, int(timestamp_bomba_off - ahora)) if estado_dosificador == "solo_bomba" else 0
        telemetria = {
            "id_equipo": id_equipo,
            "version": version,
            "estado": estado_dosificador,
            "t_estado": tiempo_estado,
            "t_bomba_off_seg": t_bomba_off_seg,
            "mensaje": mensaje_temporal if (ahora - tiempo_mensaje <= duracion_mensaje) else "",
            "bomba": bomba_rele.value() == 1, 
            "temporada": "Alta" if esta_en_temporada_verano(t_rtc) else "Baja",
            "Refuerzo": config_data['Refuerzo'] == 1,
            "PausarProg": config_data['PausarProg'],
            "DosisNo": config_data['DosisNo'],
            "Dosis": config_data['Dosis'],
            "DosisMin": config_data['DosisMin'],
            "Espera": config_data['Espera'],
            "EsperaMin": config_data['EsperaMin'],
            "Fverano": config_data['Fverano'],
            "Finvierno": config_data['Finvierno'],
            "wifi_ssid": ssid_configurado,
            "rtc_fecha": f"{t_rtc[0]:04d}-{t_rtc[1]:02d}-{t_rtc[2]:02d}",
            "rtc_hora": f"{t_rtc[3]:02d}:{t_rtc[4]:02d}:{t_rtc[5]:02d}",
            "dosis_total_seg": calcular_dosis_total(t_rtc)
        }
        
        try:
            telemetria["temp_rtc"] = reloj.temperature()
        except Exception as e:
            print("Error leyendo temperatura RTC:", e)
            telemetria["temp_rtc"] = 0
        
        if historial_pedido:
            telemetria["historial"] = config_data['historial_dosis']
            historial_pedido = False

        if redes_wifi:
            telemetria["redes_wifi"] = redes_wifi

        if cronograma_modificado:
            telemetria["cronograma"] = config_data['Cronograma']
            cronograma_modificado = False
            
        if wifi_conectado and mqtt_client:
            topic = f"dosimat/{id_equipo}/telemetria"
            try:
                await publish_async(mqtt_client, topic, json.dumps(telemetria))
                print("Telemetría enviada a MQTT.")
            except Exception as e:
                print("MQTT Publish Telemetria Error:", e)
                mqtt_client = None

        from ble_service import send_json_async
        await send_json_async(telemetria)
        gc.collect()

    except Exception as e:
        print("Error crítico en enviar_telemetria:", e)
    finally:
        telemetria_en_progreso = False


    
async def tarea_telemetria_periodica():
    global telemetria_pendiente
    contador = 0
    while True:
        await asyncio.sleep(1)
        contador += 1
        # Frecuencia: 2s en modos activos, 900s en reposo (como en VERS_OK para máxima estabilidad)
        intervalo = 2 if estado_dosificador != "inactivo" else 900
        
        if telemetria_pendiente or contador >= intervalo:
            if not telemetria_en_progreso:
                telemetria_pendiente = False
                contador = 0
                await enviar_telemetria()
            else:
                pass # Retener telemetria_pendiente para el próximo segundo

# ======================================================================
# TAREA F: DESTELLOS DEL LED DE ESTADO
# ======================================================================
LED_PATRONES = {
    'inactivo':             [(1, 200), (0, 5000)],
    'inactivo_refuerzo':    [(1, 200), (0, 200), (1, 200), (0, 5000)],
    'dosificando':          [(1, 1000), (0, 1000)],
    'dosificando_refuerzo': [(1, 5000), (0, 200)],
    'solo_bomba':           [(1, 500), (0, 500)],
    'solo_bomba_refuerzo':  [(1, 200), (0, 200), (1, 200), (0, 500)],
    'esperando_manual':     [(1, 1000), (0, 200)],
    'mantenimiento':        [(1, 100), (0, 100)]
}

async def tarea_parpadeo_led():
    while True:
        patron_sel = 'inactivo'
        if config_data['PausarProg'] == 1:
            patron_sel = 'mantenimiento'
        elif estado_dosificador in ["dosificando", "manual"]:
            patron_sel = 'dosificando_refuerzo' if config_data['Refuerzo'] == 1 else 'dosificando'
        elif estado_dosificador == "solo_bomba":
            patron_sel = 'solo_bomba_refuerzo' if config_data['Refuerzo'] == 1 else 'solo_bomba'
        elif estado_dosificador == "esperando_manual":
            patron_sel = 'esperando_manual'
        else:
            patron_sel = 'inactivo_refuerzo' if config_data['Refuerzo'] == 1 else 'inactivo'
            
        patron = LED_PATRONES[patron_sel]
        
        for valor, tiempo_ms in patron:
            ref.value(valor)
            # Evaluar si el patrón debe cambiar chequeando antes de completar el ciclo
            # Para simplificar y mantener la lógica asíncrona, dormimos el tiempo correspondiente
            await asyncio.sleep_ms(tiempo_ms)

# ======================================================================
# ARRANQUE ASÍNCRONO DEL SISTEMA (ENTRY POINT)
# ======================================================================
async def main():
    await asyncio.sleep(1) # Pequeña pausa para estabilizar hardware (I2C) tras corte de luz
    print("Iniciando Dosimat Async...")
    
    # Restablecer el Watchdog Timer (Timeout 60 segundos)
    # Si el bucle asyncio se cuelga (ej. buffer TCP lleno), la placa se reiniciará automáticamente.
    wdt = machine.WDT(timeout=60000)
    
    cargar_configuracion()
    cargar_estado_recuperacion()
    
    # Importar servicios BLE
    from ble_service import start_ble_service, rx_queue
    
    # Intentar conexión Wi-Fi Inicial
    # (Ya no arrancamos BLE primero para evitar contención de la antena)
    conectado = await conectar_wifi()
    if conectado: 
        conectar_mqtt()
    else:
        # Si falló el Wi-Fi inicial, arrancamos BLE para permitir configuración
        await start_ble_service(id_equipo[:15])
    
    # 3. Lanzar Tareas Asíncronas en Background
    asyncio.create_task(mantener_conexion_wifi())
    asyncio.create_task(sincronizacion_ntp_diaria())
    asyncio.create_task(escuchar_comandos_mqtt())
    asyncio.create_task(maquina_de_estados_cloro())
    asyncio.create_task(procesar_cola_ble(rx_queue))
    asyncio.create_task(tarea_telemetria_periodica())
    asyncio.create_task(tarea_parpadeo_led())
    
 


    # Registrar Inicio
    if conectado:
        await sincronizar_hora_ntp_async()
        
    await registrar_dosificacion_exitosa(0, tipo="Reinicio Sistema")
        
    if conectado:
        await enviar_log_nube({"evento": "Sistema Iniciado Async", "version": version})
        await enviar_telemetria()
        
    await chequear_dosis_perdidas()
    
    # Mantener el loop principal vivo y alimentar el Watchdog
    while True:
        wdt.feed()
        await asyncio.sleep(5)

# ======================================================================
# VENTANA DE SEGURIDAD (Permite Ctrl+C antes del Watchdog)
# ======================================================================
print("Esperando 3s (Ventana de seguridad para Thonny/Ctrl+C)...")
ref.value(1) # Prendo el led AZUL indicador del tablero
time.sleep(3)
ref.value(0)
print("Iniciando ejecución...")

# Lanzar Event Loop
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Interrumpido por el usuario")
except Exception as e:
    print("Error Fatal Loop Async:", e)
    try:
        import sys
        with open("crash.log", "w") as f:
            sys.print_exception(e, f)
        print("Crash log guardado en Flash.")
    except Exception as ex:
        print("No se pudo escribir crash.log:", ex)
    import time
    time.sleep(2)
    machine.reset()
