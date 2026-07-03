import uasyncio as asyncio
import aioble
import bluetooth
import json
import gc

# UUIDs de Nordic UART Service
_UART_SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_RX_CHAR_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX_CHAR_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

# Configuración del servicio y características
_uart_service = aioble.Service(_UART_SERVICE_UUID)
_uart_rx = aioble.Characteristic(_uart_service, _UART_RX_CHAR_UUID, write=True, write_no_response=True)
_uart_tx = aioble.Characteristic(_uart_service, _UART_TX_CHAR_UUID, read=True, notify=True)

aioble.register_services(_uart_service)

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

# Cola asíncrona para comandos entrantes
rx_queue = AsyncQueue()

# Conexión actual BLE
_current_connection = None
# Flag para serializar envíos BLE y evitar interleaving de chunks JSON
_ble_sending = False
# Flag para controlar las tareas
_ble_running = False

def is_ble_connected():
    return _current_connection is not None

async def ble_rx_task():
    """ Tarea asíncrona para recibir e interpretar comandos RX """
    buffer = b""
    while _ble_running:
        try:
            conn = await _uart_rx.written()
            data = _uart_rx.read()
            if data:
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        payload = line.decode("utf-8").strip()
                        if payload:
                            cmd_dict = json.loads(payload)
                            await rx_queue.put(cmd_dict)
                    except Exception as e:
                        print(f"BLE RX JSON Parse Error: {e} -> Raw len: {len(line)} bytes")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"BLE RX Exception: {e}")
            buffer = b""  # Limpiar buffer ante errores
            await asyncio.sleep_ms(1000)

async def send_json_async(datos_dict):
    """ Envía datos JSON a la característica TX en fragmentos.
    
    Usa _ble_sending para serializar envíos y evitar que dos tareas
    concurrentes intercalen sus chunks, corrompiendo el stream JSON en la app.
    """
    global _current_connection, _ble_sending
    if _current_connection is None:
        return

    # Esperar a que el envío anterior termine (máx ~2s)
    for _ in range(50):
        if not _ble_sending:
            break
        await asyncio.sleep_ms(40)
    else:
        return  # Timeout: descartar para no acumular indefinidamente

    _ble_sending = True
    try:
        json_str = json.dumps(datos_dict) + "\n"
        max_len = 20  # MTU conservador

        for i in range(0, len(json_str), max_len):
            if _current_connection is None:
                break
            chunk = json_str[i:i + max_len].encode('utf-8')
            try:
                _uart_tx.write(chunk)
                _uart_tx.notify(_current_connection, chunk)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print("BLE TX Notify Error:", e)
                break
            # 40ms para dar tiempo al teléfono a procesar las notificaciones
            # sin perder paquetes (vital para JSON grandes como el historial).
            await asyncio.sleep_ms(40)

    except Exception as e:
        print(f"BLE TX Error General: {e}")
    finally:
        _ble_sending = False

async def ble_advertise_task(name="DosimatBLE"):
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

async def start_ble_service(name="DosimatBLE"):
    """ Lanza las tareas BLE concurrentemente """
    global ble_tasks, _ble_running
    if ble_tasks:
        return
    _ble_running = True
    t1 = asyncio.create_task(ble_advertise_task(name))
    t2 = asyncio.create_task(ble_rx_task())
    ble_tasks = [t1, t2]

async def stop_ble_service():
    """ Detiene las tareas BLE para liberar memoria """
    global ble_tasks, _ble_running
    _ble_running = False
    for t in ble_tasks:
        t.cancel()
    ble_tasks = []
    # Limpiamos el buffer de BLE si es posible
    gc.collect()
