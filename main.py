import machine
import time

# Oportunidad de Ctrl+C (3 segundos con LED azul encendido) antes de arrancar
try:
    _led_azul = machine.Pin(2, machine.Pin.OUT)
    _led_azul.value(1) # Encender LED Azul
    print("Iniciando... Tienes 3 segundos para pulsar Ctrl+C y detener el script.")
    time.sleep(3)
    _led_azul.value(0) # Apagar
except Exception as e:
    pass

import uasyncio as asyncio
import network
import json
import binascii
import gc

# Importar el core y log
import riego_core
import sys_log

try:
    from umqtt.simple import MQTTClient
except ImportError:
    from simple import MQTTClient

# Variables Globales de Red y Estados
STATE_INIT = 0
STATE_BLE_ONLY = 1
STATE_WIFI_CONNECTING = 2
STATE_WIFI_ONLINE = 3
STATE_FALLBACK_BLE = 4

current_state = STATE_INIT
wifi_conectado = False
mqtt_client = None
mqtt_loop_task = None
mqtt_lock = asyncio.Lock()
wdt = None
ventana_fallback_ble_s = 180  # 3 minutos de ventana de BLE offline antes de reintentar WiFi

# Constantes Red
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
# Fix #2: El hash del topic MQTT se calcula como SHA256(chip_id[-4:] + token).
# En main.py el nombre BLE se construye como f"Riego_{chip_id[-4:]}" por lo que
# la PWA extrae exactamente los mismos 4 caracteres para calcular el hash.
# Ambos extremos son consistentes por construcción.

async def main():
    # Cargar config de riego y arrancar tareas core
    await riego_core.cargar_configuracion()
    await riego_core.iniciar_tareas()
    await sys_log.log_event({"tipo": "info", "msg": "Sistema iniciado"})
    
    # Iniciar tareas de red
    asyncio.create_task(tarea_led())
    asyncio.create_task(gestionar_interfaces_network())
    asyncio.create_task(procesar_cola_ble())
    asyncio.create_task(tarea_tx_queue())
    
    # Watchdog Timer (30 segundos para soportar bloqueos de red)
    global wdt
    try:
        wdt = machine.WDT(timeout=30000)
    except:
        wdt = None
    
    # Loop infinito
    while True:
        if wdt:
            wdt.feed()
        await asyncio.sleep(2)

async def tarea_led():
    led = machine.Pin(2, machine.Pin.OUT)
    while True:
        from ble_service import is_ble_connected
        ble_conectado = is_ble_connected()
        
        # Verificar retraso lluvia
        retraso = False
        if (riego_core.rain_sensor and riego_core.rain_sensor.value() == 0) or (time.time() < riego_core.config_data.get("timestamp_rain_delay", 0)):
            retraso = True
            
        estado = riego_core.estado_riego
        
        if retraso:
            patron = [(1, 2000), (0, 200)]
        elif estado != "IDLE":
            if wifi_conectado:
                patron = [(1, 500), (0, 500)]
            else:
                patron = [(1, 1000), (0, 200)]
        else:
            if wifi_conectado:
                patron = [(1, 200), (0, 4000)]
            elif ble_conectado:
                patron = [(1, 200), (0, 200), (1, 200), (0, 4000)]
            else:
                patron = [(1, 100), (0, 100)] # Ninguno conectado
                
        for valor, duracion in patron:
            led.value(valor)
            await asyncio.sleep_ms(duracion)

# ======================================================================
# MQTT CALLBACK Y CONEXIÓN ASÍNCRONA
# ======================================================================

def mqtt_callback(topic, msg):
    try:
        payload = json.loads(msg.decode('utf-8'))
        payload['_origen'] = 'MQTT'
        # Pasar comando a riego_core asíncronamente
        asyncio.create_task(riego_core.procesar_comando(payload))
    except Exception as e:
        print("[MQTT_CB] Error:", e)

