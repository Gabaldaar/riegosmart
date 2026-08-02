# network_manager.py - Máquina de estados de red y exclusión mutua WiFi/BLE
# Patrón basado en Dosimat IoT V2 / network_manager.py
# Adaptado para Control de Riego Smart
import network
import time
import json
import gc
import usocket as socket
import uasyncio as asyncio
import riego_core
import sys_log
import ble_service

# Usar la versión local corregida de simple.py para evitar bloqueos síncronos en el socket
from simple import MQTTClient

# ── Estados de Red ────────────────────────────────────────────────────────────
STATE_INIT            = 0
STATE_BLE_ONLY        = 1
STATE_WIFI_CONNECTING = 2
STATE_WIFI_ONLINE     = 3
STATE_FALLBACK_BLE    = 4

current_state  = STATE_INIT
wifi_conectado = False
mqtt_client    = None
mqtt_loop_task = None
mqtt_lock      = asyncio.Lock()
cached_mqtt_ip = None
wdt_ref        = None   # Referencia al WDT de main.py para hacer feed() en esperas largas

# Ventana máxima de Fallback BLE antes de reintentar WiFi.
# ⚑ El contador se REINICIA mientras haya un cliente BLE activo (ver gestionar_interfaces_network).
ventana_fallback_ble_s = 180   # 3 minutos

# ── Constantes MQTT ────────────────────────────────────────────────────────────
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT   = 1883


def get_state_name():
    names = {0: "INIT", 1: "BLE_ONLY", 2: "WIFI_CONNECTING",
             3: "WIFI_ONLINE", 4: "FALLBACK_BLE"}
    return names.get(current_state, "UNKNOWN")


# ======================================================================
# MQTT — CALLBACK Y CONEXIÓN ASÍNCRONA
# ======================================================================

def mqtt_callback(topic, msg):
    try:
        payload = json.loads(msg.decode('utf-8'))
        payload['_origen'] = 'MQTT'
        asyncio.create_task(riego_core.procesar_comando(payload))
    except Exception as e:
        print("[MQTT_CB] Error al procesar mensaje:", e)


async def conectar_mqtt_async():
    global mqtt_client, mqtt_loop_task, cached_mqtt_ip
    if not wifi_conectado:
        return False

    try:
        # Cerrar conexión anterior si existe
        async with mqtt_lock:
            if mqtt_client:
                try:
                    mqtt_client.disconnect()
                except:
                    pass
                mqtt_client = None

        import urandom
        client_id = f"riego_{riego_core.chip_id}_{urandom.getrandbits(16)}"

        broker_host = cached_mqtt_ip if cached_mqtt_ip else MQTT_BROKER
        if cached_mqtt_ip:
            print(f"[MQTT] Usando IP cacheada: {broker_host}")

        print(f"[MQTT] Conectando a {broker_host}:{MQTT_PORT}...")
        if wdt_ref: wdt_ref.feed()

        _client = MQTTClient(client_id, broker_host, port=MQTT_PORT, keepalive=60)
        _client.set_callback(mqtt_callback)
        _client.connect(clean_session=True)

        if wdt_ref: wdt_ref.feed()

        # Aplicar timeout nativo al socket MQTT
        if hasattr(_client, 'sock') and _client.sock:
            try:
                _client.sock.settimeout(3.0)
            except:
                pass

        # Suscribirse al topic de comandos (hash seguro)
        topic_hash = riego_core.calcular_hash_seguro()
        if topic_hash:
            topic_sub = f"riego/{topic_hash}/cmd"
            _client.subscribe(topic_sub)
            print(f"[MQTT] Suscrito a: {topic_sub}")

            # Cachear IP del broker para evitar DNS en reconexiones
            if not cached_mqtt_ip:
                try:
                    addr_info = socket.getaddrinfo(MQTT_BROKER, MQTT_PORT)
                    cached_mqtt_ip = addr_info[0][-1][0]
                    print(f"[MQTT] IP del broker cacheada: {cached_mqtt_ip}")
                except Exception as ex:
                    print("[MQTT] No se pudo cachear IP DNS:", ex)
        else:
            print("[MQTT] Sin token. Conectado sin suscripción a /cmd.")

        async with mqtt_lock:
            mqtt_client = _client

        # Cancelar loop anterior e iniciar uno nuevo
        if mqtt_loop_task:
            try: mqtt_loop_task.cancel()
            except: pass
        mqtt_loop_task = asyncio.create_task(loop_mqtt_escucha())

        # Enviar telemetría inicial para que la app reciba el estado actual
        asyncio.create_task(riego_core.enviar_telemetria())
        return True

    except Exception as e:
        print("[MQTT] Error al establecer conexión:", e)
        if cached_mqtt_ip:
            print("[MQTT] Limpiando IP cacheada por fallo.")
            cached_mqtt_ip = None
        mqtt_client = None
        return False


