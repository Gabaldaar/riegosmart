import machine
import time
import uasyncio as asyncio
import network
import json
import binascii

# Importar el nuevo core
import riego_core
import sys_log

try:
    from umqtt.simple import MQTTClient
except ImportError:
    from simple import MQTTClient

# Variables Globales
wifi_conectado = False
mqtt_client = None

# Constantes Red
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883

async def main():
    # Cargar config de riego y arrancar tareas core
    await riego_core.cargar_configuracion()
    await riego_core.iniciar_tareas()
    await sys_log.log_event({"tipo": "info", "msg": "Sistema iniciado"})
    
    # Iniciar tareas de red
    asyncio.create_task(tarea_led())
    asyncio.create_task(mantener_conexion_wifi())
    asyncio.create_task(escuchar_comandos_mqtt())
    asyncio.create_task(procesar_cola_ble())
    asyncio.create_task(tarea_tx_queue())
    
    # Watchdog Timer (8 segundos)
    try:
        wdt = machine.WDT(timeout=8000)
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
# MQTT CALLBACK Y CONEXIÓN
# ======================================================================

def mqtt_callback(topic, msg):
    try:
        payload = json.loads(msg.decode('utf-8'))
        # Pasar comando a riego_core asíncronamente
        asyncio.create_task(riego_core.procesar_comando(payload))
    except Exception as e:
        print("MQTT Callback Error:", e)

def conectar_mqtt():
    global mqtt_client
    if not wifi_conectado:
        return False
        
    try:
        if mqtt_client:
            mqtt_client.disconnect()
    except:
        pass
        
    try:
        # Client ID independiente
        import urandom
        client_id = f"riego_{riego_core.chip_id}_{urandom.getrandbits(16)}"
        
        mqtt_client = MQTTClient(
            client_id,
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60
        )
        
        mqtt_client.set_callback(mqtt_callback)
        mqtt_client.connect(clean_session=True)
        
        # Suscribir al topic seguro si tenemos token
        topic_hash = riego_core.calcular_hash_seguro()
        if topic_hash:
            topic_sub = f"riego/{topic_hash}/cmd"
            mqtt_client.subscribe(topic_sub)
            print(f"MQTT Conectado y Suscrito a {topic_sub}")
        else:
            print("MQTT Conectado pero no hay TOKEN (No suscrito a cmd)")
            
        return True
    except Exception as e:
        print("Error conectando MQTT:", e)
        mqtt_client = None
        return False

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
                    mqtt_client = None
            except Exception as e:
                mqtt_client = None
        await asyncio.sleep(0.5)

# ======================================================================
# WIFI Y BLE HYBRID LOGIC
# ======================================================================

async def conectar_wifi():
    global wifi_conectado
    try:
        with open("wifi_config.json", "r") as f:
            cred = json.load(f)
            ssid = cred.get("ssid", "")
            password = cred.get("pass", "")
    except:
        return False
        
    if not ssid:
        return False
        
    print(f"Conectando a Wi-Fi: {ssid}...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    try:
        wlan.disconnect()
    except:
        pass
    await asyncio.sleep(0.2)
    
    wlan.connect(ssid, password)
    for _ in range(30):
        if wlan.isconnected():
            wifi_conectado = True
            print("Wi-Fi Conectado.", wlan.ifconfig())
            
            # Sincronizar NTP y RTC DS3231
            try:
                import ntptime
                ntptime.settime() # Sincroniza RTC interno del ESP32 a UTC
                if riego_core.reloj_rtc:
                    import time
                    # UTC-3 (Argentina) = -10800 segundos
                    local_now = time.time() - 10800 
                    t = time.localtime(local_now)
                    riego_core.reloj_rtc.set_time(t)
                    print("RTC DS3231 sincronizado por NTP a UTC-3.")
            except Exception as e:
                print("Error NTP sync:", e)
                
            return True
        await asyncio.sleep(0.5)
    return False

async def mantener_conexion_wifi():
    global wifi_conectado, mqtt_client
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    from ble_service import start_ble_service, stop_ble_service, is_ble_connected
    ble_activo = True
    
    # Arranca BLE al inicio
    await start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")
    
    while True:
        if wifi_conectado and not wlan.isconnected():
            wifi_conectado = False
            mqtt_client = None
            
        if not wifi_conectado:
            # Si no hay WiFi, intentamos reconectar
            # Pero primero verificamos si siquiera hay credenciales
            tiene_creds = False
            try:
                with open("wifi_config.json", "r") as f:
                    c = json.load(f)
                    if c.get("ssid"):
                        tiene_creds = True
            except:
                pass
                
            if tiene_creds:
                # Intentar conectar a WiFi sin detener BLE
                if await conectar_wifi():
                    conectar_mqtt()
            else:
                # No hay credenciales, mantener BLE activo siempre
                if not ble_activo:
                    await start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")
                    ble_activo = True
        else:
            if mqtt_client is None:
                conectar_mqtt()
            if not ble_activo:
                await start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")
                ble_activo = True
                
        await asyncio.sleep(15 if not wifi_conectado else 10)

async def procesar_cola_ble():
    """Lee comandos de la cola BLE y los pasa a riego_core"""
    from ble_service import rx_queue
    while True:
        cmd_dict = await rx_queue.get()
        await riego_core.procesar_comando(cmd_dict)

async def tarea_tx_queue():
    """Lee de tx_queue en riego_core y transmite por MQTT y BLE"""
    while True:
        try:
            msg_dict = await riego_core.tx_queue.get()
            print(f"[MAIN_TX] Desencolado para enviar: {msg_dict.get('tipo')}")
            
            # Enviar via MQTT
            if mqtt_client and wifi_conectado:
                try:
                    topic_hash = riego_core.calcular_hash_seguro()
                    if topic_hash:
                        topic_pub = f"riego/{topic_hash}/telemetry"
                        json_str = json.dumps(msg_dict)
                        mqtt_client.publish(topic_pub, json_str)
                except Exception as e:
                    print("Error publicando MQTT TX:", e)
                    
            # Enviar via BLE
            from ble_service import send_json_async, is_ble_connected
            if is_ble_connected():
                await send_json_async(msg_dict)
                
        except Exception as e:
            print("Error en tarea_tx_queue:", e)

# Iniciar Loop Principal
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Programa terminado")
