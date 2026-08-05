import machine
import json
import os
import time
import uasyncio as asyncio
import sys_log
import gc
import ds3231
import binascii
import hashlib
from utils import AsyncQueue  # Fix #3: Cola asíncrona centralizada en utils.py

CONFIG_FILE = "config_riego.json"
DEFAULT_CONFIG = {
  "config_version": 0,
  "max_zonas": 4,
  "modo_bomba": True,
  "calibracion_temp": 0.0,
  "sensor_lluvia_activo": True,
  "sensor_lluvia_tipo": "NA",
  "ajustes_estacionales": [
    {"nombre": "Verano", "inicio": "12-21", "fin": "03-20", "porcentaje": 100},
    {"nombre": "Otono", "inicio": "03-21", "fin": "06-20", "porcentaje": 100},
    {"nombre": "Invierno", "inicio": "06-21", "fin": "09-20", "porcentaje": 100},
    {"nombre": "Primavera", "inicio": "09-21", "fin": "12-20", "porcentaje": 100}
  ],
  "timestamp_rain_delay": 0,
  "token_acceso": None,
  "nombres_zonas": {
    "1": "Zona 1", "2": "Zona 2", "3": "Zona 3", "4": "Zona 4",
    "5": "Zona 5", "6": "Zona 6", "7": "Zona 7", "8": "Zona 8"
  },
  "programas": {}
}

config_data = {}
reinicio_pendiente = False

_config_lock = asyncio.Lock()

# Caché de temperatura para proteger el bus I2C
_cached_temp = "N/A"
_cached_temp_ts = 0
ultimo_arranque_minuto = ""

# Mapado de Hardware
MV_PIN = 25
ZONAS_PINS = [18, 23, 26, 27, 19, 32, 33, 14]
RAIN_PIN = 4
ADC_PIN = 34
BOOT_PIN = 0

# Objetos Hardware
mv = None
zonas = []
rain_sensor = None
adc = None
boot_button = None
reloj_rtc = None


# Variables de Estado
estado_riego = "IDLE"
programa_activo = None
zona_actual_idx = "0"  
cola_programas = AsyncQueue()
tx_queue = AsyncQueue() # Cola para enviar datos (telemetria, config, logs) al movil
abort_event = None

# Seguimiento para telemetria
ts_inicio_ciclo = 0
duracion_ciclo_actual = 0
telemetria_extra = {}

# Seguridad
chip_id = binascii.hexlify(machine.unique_id()).decode('utf-8').upper()

def calcular_hash_seguro():
    token = config_data.get("token_acceso")
    if not token:
        return None
    # La PWA extrajo solo los últimos 4 caracteres del nombre BLE (Ej: Riego_4040)
    # Por lo tanto debemos hashear chip_id[-4:] para que coincidan EXACTAMENTE
    data = (chip_id[-4:] + token).encode('utf-8')
    h = hashlib.sha256(data).digest()
    return binascii.hexlify(h).decode('utf-8')

async def cargar_configuracion():
    global config_data
    try:
        with open(CONFIG_FILE, "r") as f:
            config_data = json.load(f)
            # Asegurar que existan las claves nuevas
            for k, v in DEFAULT_CONFIG.items():
                if k not in config_data:
                    config_data[k] = v
            # Si ajustes_estacionales está vacío o no tiene 4, forzar default
            if not isinstance(config_data.get("ajustes_estacionales"), list) or len(config_data["ajustes_estacionales"]) != 4:
                config_data["ajustes_estacionales"] = DEFAULT_CONFIG["ajustes_estacionales"]
            print("Configuración de riego cargada.")
    except Exception as e:
        print("Creando archivo de configuración por defecto...", e)
        config_data = DEFAULT_CONFIG.copy()
        await guardar_configuracion()

async def guardar_configuracion():
    async with _config_lock:
        try:
            await asyncio.sleep_ms(10)
            gc.collect()
            with open(CONFIG_FILE + ".tmp", "w") as f:
                json.dump(config_data, f)
            try:
                os.remove(CONFIG_FILE)
            except OSError:
                pass
            os.rename(CONFIG_FILE + ".tmp", CONFIG_FILE)
            gc.collect()
            await asyncio.sleep_ms(10)
        except Exception as e:
            print("Error guardando config:", e)

def get_time():
    """Retorna tiempo (año, mes, dia, hora, min, seg, diasemana) local ajustado a UTC-3 (Argentina)"""
    return time.localtime(time.time() - 10800)[:7]

