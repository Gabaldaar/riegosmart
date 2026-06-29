import bluetooth
import struct
import json
from ble_advertising import advertising_payload
from micropython import const

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)

_FLAG_READ = const(0x0002)
_FLAG_WRITE_NO_RESPONSE = const(0x0004)
_FLAG_WRITE = const(0x0008)
_FLAG_NOTIFY = const(0x0010)

# UUIDs genéricos estilo UART (Nordic UART Service) 
# Muy utilizados para envío de comandos y datos en texto/JSON
_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX = (
    bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_READ | _FLAG_NOTIFY,
)
_UART_RX = (
    bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE,
)
_UART_SERVICE = (
    _UART_UUID,
    (_UART_TX, _UART_RX),
)

class BLEService:
    def __init__(self, name="DosimatBLE"):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        
        # Registrar el servicio
        ((self._handle_tx, self._handle_rx),) = self._ble.gatts_register_services((_UART_SERVICE,))
        
        self._connections = set()
        self._write_callback = None
        self._rx_buffer = ""
        # Para no exceder el límite de 31 bytes, el nombre va en el payload principal
        # y el UUID de 128-bits va en la respuesta de escaneo (Scan Response)
        self._payload = advertising_payload(name=name)
        self._scan_resp = advertising_payload(services=[_UART_UUID])
        
        self._advertise()

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
            print("BLE: Dispositivo conectado (conn_handle:", conn_handle, ")")
            
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            if conn_handle in self._connections:
                self._connections.remove(conn_handle)
            print("BLE: Dispositivo desconectado")
            self._advertise()
            
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            print("BLE: Evento de escritura recibido (handle:", value_handle, ")")
            value = self._ble.gatts_read(value_handle)
            if value_handle == self._handle_rx and self._write_callback:
                try:
                    data_str = value.decode('utf-8')
                    self._rx_buffer += data_str
                    
                    # Procesar cada línea completa en el buffer
                    while '\n' in self._rx_buffer:
                        line, self._rx_buffer = self._rx_buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            print("BLE: Comando extraido del buffer:", line)
                            try:
                                json.loads(line)
                                self._write_callback(line)
                            except ValueError:
                                print("BLE: Ignorando comando corrupto:", line)
                                
                    # Limpieza de seguridad si el buffer crece demasiado sin saltos de línea
                    if len(self._rx_buffer) > 512:
                        print("BLE: Vaciando buffer RX por exceso de tamaño")
                        self._rx_buffer = ""
                        
                except Exception as e:
                    print("BLE: Error RX", e)
                    self._rx_buffer = ""

    def on_write(self, callback):
        """
        Establece una función callback que se ejecutará cuando se reciba 
        un comando a través de la característica RX (escritura).
        """
        self._write_callback = callback

    def send_json(self, data_dict):
        """
        Envía un diccionario en formato JSON a través de la característica TX (notificaciones).
        """
        if not self._connections:
            return
        
        try:
            json_str = json.dumps(data_dict) + "\n"
            self._send(json_str)
        except Exception as e:
            print("BLE: Error al formatear JSON", e)

    def _send(self, data_str):
        """
        Segmenta el mensaje en paquetes de 20 bytes para compatibilidad BLE estándar
        y los envía por notificación con un delay para evitar agotar la memoria.
        """
        import time
        data_bytes = data_str.encode('utf-8')
        for i in range(0, len(data_bytes), 20):
            chunk = data_bytes[i:i+20]
            for conn_handle in self._connections:
                retries = 3
                while retries > 0:
                    try:
                        self._ble.gatts_notify(conn_handle, self._handle_tx, chunk)
                        break
                    except Exception as e:
                        print("BLE: Error al notificar fragmento, reintentando...", e)
                        time.sleep_ms(100) # delay largo si hay ENOMEM
                        retries -= 1
            time.sleep_ms(40) # delay entre paquetes normal aumentado

    def _advertise(self, interval_us=500000):
        """
        Inicia la publicación del servicio BLE (Advertising) para que pueda
        ser descubierto por otros dispositivos.
        """
        print("BLE: Iniciando advertising...")
        self._ble.gap_advertise(interval_us, adv_data=self._payload, resp_data=self._scan_resp)