async def conectar_mqtt_async():
    global mqtt_client, mqtt_loop_task
    if not wifi_conectado:
        return False
        
    try:
        async with mqtt_lock:
            if mqtt_client:
                try:
                    mqtt_client.disconnect()
                except:
                    pass
                mqtt_client = None
            
        import urandom
        client_id = f"riego_{riego_core.chip_id}_{urandom.getrandbits(16)}"
        
        mqtt_client = MQTTClient(
            client_id,
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60
        )
        
        mqtt_client.set_callback(mqtt_callback)
        
        # Conectar al broker. umqtt.simple usa usocket internamente.
        # No hacemos monkey-patching de socket.socket (no portable en MicroPython moderno).
        # En su lugar aplicamos el timeout al socket ya creado, justo después del connect().
        if wdt: wdt.feed()
        mqtt_client.connect(clean_session=True)
        if wdt: wdt.feed()
        
        # Aplicar timeout de operación sobre el socket abierto por umqtt
        if hasattr(mqtt_client, 'sock') and mqtt_client.sock:
            try:
                mqtt_client.sock.settimeout(3.0)
            except:
                pass  # Algunos backends no soportan settimeout; se ignora de forma segura

            
        # FIX: Wrapper para escrituras seguras en MicroPython
        class SockWrapper:
            def __init__(self, s): 
                self.s = s
            def read(self, *a): 
                return self.s.read(*a)
            def write(self, buf, *args):
                if args: 
                    buf = buf[:args[0]]
                if type(buf) is str: 
                    buf = buf.encode()
                t = 0
                while t < len(buf):
                    r = self.s.write(buf[t:])
                    if r is None or r <= 0:
                        raise OSError("[SockWrapper] Write returned None/0")
                    t += r
                return t
            def setblocking(self, b): 
                self.s.setblocking(b)
            def settimeout(self, to): 
                if hasattr(self.s, 'settimeout'): 
                    self.s.settimeout(to)
            def close(self): 
                self.s.close()
                
        if hasattr(mqtt_client, 'sock') and mqtt_client.sock:
            mqtt_client.sock = SockWrapper(mqtt_client.sock)
            mqtt_client.sock.settimeout(3.0)
            
        topic_hash = riego_core.calcular_hash_seguro()
        if topic_hash:
            topic_sub = f"riego/{topic_hash}/cmd"
            async with mqtt_lock:
                mqtt_client.subscribe(topic_sub)
            print(f"[MQTT] Conectado y Suscrito a {topic_sub}")
            
            if mqtt_loop_task:
                try:
                    mqtt_loop_task.cancel()
                except:
                    pass
            mqtt_loop_task = asyncio.create_task(loop_mqtt_escucha())
            return True
        else:
            print("[MQTT] Conectado pero no hay TOKEN (No suscrito a cmd)")
            return True
    except Exception as e:
        print("[MQTT] Error conectando MQTT asíncronamente:", e)
        mqtt_client = None
        return False

async def loop_mqtt_escucha():
    global mqtt_client
    last_ping = time.time()
    while wifi_conectado and mqtt_client is not None:
        try:
            async with mqtt_lock:
                if mqtt_client is not None:
                    mqtt_client.sock.setblocking(False)
                    mqtt_client.check_msg()
            
            if time.time() - last_ping >= 30:
                async with mqtt_lock:
                    if mqtt_client is not None:
                        mqtt_client.ping()
                last_ping = time.time()
        except OSError as e:
            err_code = e.args[0] if e.args else None
            if err_code not in (11, 110, 115, 116):
                print("[MQTT] Error de socket en escucha:", err_code)
                mqtt_client = None
                break
        except Exception as e:
            print("[MQTT] Excepción fatal en loop escucha:", e)
            mqtt_client = None
            break
        await asyncio.sleep_ms(200)

# ======================================================================
# MAQUINA DE ESTADOS Y CONTROL DE INTERFACES RF (NO-COEXISTENCIA)
# ======================================================================

async def conectar_wifi_non_blocking(wlan):
    global wifi_conectado
    try:
        with open("wifi_config.json", "r") as f:
            cred = json.load(f)
            ssid = str(cred.get("ssid", "")).strip()
            password = str(cred.get("pass", "")).strip()
    except:
        return False
        
    if not ssid:
        return False
        
    print(f"[WIFI] Conectando a Wi-Fi: {ssid}...")
    wlan.connect(ssid, password)
    
    # Intentar conexión durante 15 segundos máximo asíncronamente
    for _ in range(30):
        if wdt: wdt.feed()
        if wlan.isconnected():
            wifi_conectado = True
            print("[WIFI] Conectado exitosamente.", wlan.ifconfig())
            
            # NTP Sync rápido
            try:
                if wdt: wdt.feed()
                import ntptime
                ntptime.host = "pool.ntp.org"
                ntptime.settime()
                if wdt: wdt.feed()
                if riego_core.reloj_rtc:
                    local_now = time.time() - 10800 # UTC-3
                    t = time.localtime(local_now)
                    riego_core.reloj_rtc.set_time(t)
                    print("[NTP] RTC DS3231 sincronizado por NTP.")
            except Exception as e:
                print("[NTP] Error NTP sync:", e)
                
            return True
        await asyncio.sleep_ms(500)
    return False