async def init_hardware():
    global mv, zonas, rain_sensor, adc, boot_button, reloj_rtc
    
    mv = machine.Pin(MV_PIN, machine.Pin.OUT, value=0)
    
    max_z = min(8, max(1, config_data.get("max_zonas", 4)))
    zonas = []
    for i in range(max_z):
        p = machine.Pin(ZONAS_PINS[i], machine.Pin.OUT, value=1)
        zonas.append(p)
        
    rain_sensor = machine.Pin(RAIN_PIN, machine.Pin.IN)
    adc = machine.ADC(machine.Pin(ADC_PIN))
    adc.atten(machine.ADC.ATTN_11DB)
    boot_button = machine.Pin(BOOT_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    
    try:
        try:
            # Intentar SoftI2C primero para evitar bloqueos del driver de hardware I2C del ESP32
            i2c = machine.SoftI2C(scl=machine.Pin(22), sda=machine.Pin(21))
            print("SoftI2C cargado para RTC.")
        except (AttributeError, ValueError):
            # Fallback a hardware I2C si SoftI2C no está soportado en la build de MicroPython
            i2c = machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21))
            print("Hardware I2C(0) cargado para RTC.")
            
        reloj_rtc = ds3231.DS3231(i2c)
        print("DS3231 RTC Detectado y cargado.")
        
        # Sincronizar el reloj interno del ESP32 con la hora del DS3231 convertido a UTC (Argentina es UTC-3)
        try:
            t_local = reloj_rtc.get_time()
            # Convertir tupla local a segundos desde Y2K
            sec_local = time.mktime((t_local[0], t_local[1], t_local[2], t_local[3], t_local[4], t_local[5], t_local[6], 0))
            # Sumar 3 horas (10800 segundos) para pasarlo a UTC
            sec_utc = sec_local + 10800
            t_utc = time.localtime(sec_utc)
            
            rtc_int = machine.RTC()
            rtc_int.datetime((t_utc[0], t_utc[1], t_utc[2], t_utc[6], t_utc[3], t_utc[4], t_utc[5], 0))
            print("[CORE] Reloj interno sincronizado en UTC con RTC externo:", time.localtime())
        except Exception as e_sync:
            print("Error sincronizando reloj interno con RTC:", e_sync)
    except Exception as e:
        print("Error inicializando RTC:", e)
        reloj_rtc = None

def es_sensor_lluvia_activo_y_detectando():
    """Comprueba si el sensor de lluvia físico está habilitado y detectando lluvia (evaluando tipo NA o NC con debouncing de 50ms)."""
    if not config_data.get("sensor_lluvia_activo", True):
        return False
    if not rain_sensor:
        return False
    
    # Debouncing por software (50ms) para filtrar rebotes mecánicos del interruptor o ruido en cables largos
    val1 = rain_sensor.value()
    time.sleep_ms(25)
    val2 = rain_sensor.value()
    if val1 != val2:
        time.sleep_ms(25)
        val = rain_sensor.value()
    else:
        val = val1

    tipo = str(config_data.get("sensor_lluvia_tipo", "NA")).upper()
    if tipo == "NC":
        return val == 1
    else: # NA
        return val == 0

async def enviar_telemetria():
    """ Encola el estado actual para que se transmita a la app """
    if estado_riego == "IDLE":
        if es_sensor_lluvia_activo_y_detectando():
            est = "PAUSA: SENSOR"
        elif config_data.get("timestamp_sensor_lluvia_clear", 0) > time.time():
            est = "PAUSA: SECADO"
        elif config_data.get("timestamp_rain_delay", 0) > time.time():
            est = "PAUSA: MANUAL"
        else:
            est = "IDLE"
        t_rest = 0
        t_tot = 1
    else:
        est = estado_riego
        elapsed = time.time() - ts_inicio_ciclo
        t_rest = max(0, duracion_ciclo_actual - int(elapsed))
        t_tot = duracion_ciclo_actual if duracion_ciclo_actual > 0 else 1
        
    payload_data = {
        "estado": est,
        "zona": zona_actual_idx,
        "tiempo_restante": t_rest,
        "tiempo_total": t_tot,
        "temp": _cached_temp,
        "timestamp_rain_delay": config_data.get("timestamp_rain_delay", 0),
        "timestamp_sensor_lluvia_clear": config_data.get("timestamp_sensor_lluvia_clear", 0)
    }
    payload_data.update(telemetria_extra)
    
    await tx_queue.put({
        "tipo": "TELEMETRIA",
        "data": payload_data
    })

