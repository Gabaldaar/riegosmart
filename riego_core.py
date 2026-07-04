import machine
import json
import os
import time
import uasyncio as asyncio
import sys_log
import ds3231
import binascii
import hashlib

CONFIG_FILE = "config_riego.json"
DEFAULT_CONFIG = {
  "max_zonas": 4,
  "modo_bomba": True,
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
_config_lock = asyncio.Lock()

# Mapado de Hardware
MV_PIN = 19
ZONAS_PINS = [18, 23, 26, 27, 25, 32, 33, 14]
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

class AsyncQueue:
    def __init__(self):
        self._queue = []
        self._event = asyncio.Event()

    async def put(self, item):
        self._queue.append(item)
        self._event.set()

    async def get(self):
        while not self._queue:
            self._event.clear()
            await self._event.wait()
        return self._queue.pop(0)

# Variables de Estado
estado_riego = "IDLE"
programa_activo = None
zona_actual_idx = "0"  
cola_programas = AsyncQueue()
tx_queue = AsyncQueue() # Cola para enviar datos (telemetria, config, logs) al movil
abort_event = asyncio.Event()

# Seguimiento para telemetria
ts_inicio_ciclo = 0
duracion_ciclo_actual = 0

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
            with open(CONFIG_FILE + ".tmp", "w") as f:
                json.dump(config_data, f)
            try:
                os.remove(CONFIG_FILE)
            except OSError:
                pass
            os.rename(CONFIG_FILE + ".tmp", CONFIG_FILE)
        except Exception as e:
            print("Error guardando config:", e)

def get_time():
    """Retorna tiempo (año, mes, dia, hora, min, seg, diasemana)"""
    try:
        if reloj_rtc:
            return reloj_rtc.get_time()
    except:
        pass
    return time.localtime()[:7]

async def init_hardware():
    global mv, zonas, rain_sensor, adc, boot_button, reloj_rtc
    
    mv = machine.Pin(MV_PIN, machine.Pin.OUT, value=1)
    
    max_z = min(8, max(1, config_data.get("max_zonas", 4)))
    zonas = []
    for i in range(max_z):
        p = machine.Pin(ZONAS_PINS[i], machine.Pin.OUT, value=1)
        zonas.append(p)
        
    rain_sensor = machine.Pin(RAIN_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    adc = machine.ADC(machine.Pin(ADC_PIN))
    adc.atten(machine.ADC.ATTN_11DB)
    boot_button = machine.Pin(BOOT_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    
    try:
        i2c = machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21))
        reloj_rtc = ds3231.DS3231(i2c)
        print("DS3231 RTC Detectado y cargado.")
    except Exception as e:
        print("Error inicializando RTC:", e)
        reloj_rtc = None

async def enviar_telemetria():
    """ Encola el estado actual para que se transmita a la app """
    if estado_riego == "IDLE":
        t_rest = 0
        t_tot = 1
    else:
        elapsed = time.time() - ts_inicio_ciclo
        t_rest = max(0, duracion_ciclo_actual - int(elapsed))
        t_tot = duracion_ciclo_actual if duracion_ciclo_actual > 0 else 1
        
    await tx_queue.put({
        "tipo": "TELEMETRIA",
        "data": {
            "estado": estado_riego,
            "zona": zona_actual_idx,
            "tiempo_restante": t_rest,
            "tiempo_total": t_tot
        }
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

def apagar_todo():
    mv.value(1)
    for z in zonas:
        z.value(1)

async def ejecutar_riego():
    global estado_riego, programa_activo, abort_event
    global zona_actual_idx, ts_inicio_ciclo, duracion_ciclo_actual
    
    while True:
        if estado_riego == "IDLE" or estado_riego == "FALLO_CORRIENTE":
            programa_activo = await cola_programas.get()
            abort_event.clear()
            estado_riego = "PRESURIZANDO"
            await sys_log.log_event({"tipo": "inicio_prog", "prog": programa_activo.get("nombre", "Manual")})
            await enviar_telemetria()
            
        elif estado_riego == "PRESURIZANDO":
            mv.value(0)
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
            zonas_prog.sort(key=lambda x: int(x))
            
            ajuste = 1.0
            if reloj_rtc:
                try:
                    t = reloj_rtc.get_time() # YYYY, MM, DD, HH, MM, SS, WD, YD
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
                z_idx = int(str_z) - 1
                if z_idx >= len(zonas) or z_idx < 0:
                    continue
                    
                z_config = programa_activo["zonas"][str_z]
                min_base = z_config.get("minutos", 0)
                min_reales = int(min_base * ajuste)
                
                if min_reales < 0.5:
                    continue
                    
                c_min = z_config.get("cycle_min", min_reales)
                ciclos = 1
                if c_min < min_reales and c_min > 0:
                    ciclos = (min_reales // c_min) + (1 if min_reales % c_min != 0 else 0)
                
                soak = z_config.get("soak_min", 0)
                
                for c in range(ciclos):
                    t_ciclo = c_min if c < ciclos - 1 else min_reales - (c_min * c)
                    
                    if abort_event.is_set():
                        break
                        
                    # Encender zona y notificar PWA
                    zonas[z_idx].value(0)
                    zona_actual_idx = str_z
                    duracion_ciclo_actual = int(t_ciclo * 60)
                    ts_inicio_ciclo = time.time()
                    estado_riego = "REGANDO"
                    await sys_log.log_event({"tipo": "inicio_zona", "zona": str_z, "duracion": round(t_ciclo, 1), "prog": programa_activo.get("nombre", "Manual")})
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
                        estado_riego = "REMOJANDO"
                        duracion_ciclo_actual = int(soak * 60)
                        ts_inicio_ciclo = time.time()
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
                    next_z_idx = int(next_z_str) - 1
                    
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
            await sys_log.log_event({"tipo": "fin_prog", "prog": programa_activo.get("nombre", "Manual")})
            await enviar_telemetria()

async def tarea_planificador():
    while True:
        if estado_riego == "IDLE":
            if rain_sensor.value() == 0 or time.time() < config_data.get("timestamp_rain_delay", 0):
                await asyncio.sleep(10)
                continue
                
            t = get_time() 
            hora_str = f"{t[3]:02d}:{t[4]:02d}"
            dia_sem = t[6] + 1 
            
            prog_keys = list(config_data.get("programas", {}).keys())
            prog_keys.sort()
            
            for pk in prog_keys:
                prog = config_data["programas"][pk]
                if not prog.get("activo", False):
                    continue
                if dia_sem not in prog.get("dias_semana", []):
                    continue
                if hora_str in prog.get("horas_arranque", []) and t[5] < 15:
                    print(f"Lanzando programa {pk}")
                    await cola_programas.put(prog)
                    await asyncio.sleep(16)
        else:
            if rain_sensor.value() == 0:
                print("LLUVIA DETECTADA, ABORTANDO")
                await sys_log.log_event({"tipo": "error", "msg": "Aborto por lluvia física"})
                abort_event.set()
                
        await asyncio.sleep(10)

async def iniciar_tareas():
    await init_hardware()
    asyncio.create_task(tarea_monitoreo_corriente())
    asyncio.create_task(tarea_reset_emergencia())
    asyncio.create_task(ejecutar_riego())
    asyncio.create_task(tarea_planificador())
    print("Core Riego Inicializado.")

async def procesar_comando(cmd_dict):
    """Interfaz RX para MQTT y BLE"""
    print(f"[CORE] Procesando comando: {cmd_dict}")
    token_recibido = cmd_dict.get("token")
    if config_data.get("token_acceso") is None:
        if cmd_dict.get("comando") == "INIT_TOKEN":
            config_data["token_acceso"] = token_recibido
            await guardar_configuracion()
            await sys_log.log_event({"tipo": "seguridad", "msg": "Token inicializado"})
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
        resp = config_data.copy()
        resp["ssid"] = "Desconocido"
        try:
            with open("wifi_config.json", "r") as f:
                creds = json.load(f)
                resp["ssid"] = creds.get("ssid", "Desconocido")
        except:
            pass
        await tx_queue.put({"tipo": "CONFIG", "data": resp})
        
    elif cmd == "GET_TEMP":
        temp = "N/A"
        if reloj_rtc:
            try:
                temp = round(reloj_rtc.temperature(), 1)
            except:
                pass
        await tx_queue.put({"tipo": "TEMP", "data": temp})
        
    elif cmd == "GET_LOGS":
        # Leer las últimas N líneas para no saturar memoria
        lines = []
        try:
            with open(sys_log.LOG_FILE, "r") as f:
                content = f.readlines()
                lines = [json.loads(l) for l in content[-50:]]
        except:
            pass
        await tx_queue.put({"tipo": "LOGS", "data": lines})
        
    elif cmd == "CLEAR_HISTORY":
        await sys_log.limpiar_historial()
        await tx_queue.put({"tipo": "LOGS", "data": []})
        
    elif cmd == "SYNC_RTC":
        # Formato esperado: {"comando": "SYNC_RTC", "timestamp": epoch_segs}
        # Nota: El PWA debe enviar los segundos (epoch unix offset timezone si lo desea)
        try:
            ts = cmd_dict.get("timestamp", 0)
            if ts > 0 and reloj_rtc:
                # Ojo: MicroPython usa Y2K epoch (2000-01-01). Unix usa 1970.
                # Si PWA envia Unix Timestamp, restamos 946684800
                mpy_ts = int(ts) - 946684800
                if mpy_ts > 0:
                    t = time.localtime(mpy_ts)
                    # ds3231: set_time((YY, MM, mday, hh, mm, ss, wday, yday))
                    # time.localtime(): (year, month, mday, hour, minute, second, weekday, yearday)
                    reloj_rtc.set_time(t)
                    await sys_log.log_event({"tipo": "info", "msg": "RTC Sincronizado por App"})
        except Exception as e:
            print("Error sync rtc:", e)

    elif cmd == "FACTORY_RESET":
        config_data["token_acceso"] = None
        await guardar_configuracion()
        try:
            os.remove(sys_log.LOG_FILE)
        except:
            pass
        machine.reset()
        
    elif cmd == "config_wifi":
        try:
            with open("wifi_config.json", "w") as f:
                json.dump({"ssid": cmd_dict.get("ssid"), "pass": cmd_dict.get("pass")}, f)
            print("WiFi configurado. Reiniciando equipo para aplicar...")
            await sys_log.log_event({"tipo": "info", "msg": "Nuevas credenciales WiFi recibidas"})
            await asyncio.sleep(1) # dar tiempo al log
            machine.reset()
        except Exception as e:
            print("Error wifi config:", e)
        
    elif cmd == "RAIN_DELAY":
        dias = cmd_dict.get("dias", 1)
        if dias <= 0:
            config_data["timestamp_rain_delay"] = 0
        else:
            config_data["timestamp_rain_delay"] = time.time() + (dias * 86400)
        await guardar_configuracion()
        if estado_riego != "IDLE":
            abort_event.set()
        await procesar_comando({"comando": "GET_CONFIG"})
            
    elif cmd == "RIEGO_MANUAL":
        prog = {
            "nombre": "Manual",
            "zonas": cmd_dict.get("zonas", {})
        }
        if estado_riego != "IDLE":
            abort_event.set()
            await asyncio.sleep(2.5) # dar tiempo a abortar si estaba corriendo
        await cola_programas.put(prog)
        
    elif cmd == "CANCELAR_RIEGO":
        if estado_riego != "IDLE":
            abort_event.set()
            
    elif cmd == "UPDATE_CONFIG":
        for k, v in cmd_dict.get("config", {}).items():
            config_data[k] = v
        await guardar_configuracion()
        await tx_queue.put({"tipo": "CONFIG", "data": config_data})
