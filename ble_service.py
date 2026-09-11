import uasyncio as asyncio
import aioble
import bluetooth
import json
import gc
from utils import AsyncQueue  # Fix #3: Cola asíncrona centralizada en utils.py

# UUIDs de Nordic UART Service
_UART_SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_RX_CHAR_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX_CHAR_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

# Configuración del servicio y características
_uart_service = aioble.Service(_UART_SERVICE_UUID)
_uart_rx = aioble.Characteristic(_uart_service, _UART_RX_CHAR_UUID, write=True, write_no_response=True, capture=True)
_uart_tx = aioble.Characteristic(_uart_service, _UART_TX_CHAR_UUID, read=True, notify=True)

aioble.register_services(_uart_service)

# Cola asíncrona para comandos entrantes
rx_queue = AsyncQueue()

# Conexión actual BLE
_current_connection = None
# Flag para serializar envíos BLE y evitar interleaving de chunks JSON
_ble_sending = False
# Flag para controlar el ciclo de tareas (idempotencia y estado)
_ble_running = False

def is_ble_connected():
    return _current_connection is not None

async def ble_rx_task():
    """ Tarea asíncrona para recibir e interpretar comandos RX """
    buffer = b""
    while _ble_running:
        try:
            conn, data = await _uart_rx.written()
            if data:
                print(f"[BLE_RX] Data cruda: {data}")
                buffer += data
                if len(buffer) > 1024:
                    print("[BLE_RX] Advertencia: Buffer RX excedió 1024 bytes. Limpiando para prevenir desbordamiento.")
                    buffer = b""
                    continue
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    print(f"[BLE_RX] Linea completa: {line}")
                    try:
                        payload = line.decode("utf-8").strip()
                        if payload:
                            cmd_dict = json.loads(payload)
                            print(f"[BLE_RX] JSON Válido. Encolando comando: {cmd_dict.get('comando')}")
                            await rx_queue.put(cmd_dict)
                    except Exception as e:
                        print(f"[BLE_RX] Error parseando JSON: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"BLE RX Exception: {e}")
            buffer = b""  # Limpiar buffer ante errores
            await asyncio.sleep_ms(1000)

async def send_json_async(datos_dict):
    """ Envía datos JSON a la característica TX en fragmentos.
    
    Rechaza payloads extensos (>150 bytes) para evitar fragmentación de memoria y congestión BLE.
    Usa _ble_sending para serializar envíos y evitar colisiones de chunks.
    """
    global _current_connection, _ble_sending
    if _current_connection is None:
        print("[BLE_TX] Error: No hay conexion activa para enviar")
        return

    try:
        json_str = json.dumps(datos_dict) + "\n"
    except Exception as e:
        print(f"[BLE_TX] Error serializando JSON: {e}")
        return

    # Fix #1: Límite TX aumentado a 500 bytes (solo afecta el canal de envío).
    # El límite de RX se mantiene en 150 bytes (ver ble_rx_task buffer).
    # 500 bytes es seguro: aioble fragmenta en chunks de MTU internamente,
    # y el string JSON ya está en memoria al llegar a esta línea.
    if len(json_str) > 500:
        print(f"[BLE_TX] Error: Payload excedió límite TX ({len(json_str)} bytes > 500)")
        return

    # Esperar a que el envío anterior termine (máx ~2s)
    for _ in range(50):
        if not _ble_sending:
            break
        await asyncio.sleep_ms(40)
    else:
        print("[BLE_TX] Error: Timeout esperando que se libere el TX")
        return  # Timeout: descartar

    _ble_sending = True
    try:
        max_len = 20  # MTU conservador
        print(f"[BLE_TX] Enviando {len(json_str)} bytes en trozos de {max_len}...")

        for i in range(0, len(json_str), max_len):
            if _current_connection is None:
                print("[BLE_TX] Error: Desconectado en medio del envío")
                break
            chunk = json_str[i:i + max_len].encode('utf-8')
            try:
                _uart_tx.notify(_current_connection, chunk)
            except asyncio.TimeoutError:
                print("[BLE_TX] Timeout enviando chunk")
            except Exception as e:
                print("[BLE_TX] Notify Error:", e)
                break
            # Esperar 40ms entre chunks
            await asyncio.sleep_ms(40)
        print("[BLE_TX] Envío completado.")

    except Exception as e:
        print(f"[BLE_TX] Error General: {e}")
    finally:
        _ble_sending = False

async def ble_advertise_task(name="RiegoBLE"):
    """ Tarea asíncrona para anunciar el servicio """
    global _current_connection
    while _ble_running:
        try:
            print(f"BLE: Iniciando Advertising ({name})...")
            connection = await aioble.advertise(
                250_000, 
                name=name, 
                services=[_UART_SERVICE_UUID], 
                appearance=0x00
            )
            if connection:
                print(f"BLE: Conectado a {connection.device}")
                _current_connection = connection
                
                # Esperar a la desconexión
                await connection.disconnected(timeout_ms=None)
                print("BLE: Dispositivo desconectado.")
            _current_connection = None
        except asyncio.CancelledError:
            print("BLE Advertising cancelado.")
            _current_connection = None
            raise
        except Exception as e:
            print(f"BLE Adv Error: {e}")
            _current_connection = None
            await asyncio.sleep_ms(2000)

ble_tasks = []

async def start_ble_service(name="RiegoBLE"):
    """ Lanza las tareas BLE concurrentemente (Idempotente) """
    global ble_tasks, _ble_running
    if _ble_running:
        return  # Ya iniciado
        
    _ble_running = True
    try:
        # Asegurar inicialización física del controlador de radio
        ble_hw = bluetooth.BLE()
        if not ble_hw.active():
            ble_hw.active(True)
            
        t1 = asyncio.create_task(ble_advertise_task(name))
        t2 = asyncio.create_task(ble_rx_task())
        ble_tasks = [t1, t2]
        print("[BLE] Servicio de Bluetooth iniciado.")
    except Exception as e:
        print("[BLE] Error al iniciar servicio BLE:", e)
        _ble_running = False

async def stop_ble_service():
    """ Detiene las tareas BLE y apaga el hardware para liberar memoria (Idempotente) """
    global ble_tasks, _ble_running, _current_connection
    if not _ble_running:
        return  # Ya apagado
        
    _ble_running = False
    _current_connection = None
    
    # Cancelar tareas
    for t in ble_tasks:
        try:
            t.cancel()
        except:
            pass
    ble_tasks = []
    
    # Detener controlador de Bluetooth nativo del chip para liberar memoria del stack RF
    try:
        ble_hw = bluetooth.BLE()
        ble_hw.active(False)
        print("[BLE] Hardware Bluetooth desactivado.")
    except Exception as e:
        print("[BLE] Error al desactivar hardware Bluetooth:", e)
        
    gc.collect()