async def loop_mqtt_escucha():
    """Polling no bloqueante de mensajes MQTT con keep-alive por PING y watchdog de silencio."""
    global mqtt_client
    last_ping = time.time()
    last_recv = time.time()
    MAX_SILENCE_S = 65

    while wifi_conectado and mqtt_client is not None:
        try:
            # Tomar referencia LOCAL sin mantener el lock durante check_msg().
            # check_msg() es síncrono (sin await), por lo que en asyncio cooperativo
            # ninguna otra tarea puede modificar mqtt_client mientras corre.
            _mc = mqtt_client
            got_data = False
            if _mc is not None:
                if wdt_ref: wdt_ref.feed()   # Alimentar WDT antes de posible bloqueo
                got_data = _mc.check_msg()
                if wdt_ref: wdt_ref.feed()   # Alimentar WDT tras check_msg
                if got_data:
                    last_recv = time.time()

            now = time.time()
            if now - last_ping >= 30:
                _mc2 = mqtt_client
                if _mc2 is not None:
                    try:
                        if wdt_ref: wdt_ref.feed()
                        _mc2.ping()
                    except Exception as ep:
                        print("[MQTT] Error enviando PING:", ep)
                        mqtt_client = None
                        break
                last_ping = now
                print("[MQTT] PING enviado al broker.")

            # Watchdog: sin datos por MAX_SILENCE_S → conexión silenciosamente muerta
            if now - last_recv > MAX_SILENCE_S:
                print(f"[MQTT] Sin respuesta por {MAX_SILENCE_S}s. Forzando reconexión.")
                mqtt_client = None
                break

        except OSError as e:
            err_code = e.args[0] if e.args else None
            # EAGAIN / timeout de no-bloqueo son normales, ignorar
            if err_code not in (11, 110, 115, 116):
                print("[MQTT] Desconexión de socket. Código:", err_code)
                async with mqtt_lock:
                    mqtt_client = None
                break
        except Exception as e:
            print("[MQTT] Excepción en loop de escucha:", e)
            async with mqtt_lock:
                mqtt_client = None
            break
        await asyncio.sleep_ms(200)


# ======================================================================
# WIFI — HELPERS
# ======================================================================

async def conectar_wifi_non_blocking(wlan):
    """Conecta a la red WiFi guardada en flash sin bloquear el loop de asyncio."""
    global wifi_conectado
    try:
        with open("wifi_config.json", "r") as f:
            cred = json.load(f)
            ssid     = str(cred.get("ssid", "")).strip()
            password = str(cred.get("pass", "")).strip()
    except:
        print("[WIFI] Sin credenciales configuradas.")
        return False

    if not ssid:
        return False

    print(f"[WIFI] Conectando a AP: {ssid}...")
    wlan.connect(ssid, password)

    # Espera no bloqueante de hasta 15 segundos (30 × 500 ms)
    for _ in range(30):
        if wdt_ref: wdt_ref.feed()
        if wlan.isconnected():
            wifi_conectado = True
            print("[WIFI] Conectado exitosamente.", wlan.ifconfig())

            # Sincronización horaria NTP + DS3231
            try:
                if wdt_ref: wdt_ref.feed()
                import ntptime
                ntptime.host = "pool.ntp.org"
                ntptime.settime()
                if wdt_ref: wdt_ref.feed()
                if riego_core.reloj_rtc:
                    local_now = time.time() - 10800  # Ajuste UTC-3 (Argentina)
                    t = time.localtime(local_now)
                    riego_core.reloj_rtc.set_time(t)
                    print("[NTP] DS3231 sincronizado por NTP.")
            except Exception as e:
                print("[NTP] Error de sincronización:", e)

            return True
        await asyncio.sleep_ms(500)

    print("[WIFI] Timeout al conectar WiFi.")
    return False