async def tarea_monitoreo_corriente():
    global estado_riego
    UMBRAL_ADC = 2500 
    ventanas_alto = 0
    
    while True:
        if estado_riego in ["PRESURIZANDO", "REGANDO"]:
            if adc.read() > UMBRAL_ADC:
                ventanas_alto += 1
            else:
                ventanas_alto = 0
                
            if ventanas_alto >= 5:
                print("FALLO CORRIENTE DETECTADO")
                await sys_log.log_event({"tipo": "error", "msg": "Fallo corriente. Corto detectado."})
                if abort_event is not None:  # Fix #5: Guard contra race condition al arranque
                    abort_event.set()
                ventanas_alto = 0
                # Si falla forzamos FALLO_CORRIENTE
                estado_riego = "FALLO_CORRIENTE"
                await enviar_telemetria()
                await asyncio.sleep(5) 
        else:
            ventanas_alto = 0
            
        await asyncio.sleep_ms(200)

async def tarea_reset_emergencia():
    presionado_s = 0
    while True:
        if boot_button.value() == 0:
            presionado_s += 1
            if presionado_s >= 10:
                print("RESET DE EMERGENCIA INICIADO")
                config_data["token_acceso"] = None
                await guardar_configuracion()
                try:
                    os.remove(sys_log.LOG_FILE)
                except:
                    pass
                machine.reset()
        else:
            presionado_s = 0
        await asyncio.sleep(1)

def obtener_num_zona(z_str):
    try:
        s = str(z_str).upper().replace("Z", "").strip()
        return int(s)
    except:
        return 999

def apagar_todo():
    mv.value(0) # Apagar (lógica directa)
    for z in zonas:
        z.value(1) # Apagar (lógica inversa)