async def gestionar_interfaces_network():
    global current_state, wifi_conectado, mqtt_client
    wlan = network.WLAN(network.STA_IF)
    
    while True:
        # Evaluar credenciales locales
        tiene_creds = False
        try:
            with open("wifi_config.json", "r") as f:
                c = json.load(f)
                if c.get("ssid"):
                    tiene_creds = True
        except:
            pass

        if current_state == STATE_INIT:
            if tiene_creds:
                print("[NET] Credenciales detectadas. Conectando WiFi...")
                current_state = STATE_WIFI_CONNECTING
            else:
                print("[NET] Sin credenciales. Iniciando BLE de configuración...")
                current_state = STATE_BLE_ONLY

        elif current_state == STATE_BLE_ONLY:
            # Exclusión mutua: WiFi OFF, BLE ON
            if wlan.active():
                wlan.active(False)
                gc.collect()
            from ble_service import start_ble_service
            await start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")
            
            # Fix #4: Si se detectan credenciales WiFi, transicionar de inmediato
            # sin ejecutar el sleep de 5s para evitar que el estado lógico y el
            # hardware (BLE activo) estén desincronizados durante ese período.
            if tiene_creds:
                current_state = STATE_WIFI_CONNECTING
                continue  # Re-iterar el while True inmediatamente
            await asyncio.sleep(5)

        elif current_state == STATE_WIFI_CONNECTING:
            # Exclusión mutua: BLE OFF, WiFi ON
            from ble_service import stop_ble_service
            await stop_ble_service()
            wlan.active(True)
            gc.collect()
            
            success = await conectar_wifi_non_blocking(wlan)
            if success:
                current_state = STATE_WIFI_ONLINE
            else:
                print("[NET] Error conectando WiFi. Yendo a Fallback BLE...")
                current_state = STATE_FALLBACK_BLE

        elif current_state == STATE_WIFI_ONLINE:
            # Monitorear enlace WiFi y MQTT
            if not wlan.isconnected():
                print("[NET] Link WiFi caído. Yendo a Fallback BLE.")
                wifi_conectado = False
                mqtt_client = None
                current_state = STATE_FALLBACK_BLE
            else:
                if mqtt_client is None:
                    await conectar_mqtt_async()
            # Fix #9 MQTT: Polling dinámico — 1s si MQTT está caído (reconexión rápida),
            # 5s si todo está estable para no saturar el event-loop.
            await asyncio.sleep(1 if (current_state == STATE_WIFI_ONLINE and mqtt_client is None) else 5)

        elif current_state == STATE_FALLBACK_BLE:
            # Exclusión mutua: WiFi OFF, BLE ON durante ventana temporal
            print("[NET] Fallback BLE iniciado.")
            wlan.active(False)
            gc.collect()
            
            from ble_service import start_ble_service
            await start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")
            
            # Mantener la ventana temporal de BLE de emergencia
            for _ in range(int(ventana_fallback_ble_s / 5)):
                # Salida anticipada si el estado cambió (por ejemplo, nuevas credenciales)
                if current_state != STATE_FALLBACK_BLE:
                    break
                await asyncio.sleep(5)
                
            # Expiró la ventana, intentar re-conectar WiFi
            if current_state == STATE_FALLBACK_BLE:
                print("[NET] Ventana de fallback expirada. Intentando reconectar WiFi...")
                from ble_service import stop_ble_service
                await stop_ble_service()
                current_state = STATE_WIFI_CONNECTING

async def procesar_cola_ble():
    """Lee comandos de la cola BLE y los pasa a riego_core"""
    from ble_service import rx_queue
    while True:
        cmd_dict = await rx_queue.get()
        if isinstance(cmd_dict, dict):
            cmd_dict['_origen'] = 'BLE'
        await riego_core.procesar_comando(cmd_dict)

async def tarea_tx_queue():
    """Lee de tx_queue en riego_core y transmite por MQTT y BLE"""
    global mqtt_client
    while True:
        try:
            msg_dict = await riego_core.tx_queue.get()
            print(f"[MAIN_TX] Desencolado para enviar: {msg_dict.get('tipo', 'UNKN')}")
            
            destino = msg_dict.get("_destino", "ALL")
            
            if "_destino" in msg_dict:
                del msg_dict["_destino"]
            
            # Transmitir vía BLE
            if destino in ("ALL", "BLE"):
                from ble_service import send_json_async, is_ble_connected
                if is_ble_connected():
                    await send_json_async(msg_dict)
                
            # Transmitir vía MQTT
            if destino in ("ALL", "MQTT"):
                if mqtt_client and wifi_conectado:
                    try:
                        topic_hash = riego_core.calcular_hash_seguro()
                        if topic_hash:
                            topic_pub = f"riego/{topic_hash}/telemetry".encode('utf-8')
                            json_bytes = json.dumps(msg_dict).encode('utf-8')
                            async with mqtt_lock:
                                if mqtt_client is not None:
                                    mqtt_client.publish(topic_pub, json_bytes)
                    except Exception as e:
                        print("[MAIN_TX] Error publicando MQTT:", e)
                        mqtt_client = None
                    
                    await asyncio.sleep_ms(50)
        except Exception as e:
            print("[MAIN_TX] Error general en tarea_tx_queue:", e)
        await asyncio.sleep(0.1)

# Iniciar Loop Principal
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Programa terminado")