def comprobar_internet():
    """Verifica conectividad real (8.8.8.8:53). Robusto ante agotamiento de sockets."""
    try:
        gc.collect()
        s = socket.socket()
        s.settimeout(2.0)
        try:
            if wdt_ref: wdt_ref.feed()
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except:
            try: s.close()
            except: pass
            return False
    except OSError as e:
        print("[NET] comprobar_internet error:", e)
        return False


def tiene_credenciales_wifi():
    """Comprueba si existe un archivo wifi_config.json con SSID válido."""
    try:
        with open("wifi_config.json", "r") as f:
            c = json.load(f)
            return bool(c.get("ssid"))
    except:
        return False


# ======================================================================
# MÁQUINA DE ESTADOS — ORQUESTADOR PRINCIPAL DE INTERFACES RF
# ======================================================================

async def gestionar_interfaces_network():
    """
    Orquestador de la máquina de estados de red.
    Garantiza exclusión mutua entre WiFi y BLE (no pueden estar activos simultáneamente).

    Estados:
      INIT            → detecta credenciales y decide el estado inicial
      BLE_ONLY        → sin WiFi; BLE activo para provisioning
      WIFI_CONNECTING → intentando conectar WiFi (BLE apagado)
      WIFI_ONLINE     → WiFi+MQTT activos; monitorea enlace
      FALLBACK_BLE    → WiFi caído; BLE activo; ⚑ pausa si hay sesión BLE activa
    """
    global current_state, wifi_conectado, mqtt_client
    wlan = network.WLAN(network.STA_IF)

    while True:
        tiene_creds = tiene_credenciales_wifi()

        # ─── STATE_INIT ───────────────────────────────────────────────────────
        if current_state == STATE_INIT:
            if tiene_creds:
                print("[NET] Credenciales detectadas en inicio. Conectando WiFi...")
                current_state = STATE_WIFI_CONNECTING
            else:
                print("[NET] Sin credenciales. Arrancando BLE de configuración...")
                current_state = STATE_BLE_ONLY

        # ─── STATE_BLE_ONLY ───────────────────────────────────────────────────
        elif current_state == STATE_BLE_ONLY:
            # Exclusión mutua: asegurar WiFi apagado
            if wlan.active():
                wlan.active(False)
                gc.collect()

            await ble_service.start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")

            # Si se recibieron credenciales vía BLE, transicionar de inmediato
            if tiene_creds:
                print("[NET] Credenciales recibidas por BLE. Cambiando a WiFi...")
                current_state = STATE_WIFI_CONNECTING
                continue   # Re-iterar sin dormir para no demorar la transición
            await asyncio.sleep(2)

        # ─── STATE_WIFI_CONNECTING ────────────────────────────────────────────
        elif current_state == STATE_WIFI_CONNECTING:
            # Exclusión mutua: apagar BLE antes de encender WiFi
            await ble_service.stop_ble_service()
            wlan.active(True)
            gc.collect()

            success = await conectar_wifi_non_blocking(wlan)
            if success:
                current_state = STATE_WIFI_ONLINE
            else:
                print("[NET] Fallo al conectar WiFi. Activando Fallback BLE...")
                current_state = STATE_FALLBACK_BLE

        # ─── STATE_WIFI_ONLINE ────────────────────────────────────────────────
        elif current_state == STATE_WIFI_ONLINE:
            # Monitorear enlace WiFi
            if not wlan.isconnected():
                print("[NET] Enlace WiFi perdido. Pasando a Fallback BLE.")
                wifi_conectado = False
                async with mqtt_lock:
                    mqtt_client = None
                current_state = STATE_FALLBACK_BLE
            else:
                # Mantener conexión MQTT activa
                if mqtt_client is None:
                    try:
                        internet_ok = comprobar_internet()
                    except Exception as e:
                        print("[NET] Error verificando internet:", e)
                        internet_ok = False
                    if internet_ok:
                        await conectar_mqtt_async()
                    else:
                        print("[NET] WiFi OK pero sin salida a Internet.")

            # Polling más frecuente si MQTT no está listo
            await asyncio.sleep(2 if mqtt_client is None else 5)

        # ─── STATE_FALLBACK_BLE ───────────────────────────────────────────────
        elif current_state == STATE_FALLBACK_BLE:
            # Exclusión mutua: apagar WiFi, encender BLE temporal
            wlan.active(False)
            wifi_conectado = False
            async with mqtt_lock:
                mqtt_client = None
            gc.collect()

            await ble_service.start_ble_service(name=f"Riego_{riego_core.chip_id[-4:]}")

            # ⚑ FIX CLAVE (basado en Dosimat IoT V2):
            # El contador se reinicia mientras haya un usuario conectado por BLE.
            # El equipo NUNCA interrumpe una sesión activa para reconectar WiFi.
            print(f"[NET] Ventana Fallback BLE activa (máx {ventana_fallback_ble_s}s, pausa si hay sesión)...")
            segundos_esperados = 0
            while segundos_esperados < ventana_fallback_ble_s:
                if current_state != STATE_FALLBACK_BLE:
                    break
                if ble_service.is_ble_connected():
                    # Hay un usuario conectado: reiniciar contador para no cortarle
                    segundos_esperados = 0
                else:
                    segundos_esperados += 1
                await asyncio.sleep(1)

            # Ventana completada sin clientes activos → intentar reconectar WiFi
            if current_state == STATE_FALLBACK_BLE:
                print("[NET] Ventana Fallback completada sin clientes BLE. Reintentando WiFi...")
                await ble_service.stop_ble_service()
                current_state = STATE_WIFI_CONNECTING