async def ejecutar_riego():
    global estado_riego, programa_activo, abort_event, reinicio_pendiente
    global zona_actual_idx, ts_inicio_ciclo, duracion_ciclo_actual, telemetria_extra
    
    while True:
        if estado_riego == "IDLE" or estado_riego == "FALLO_CORRIENTE":
            programa_activo = await cola_programas.get()
            abort_event.clear()
            estado_riego = "PRESURIZANDO"
            ts_inicio_ciclo = time.time()
            duracion_ciclo_actual = 2
            zona_actual_idx = ""
            telemetria_extra = {
                "t_prog": 0, "ajuste": 100, "t_real": 0, "ciclo": 0, "remojo": 0,
                "ciclo_actual": 0, "ciclos_totales": 0, "remojo_actual": 0, "remojos_totales": 0
            }
            await sys_log.log_event({"tipo": "inicio_prog", "prog": programa_activo.get("nombre", "Manual")})
            await enviar_telemetria()
            
        elif estado_riego == "PRESURIZANDO":
            mv.value(1) # Encender (lógica directa)
            try:
                await asyncio.wait_for(abort_event.wait(), timeout=2.0)
                # Abortado
                apagar_todo()
                estado_riego = "IDLE"
                await enviar_telemetria()
                continue
            except asyncio.TimeoutError:
                estado_riego = "REGANDO"
                
        elif estado_riego == "REGANDO":
            zonas_prog = list(programa_activo.get("zonas", {}).keys())
            zonas_prog.sort(key=obtener_num_zona)
            
            ajuste = 1.0
            if programa_activo.get("nombre") != "Manual":
                try:
                    t = get_time()
                    current_mm_dd = f"{t[1]:02d}-{t[2]:02d}"
                    temporadas = config_data.get("ajustes_estacionales", [])
                    for temp in temporadas:
                        ini = temp.get("inicio", "01-01")
                        fin = temp.get("fin", "12-31")
                        if ini <= fin:
                            if ini <= current_mm_dd <= fin:
                                ajuste = temp.get("porcentaje", 100) / 100.0
                                break
                        else:
                            # Caso cruce de año (ej: 12-21 a 03-20)
                            if current_mm_dd >= ini or current_mm_dd <= fin:
                                ajuste = temp.get("porcentaje", 100) / 100.0
                                break
                except Exception as e:
                    print("Error RTC estacional:", e)
            
            for idx, str_z in enumerate(zonas_prog):
                z_idx = obtener_num_zona(str_z) - 1
                if z_idx >= len(zonas) or z_idx < 0:
                    continue
                    
                z_config = programa_activo["zonas"][str_z]
                min_base = z_config.get("minutos", 0)
                min_reales = round(min_base * ajuste)
                
                if min_reales < 0.5:
                    continue
                    
                c_min = z_config.get("cycle_min", min_reales)
                if c_min > min_reales or c_min <= 0: c_min = min_reales
                s_min = z_config.get("soak_min", 0)
                
                ciclos = (min_reales // c_min) + (1 if min_reales % c_min != 0 else 0)
                soak = z_config.get("soak_min", 0)
                
                telemetria_extra = {
                    "t_prog": min_base,
                    "ajuste": int(ajuste * 100) if programa_activo.get("nombre") != "Manual" else 100,
                    "t_real": min_reales,
                    "ciclo": c_min,
                    "remojo": s_min,
                    "ciclo_actual": 0,
                    "ciclos_totales": ciclos,
                    "remojo_actual": 0,
                    "remojos_totales": max(0, ciclos - 1) if soak > 0 else 0
                }
                
                for c in range(ciclos):
                    t_ciclo = c_min if c < ciclos - 1 else min_reales - (c_min * c)
                    
                    if abort_event.is_set():
                        break
                        
                    # Encender zona y notificar PWA
                    zonas[z_idx].value(0)
                    mv.value(1) # Asegurar bomba (MV) encendida
                    zona_actual_idx = str_z
                    duracion_ciclo_actual = int(t_ciclo * 60)
                    ts_inicio_ciclo = time.time()
                    estado_riego = "REGANDO"
                    telemetria_extra.update({
                        "ciclo_actual": c + 1,
                        "remojo_actual": 0
                    })
                    await sys_log.log_event({
                        "tipo": "inicio_zona",
                        "zona": str_z,
                        "duracion": round(t_ciclo, 1),
                        "prog": programa_activo.get("nombre", "Manual"),
                        "ajuste": int(ajuste * 100) if programa_activo.get("nombre") != "Manual" else 100,
                        "ciclo_actual": c + 1,
                        "ciclos_totales": ciclos
                    })
                    await enviar_telemetria()
                    
                    try:
                        await asyncio.wait_for(abort_event.wait(), timeout=t_ciclo * 60.0)
                    except asyncio.TimeoutError:
                        pass 
                        
                    await sys_log.log_event({"tipo": "fin_zona", "zona": str_z})
                        
                    if abort_event.is_set():
                        break
                        
                    # Transición o Soak
                    if c < ciclos - 1 and soak > 0:
                        zonas[z_idx].value(1) # Soak OFF
                        mv.value(0) # Apagar bomba (MV) durante remojo
                        estado_riego = "REMOJANDO"
                        duracion_ciclo_actual = int(soak * 60)
                        ts_inicio_ciclo = time.time()
                        telemetria_extra.update({
                            "remojo_actual": c + 1
                        })
                        await enviar_telemetria()
                        try:
                            await asyncio.wait_for(abort_event.wait(), timeout=soak * 60.0)
                        except:
                            pass
                    
                if abort_event.is_set():
                    break
                    
                # Transición hidráulica entre zonas
                if idx < len(zonas_prog) - 1:
                    modo_bomba = config_data.get("modo_bomba", True)
                    next_z_str = zonas_prog[idx + 1]
                    next_z_idx = obtener_num_zona(next_z_str) - 1
                    
                    if next_z_idx < len(zonas) and next_z_idx >= 0:
                        estado_riego = "TRANSICION_ZONAS"
                        duracion_ciclo_actual = 2
                        ts_inicio_ciclo = time.time()
                        await enviar_telemetria()
                        
                        if modo_bomba:
                            # Overlap
                            zonas[next_z_idx].value(0)
                            await asyncio.sleep(2)
                            zonas[z_idx].value(1)
                        else:
                            # Pause
                            zonas[z_idx].value(1)
                            await asyncio.sleep(2)
                            zonas[next_z_idx].value(0)
                        estado_riego = "REGANDO"
                    else:
                        zonas[z_idx].value(1)
                else:
                    # Ultima zona
                    zonas[z_idx].value(1)

            # Fin del programa
            apagar_todo()
            estado_riego = "IDLE"
            telemetria_extra = {}
            await sys_log.log_event({"tipo": "fin_prog", "prog": programa_activo.get("nombre", "Manual")})
            await enviar_telemetria()
            
            # Ejecutar reinicio diferido seguro si fue programado
            if reinicio_pendiente:
                print("[CORE] Ejecutando reinicio diferido seguro...")
                await asyncio.sleep(2)
                machine.reset()


async def tarea_planificador():
    global ultimo_arranque_minuto
    while True:
        if estado_riego == "IDLE":
            tiene_lluvia = es_sensor_lluvia_activo_y_detectando()
            tiene_secado = (time.time() < config_data.get("timestamp_sensor_lluvia_clear", 0))
            tiene_retraso_manual = (time.time() < config_data.get("timestamp_rain_delay", 0))
            
            if tiene_lluvia or tiene_secado or tiene_retraso_manual:
                await asyncio.sleep(10)
                continue
                
            t = get_time() 
            hora_str = f"{t[3]:02d}:{t[4]:02d}"
            dia_sem = t[6] + 1 
            id_minuto = f"{t[0]}_{t[1]}_{t[2]}_{t[3]}_{t[4]}"
            
            prog_keys = list(config_data.get("programas", {}).keys())
            prog_keys.sort()
            
            for pk in prog_keys:
                prog = config_data["programas"][pk]
                if not prog.get("activo", False):
                    continue
                if dia_sem not in prog.get("dias_semana", []):
                    continue
                if hora_str in prog.get("horas_arranque", []):
                    if ultimo_arranque_minuto != id_minuto:
                        print(f"Lanzando programa {pk}")
                        ultimo_arranque_minuto = id_minuto
                        await cola_programas.put(prog)
                        break # Salir para procesar este programa
        else:
            if es_sensor_lluvia_activo_y_detectando():
                print("LLUVIA DETECTADA, ABORTANDO")
                await sys_log.log_event({"tipo": "error", "msg": "Aborto por lluvia física"})
                abort_event.set()
                
        await asyncio.sleep(10)

async def tarea_actualizar_temperatura():
    global _cached_temp
    while True:
        if reloj_rtc:
            try:
                raw_t = reloj_rtc.temperature()
                offset = config_data.get("calibracion_temp", 0.0)
                _cached_temp = round(raw_t + offset, 1)
            except Exception as e:
                print("[CORE] Error leyendo temp RTC en background:", e)
        # Actualizar cada 5 minutos
        await asyncio.sleep(300)

async def tarea_monitoreo_lluvia():
    global rain_sensor, estado_riego
    if not rain_sensor:
        return

    ultimo_estado_detectado = es_sensor_lluvia_activo_y_detectando()
    ultimo_evento_ts = 0        # Timestamp del último evento que generó I/O (flash + MQTT)
    MIN_INTERVALO_EVENTO_S = 5  # Mínimo 5s entre eventos para evitar avalancha

    while True:
        try:
            # Comprobar si el retraso de secado activo ha expirado naturalmente
            ts_clear = config_data.get("timestamp_sensor_lluvia_clear", 0)
            if ts_clear > 0 and time.time() >= ts_clear:
                print("[RAIN] Retraso de secado expirado naturalmente. Liberando pausa.")
                config_data["timestamp_sensor_lluvia_clear"] = 0
                await guardar_configuracion()
                await sys_log.log_event({"tipo": "sensor_lluvia", "estado": "fin_secado"})
                await enviar_telemetria()

            estado_actual_detectado = es_sensor_lluvia_activo_y_detectando()
            if estado_actual_detectado != ultimo_estado_detectado:
                # Debounce: esperar 2s y reconfirmar.
                await asyncio.sleep_ms(2000)
                debounced = es_sensor_lluvia_activo_y_detectando()
                if debounced == estado_actual_detectado:
                    ultimo_estado_detectado = estado_actual_detectado
                    ahora = time.time()

                    # Si el sensor cambió muy rápido, actualizar el estado interno
                    # pero NO hacer I/O (flash + MQTT) hasta que pase el intervalo mínimo.
                    if ahora - ultimo_evento_ts < MIN_INTERVALO_EVENTO_S:
                        print(f"[RAIN] Cambio de sensor ignorado (demasiado rápido, esperar {MIN_INTERVALO_EVENTO_S}s).")
                        if estado_actual_detectado and estado_riego not in ("IDLE", "FALLO_CORRIENTE"):
                            print("[RAIN] Abortando riego por lluvia (evento rápido).")
                            abort_event.set()
                        await asyncio.sleep(1)
                        continue

                    ultimo_evento_ts = ahora

                    if estado_actual_detectado:
                        print(f"[RAIN] Lluvia detectada física (Tipo: {config_data.get('sensor_lluvia_tipo', 'NA')}).")
                        config_data["timestamp_sensor_lluvia_clear"] = 0
                        await guardar_configuracion()

                        await sys_log.log_event({"tipo": "sensor_lluvia", "estado": "detectada"})
                        # Si está regando, abortar inmediatamente
                        if estado_riego not in ("IDLE", "FALLO_CORRIENTE"):
                            print("[RAIN] Abortando riego por sensor de lluvia activo.")
                            await sys_log.log_event({"tipo": "error", "msg": "Aborto por lluvia física"})
                            abort_event.set()
                        # Forzar envío de telemetría para actualizar la interfaz
                        await enviar_telemetria()
                    else:
                        print("[RAIN] Sensor de lluvia despejado/seco.")
                        delay_horas = config_data.get("sensor_lluvia_delay_horas", 0)
                        if delay_horas > 0:
                            config_data["timestamp_sensor_lluvia_clear"] = time.time() + (delay_horas * 3600)
                            print(f"[RAIN] Iniciando retraso de secado por {delay_horas} horas.")
                            await sys_log.log_event({"tipo": "sensor_lluvia", "estado": "secado", "horas": delay_horas})
                        else:
                            config_data["timestamp_sensor_lluvia_clear"] = 0
                            await sys_log.log_event({"tipo": "sensor_lluvia", "estado": "despejado"})

                        await guardar_configuracion()
                        await enviar_telemetria()
        except Exception as e:
            print("[RAIN] Error en tarea_monitoreo_lluvia:", e)

        await asyncio.sleep(1)  # Polling cada segundo


async def iniciar_tareas():
    global abort_event
    if abort_event is None:
        abort_event = asyncio.Event()
    await init_hardware()
    asyncio.create_task(tarea_monitoreo_corriente())
    asyncio.create_task(tarea_reset_emergencia())
    asyncio.create_task(ejecutar_riego())
    asyncio.create_task(tarea_planificador())
    asyncio.create_task(tarea_actualizar_temperatura())
    asyncio.create_task(tarea_monitoreo_lluvia())
    print("Core Riego Inicializado.")

async def enviar_respuesta_config(origen="ALL"):
    """Envía la configuración actual al cliente optimizando RAM y fragmentando payloads para BLE y MQTT."""
    gc.collect()
    resp = config_data.copy()
    resp["ssid"] = "Desconocido"
    try:
        with open("wifi_config.json", "r") as f:
            creds = json.load(f)
            resp["ssid"] = creds.get("ssid", "Desconocido")
    except:
        pass

    campos_basicos = ["config_version", "max_zonas", "modo_bomba", "calibracion_temp",
                      "timestamp_rain_delay", "ssid", "sensor_lluvia_delay_horas",
                      "timestamp_sensor_lluvia_clear", "sensor_lluvia_activo", "sensor_lluvia_tipo"]
    resp_base = {k: resp[k] for k in campos_basicos if k in resp}
    await tx_queue.put({"tipo": "CONFIG", "data": resp_base, "_destino": origen})

    for prog_id, prog_data in resp.get("programas", {}).items():
        await tx_queue.put({
            "tipo": "CONFIG_PROG",
            "prog_id": prog_id,
            "data": prog_data,
            "_destino": origen
        })
        await asyncio.sleep_ms(20)
    gc.collect()

async def procesar_comando(cmd_dict):
    """Interfaz RX para MQTT y BLE"""
    global reinicio_pendiente, _cached_temp
    gc.collect()
    print(f"[CORE] Procesando comando: {cmd_dict}")
    token_recibido = cmd_dict.get("token")
    origen = cmd_dict.get("_origen", "ALL")  # Asignado aquí para que esté disponible en todos los bloques

    if config_data.get("token_acceso") is None:
        if cmd_dict.get("comando") == "INIT_TOKEN":
            config_data["token_acceso"] = token_recibido
            config_data["config_version"] = 1
            await guardar_configuracion()
            await sys_log.log_event({"tipo": "seguridad", "msg": "Token inicializado"})
            
            # Reinicio seguro diferido
            if estado_riego == "IDLE" or estado_riego == "FALLO_CORRIENTE":
                await asyncio.sleep(1)
                machine.reset()
            else:
                reinicio_pendiente = True
                print("[CORE] Riego activo. Reinicio diferido para INIT_TOKEN.")
        else:
            # Fix: en lugar de descartar silenciosamente, notificar al cliente
            # que el dispositivo necesita inicialización con INIT_TOKEN.
            print("[CORE] Dispositivo sin token. Enviando NEED_INIT al cliente.")
            await tx_queue.put({"tipo": "NEED_INIT", "_destino": origen})
        return
        
    if token_recibido != config_data.get("token_acceso"):
        print("Token inválido")
        await sys_log.log_event({"tipo": "alerta", "msg": "Token inválido en comando"})
        await tx_queue.put({"tipo": "AUTH_ERROR"})
        return
        
    cmd = cmd_dict.get("comando")
    
    if cmd == "GET_STATE":
        await enviar_telemetria()
        
    elif cmd == "GET_CONFIG":
        await enviar_respuesta_config(origen)
        
    elif cmd == "GET_TEMP":
        # Devolver inmediatamente el valor cacheado en RAM sin bloquear la cola de comandos
        await tx_queue.put({"tipo": "TEMP", "data": _cached_temp, "_destino": origen})
        
    elif cmd == "GET_LOGS":
        lines = []
        if origen == "BLE":
            lines = sys_log.logs_ram.copy()
        else:
            try:
                gc.collect()
                buffer = []
                for file_path in (sys_log.LOG_FILE_OLD, sys_log.LOG_FILE):
                    try:
                        with open(file_path, "r") as f:
                            for line in f:
                                if line.strip():
                                    buffer.append(line.strip())
                                    if len(buffer) > 15:
                                        buffer.pop(0)
                    except OSError:
                        pass
                for l in buffer:
                    try:
                        lines.append(json.loads(l))
                    except:
                        pass
            except Exception as e:
                print("Error GET_LOGS:", e)
        await tx_queue.put({"tipo": "LOGS_END", "data": lines, "_destino": origen})
        gc.collect()

    elif cmd == "CLEAR_HISTORY":
        await sys_log.limpiar_historial()
        await tx_queue.put({"tipo": "LOGS", "data": []})
        
    elif cmd == "SYNC_RTC":
        try:
            ts = cmd_dict.get("timestamp", 0)
            if ts > 0 and reloj_rtc:
                mpy_ts = int(ts) - 946684800
                # Fix #7: Validar que el timestamp corresponde a una fecha razonable.
                # 631152000 es el epoch MicroPython (Y2K) equivalente a 2020-01-01 UTC.
                # Esto descarta timestamps nulos, negativos o de años anteriores a 2020.
                if mpy_ts > 631152000:
                    t = time.localtime(mpy_ts)
                    reloj_rtc.set_time(t)
                    await sys_log.log_event({"tipo": "info", "msg": "RTC Sincronizado por App"})
                else:
                    print("[RTC] Timestamp inválido recibido:", ts)
        except Exception as e:
            print("Error sync rtc:", e)
 
    elif cmd == "FACTORY_RESET":
        config_data["token_acceso"] = None
        config_data["config_version"] = 0
        await guardar_configuracion()
        try:
            os.remove(sys_log.LOG_FILE)
        except:
            pass
            
        if estado_riego == "IDLE" or estado_riego == "FALLO_CORRIENTE":
            await asyncio.sleep(1)
            machine.reset()
        else:
            reinicio_pendiente = True
            print("[CORE] Riego activo. Reinicio diferido para FACTORY_RESET.")
            await tx_queue.put({"tipo": "ACK", "status": "DEFERRED", "_destino": origen})
        
    elif cmd == "config_wifi":
        try:
            s = str(cmd_dict.get("ssid", "")).strip()
            p = str(cmd_dict.get("pass", "")).strip()
            with open("wifi_config.json", "w") as f:
                json.dump({"ssid": s, "pass": p}, f)
            print("WiFi configurado en flash.")
            await sys_log.log_event({"tipo": "info", "msg": "Nuevas credenciales WiFi recibidas"})
            
            if estado_riego == "IDLE" or estado_riego == "FALLO_CORRIENTE":
                await asyncio.sleep(1)
                machine.reset()
            else:
                reinicio_pendiente = True
                print("[CORE] Riego activo. Reinicio diferido para config_wifi.")
                await tx_queue.put({"tipo": "ACK", "status": "DEFERRED", "_destino": origen})
        except Exception as e:
            print("Error wifi config:", e)
        
    elif cmd == "RAIN_DELAY":
        dias = cmd_dict.get("dias", 1)
        if dias <= 0:
            config_data["timestamp_rain_delay"] = 0
            if config_data.get("timestamp_sensor_lluvia_clear", 0) > 0:
                await sys_log.log_event({"tipo": "sensor_lluvia", "estado": "fin_secado"})
            config_data["timestamp_sensor_lluvia_clear"] = 0
        else:
            config_data["timestamp_rain_delay"] = time.time() + (dias * 86400)
            
        # Incremento local de versión
        config_data["config_version"] = config_data.get("config_version", 0) + 1
        await guardar_configuracion()
        
        if estado_riego != "IDLE":
            abort_event.set()
            await asyncio.sleep_ms(200)
        
        # Enviar respuesta CONFIG compacta al origen para ahorrar memoria y evitar recursividad
        await tx_queue.put({
            "tipo": "CONFIG",
            "data": {
                "config_version": config_data["config_version"],
                "timestamp_rain_delay": config_data["timestamp_rain_delay"],
                "timestamp_sensor_lluvia_clear": config_data.get("timestamp_sensor_lluvia_clear", 0)
            },
            "_destino": origen
        })
        await enviar_telemetria()
            
    elif cmd == "RIEGO_MANUAL":
        prog = {
            "nombre": "Manual",
            "zonas": cmd_dict.get("zonas", {})
        }
        if estado_riego != "IDLE":
            abort_event.set()
            await asyncio.sleep(2.5) 
        await cola_programas.put(prog)
        
    elif cmd == "RIEGO_PROGRAMA":
        prog_id = cmd_dict.get("prog_id")
        if prog_id and "programas" in config_data and prog_id in config_data["programas"]:
            if estado_riego != "IDLE":
                abort_event.set()
                await asyncio.sleep(2.5)
            await cola_programas.put(config_data["programas"][prog_id])
        else:
            # Programa no encontrado localmente: responder con la config actual para que
            # la app detecte el desajuste de versión y dispare el re-sync automático.
            print(f"[CORE] Programa '{prog_id}' no encontrado en config local (v{config_data.get('config_version',0)}). Enviando CONFIG para re-sync.")
            await enviar_respuesta_config(origen)

    elif cmd == "CANCELAR_RIEGO":
        if estado_riego != "IDLE":
            abort_event.set()
            
    elif cmd == "UPDATE_CONFIG":
        config_recibida = cmd_dict.get("config", {})
        version_recibida = config_recibida.get("config_version", 0)
        version_local = config_data.get("config_version", 0)
        
        if version_recibida > version_local:
             print(f"[CORE] Aceptando config versión {version_recibida} (Local: {version_local})")
             # Preservar nombres_zonas si existían localmente
             if "nombres_zonas" in config_data and "nombres_zonas" not in config_recibida:
                 config_recibida["nombres_zonas"] = config_data["nombres_zonas"]
             for k, v in config_recibida.items():
                 config_data[k] = v
             await guardar_configuracion()
             if reloj_rtc:
                 try:
                     raw_t = reloj_rtc.temperature()
                     offset = config_data.get("calibracion_temp", 0.0)
                     _cached_temp = round(raw_t + offset, 1)
                     await tx_queue.put({"tipo": "TEMP", "data": _cached_temp, "_destino": origen})
                 except: pass
             await tx_queue.put({"tipo": "ACK_CFG", "v": version_recibida, "_destino": origen})
        else:
             print(f"[CORE] Rechazando config obsoleta {version_recibida} (Local: {version_local})")
             await enviar_respuesta_config(origen)
 
    elif cmd == "UPDATE_PROGRAMA":
        prog_id = cmd_dict.get("prog_id")
        prog_data = cmd_dict.get("prog_data")
        if prog_id and prog_data:
            # Fix #9: Normalizar claves de zonas a formato "Z1".."Z8" antes de guardar.
            # La PWA ya hace esta conversión en sendCmd(), pero se normaliza aquí también
            # como capa defensiva ante cualquier ruta alternativa de entrada de datos.
            if "zonas" in prog_data:
                zonas_norm = {}
                for z_key, z_val in prog_data["zonas"].items():
                    norm_key = str(z_key).upper()
                    if not norm_key.startswith("Z"):
                        norm_key = "Z" + norm_key
                    zonas_norm[norm_key] = z_val
                prog_data["zonas"] = zonas_norm

            if "programas" not in config_data:
                config_data["programas"] = {}
            config_data["programas"][prog_id] = prog_data

            # Incremento local de versión
            config_data["config_version"] = config_data.get("config_version", 0) + 1
            await guardar_configuracion()
            # Enviar respuesta CONFIG compacta al origen para ahorrar memoria y evitar recursividad
            await tx_queue.put({
                "tipo": "CONFIG",
                "data": {
                    "config_version": config_data["config_version"],
                    "programas": config_data.get("programas", {})
                },
                "_destino": origen
            })