# ======================================================================
# TAREAS DE COMUNICACIÓN
# ======================================================================

async def procesar_cola_ble():
    """Lee comandos de la cola BLE y los pasa al núcleo funcional de riego."""
    while True:
        cmd_dict = await ble_service.rx_queue.get()
        if isinstance(cmd_dict, dict):
            cmd_dict['_origen'] = 'BLE'
        await riego_core.procesar_comando(cmd_dict)


async def tarea_tx_queue():
    """
    Desencola mensajes de tx_queue de riego_core y los transmite por el canal activo.

    Routing MQTT por tipo de mensaje:
      TELEMETRIA                      → riego/{hash}/telemetry
      CONFIG, ACK_CFG, ACK_WIFI, ...  → riego/{hash}/config
      LOG_ENTRY, LOGS_END, ...        → riego/{hash}/logs
    """
    global mqtt_client
    while True:
        try:
            msg_dict = await riego_core.tx_queue.get()
            destino = msg_dict.pop("_destino", "ALL")

            # — Canal BLE ─────────────────────────────────────────────────────
            if destino in ("ALL", "BLE"):
                if ble_service.is_ble_connected():
                    await ble_service.send_json_async(msg_dict)

            # — Canal MQTT ────────────────────────────────────────────────────
            if destino in ("ALL", "MQTT"):
                # Referencia local sin lock: check_msg() y publish() no pueden
                # interleavearse en asyncio cooperativo (ambos son síncronos).
                _mc = mqtt_client
                if _mc and wifi_conectado:
                    try:
                        topic_hash = riego_core.calcular_hash_seguro()
                        if topic_hash:
                            tipo = msg_dict.get("tipo", "")
                            if tipo in ("LOG_ENTRY", "LOGS_END", "LOGS_LIST"):
                                suffix = "logs"
                            elif tipo in ("CONFIG", "ACK_CFG", "ACK_CONFIG",
                                          "ACK_WIFI", "NEED_INIT", "CONFIG_PROG"):
                                suffix = "config"
                            else:
                                suffix = "telemetry"

                            topic_pub = f"riego/{topic_hash}/{suffix}".encode('utf-8')
                            json_bytes = json.dumps(msg_dict).encode('utf-8')

                            MAX_MQTT_PAYLOAD = 8192
                            if len(json_bytes) > MAX_MQTT_PAYLOAD:
                                print(f"[NET_TX] Payload {len(json_bytes)}B excede límite. Descartando.")
                                gc.collect()
                            else:
                                # WDT alimentado antes y después de publish() para evitar crash
                                # si SockWrapper.write() tarda (máx 5 reintentos × 3s = 15s).
                                if wdt_ref: wdt_ref.feed()
                                _mc.publish(topic_pub, json_bytes)
                                if wdt_ref: wdt_ref.feed()
                                gc.collect()

                    except MemoryError:
                        print("[NET_TX] Memoria insuficiente. Reclamando RAM...")
                        gc.collect()
                    except OSError as e:
                        print("[NET_TX] Error de socket publicando MQTT:", e)
                        mqtt_client = None
                    except Exception as e:
                        print("[NET_TX] Error publicando MQTT:", e)

            await asyncio.sleep_ms(50)

        except Exception as e:
            print("[NET_TX] Error general en tarea_tx_queue:", e)
            await asyncio.sleep_ms(100)
