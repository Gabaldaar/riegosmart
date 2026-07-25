# Arquitectura y Comunicación de Smart Riego
Este documento detalla la arquitectura de comunicación, el handshake de red, el modelo de datos unificado y la robustez operativa del sistema **Smart Riego** (MicroPython ESP32 + Web App PWA + Firebase Firestore). Está diseñado para servir como manual de inducción técnico definitivo para desarrolladores y agentes de inteligencia artificial.

---

## 1. Exclusión Mutua de Radio (No-Coexistencia Física)

### Motivación y Desafío Físico
El ESP32 tiene un único transceptor RF compartido que se encarga del hardware físico de **Bluetooth (BLE)** y **Wi-Fi**. Operar ambos de manera simultánea en MicroPython genera inestabilidad RF severa (caídas de conexión por colisión de frecuencia), congestiona la RAM del procesador y puede gatillar reinicios espontáneos debido a la falta de memoria del montón (Heap).

### Implementación (Máquina de Estados de Red)
El firmware implementa una máquina de estados asíncrona estricta en el archivo [main.py](file:///c:/Users/gabal/OneDrive/Documentos/00_PROYECTOS_APPS/CONTROL_RIEGO/main.py) que garantiza que **WiFi y BLE nunca operen a la vez**:

```
                  +--------------------------------+
                  |           MODO BLE             |
                  | (Búsqueda local, WiFi apagado) |
                  +---------------+----------------+
                                  |
               Conexión WiFi      |      Pérdida de red /
                 Exitosa          |      WiFi no configurado
                                  v
                  +---------------+----------------+
                  |           MODO WIFI            |
                  | (Broker MQTT, BLE apagado)     |
                  +--------------------------------+
```

1. **Estado Inicial (Modo BLE / Aprovisionamiento)**:
   * Al arrancar, si el ESP32 no tiene credenciales de red válidas o no logra conectarse a la red WiFi, inicializa el servicio Bluetooth en [ble_service.py](file:///c:/Users/gabal/OneDrive/Documentos/00_PROYECTOS_APPS/CONTROL_RIEGO/ble_service.py) y entra en modo de escucha local. El módem Wi-Fi se apaga físicamente para liberar memoria y recursos RF.
2. **Transición a Wi-Fi (Modo Remoto)**:
   * Cuando el usuario envía las credenciales mediante el comando `config_wifi` por Bluetooth, el ESP32:
     1. Desactiva y detiene por completo el servicio Bluetooth, apagando físicamente la radio (`bluetooth.active(False)`).
     2. Libera memoria RAM mediante una recolección forzada (`gc.collect()`).
     3. Activa la radio Wi-Fi e intenta conectarse al punto de acceso.
3. **Mecanismo de Caída (Fallback)**:
   * Si la conexión Wi-Fi se cae de forma permanente o no se logra establecer conexión en un periodo de tiempo prudente, la máquina de estados apaga la radio Wi-Fi, vuelve a activar el transceptor en modo Bluetooth (`bluetooth.active(True)`) y levanta el servicio BLE para permitir diagnóstico o re-configuración local.

---

## 2. Protocolos de Comunicación y Handshake de Datos

El sistema admite dos interfaces de transporte transparentes:

### A. Canal de Comunicación BLE (Local)
* **Protocolo**: Perfil de UART virtual (Nordic SPP) para transferencia de datos en serie sobre BLE.
* **Limitación de MTU**: Dado que las tramas BLE estándar son de tamaño reducido, la PWA en [comms.js](file:///c:/Users/gabal/OneDrive/Documentos/00_PROYECTOS_APPS/CONTROL_RIEGO/pwa_app/comms.js) fragmenta los comandos JSON salientes en paquetes de **20 bytes** y añade un carácter delimitador de fin de línea (`\n`).
* **Límite de Payload en ESP32**: El ESP32 re-ensambla los fragmentos recibidos en memoria. Para evitar desbordamientos de buffer o fragmentación del montón de MicroPython, el firmware descarta cualquier payload JSON acumulado que exceda los **150 bytes**.

### B. Canal de Comunicación MQTT (Remoto)
* **Servidor**: Broker MQTT seguro (`wss://broker.hivemq.com:8884/mqtt` para WebSockets y `mqtt.client` estándar para el microcontrolador).
* **Seguridad por Hash SHA256**: Los topics de comunicación se obfuscación mutuamente mediante un canal seguro calculado a partir de la firma:
  $$\text{Hash} = \text{SHA256}(\text{ChipID}[-4:] + \text{Token})$$
  * **Comandos**: `riego/{hash}/cmd`
  * **Telemetría**: `riego/{hash}/telemetry`
* **Conexión No Bloqueante**: MicroPython utiliza sockets bloqueantes por defecto. Para evitar que el motor de riego se congele si el broker de MQTT está caído, el firmware en [main.py](file:///c:/Users/gabal/OneDrive/Documentos/00_PROYECTOS_APPS/CONTROL_RIEGO/main.py) intercepta la llamada a `socket.socket` durante la conexión, aplicando un timeout agresivo de `2.0 segundos` que no bloquea la máquina de estados.

---

## 3. Modelo de Datos Firebase como SSOT (Single Source of Truth)

Para aliviar al ESP32 del consumo de almacenamiento flash y tráfico en canales de radio limitados, la arquitectura delega la fuente de verdad a **Firebase Firestore**:

```
  +------------------+                   +--------------------+
  |   App PWA        |                   | Firebase Firestore |
  | (Traducción/UI)  |<=================>|  (Fuente de Verdad)|
  +--------+---------+                   +---------+----------+
           |                                       ^
           | Comandos locales (BLE/MQTT)           |
           v                                       |
  +--------+---------+                             |
  | ESP32 Firmware   |-----------------------------+
  | (Config Mínima)  |  Sincronización en la Nube
  +------------------+
```

### A. El Rol de la App Móvil (PWA) como Gateway
La aplicación móvil se conecta mediante autenticación anónima a Firebase y lee/escribe en el documento `dispositivos/{chipId}`.
* **Aprovisionamiento**: Si un dispositivo nuevo se empareja por primera vez, la PWA crea y inicializa el documento correspondiente en Firestore.
* **Separación de Nombres Largos**: El mapa `nombres_zonas` (ej: `"Z1": "Césped Delantero"`) reside **exclusivamente** en Firestore. El ESP32 solo opera con códigos cortos de zona (`Z1` a `Z8`).
* **Traducción Dinámica**: La UI de la PWA asocia dinámicamente el código de zona enviado en la telemetría del ESP32 con el nombre amigable de Firestore mediante la función `obtenerNombreZona(zonaId)`.

### B. Sincronización y Reconciliación de Versiones (`config_version`)
El firmware local en la memoria flash guarda únicamente la configuración técnica mínima (horas de arranque, tiempos de riego por zona, modo de bomba, estado estacional).
1. Cuando el usuario realiza cambios operativos en la PWA, esta **incrementa la versión local (`config_version += 1`)**, escribe los datos en Firestore y envía el payload técnico limpio (removiendo nombres largos) al ESP32 por el canal disponible.
2. Al recibir un comando `UPDATE_CONFIG`, el ESP32 compara `version_recibida` con `version_local`:
   * **Caso Mayor (`recibida > local`)**: Aplica y almacena los cambios en flash de forma atómica y responde con `ACK_CFG`.
   * **Caso Menor o Igual (`recibida <= local`)**: Rechaza la configuración recibida y responde enviando su propia configuración local. Esto actúa como un sistema de **auto-recuperación** si el ESP32 sufre un corte eléctrico inesperado y reinicia con una versión de datos inconsistente.

---

## 4. Robustez Operativa y Protección de Hardware

El firmware está diseñado para resistir fallos físicos y comandos erróneos sin interrumpir el funcionamiento del motor de riego:

### A. Lógica Eléctrica de Pines de Control
* **Válvula Maestra (MV) [GPIO 25]**: Configurada con **lógica directa** (activa en alto: `0 = Apagada`, `1 = Encendida`). Se apaga en el boot de forma inmediata para evitar transitorios eléctricos de arranque.
* **Zonas de Riego 1 a 8 [GPIO 18, 23, 26, 27, 19, 32, 33, 14]**: Configurada con **lógica inversa** (activas en bajo: `1 = Apagadas`, `0 = Encendidas`). La inicialización segura en `boot.py` pone todos los GPIOs en estado `1` (apagadas).

### B. Reinicios Diferidos Seguros
Para evitar daños en el motor o solenoides bajo presión de agua activa, si el ESP32 está ejecutando un ciclo de riego (`estado_riego != "IDLE"`), los comandos que requieren un reset de hardware (`config_wifi`, `INIT_TOKEN` o `FACTORY_RESET`) no ejecutan `machine.reset()` inmediatamente. En su lugar, colocan la bandera `reinicio_pendiente = True` y difieren el reset hasta que el ciclo de riego termine de forma segura y el sistema regrese al reposo.

### C. Logs Antidesgaste y Rotación de Flash
Para proteger la celda de memoria flash de la placa ESP32 del desgaste prematuro y fragmentación del sistema de archivos:
* **Modo BLE (Offline)**: Los logs se almacenan temporalmente en un **buffer circular de RAM de 3 ítems**. Al conectarse la PWA por BLE, lee los logs RAM, los sube a Firestore (subcolección `logs`) y limpia el buffer circular. No se realiza ninguna escritura física en disco.
* **Modo WiFi (Online)**: Los logs se escriben por append en `sys_log.jsonl`. Si el archivo supera los **4 KB**, el sistema lo rota atómicamente a `sys_log.old` renombrándolo en el disco de MicroPython. Esto elimina la necesidad de cargar todo el historial en RAM para procesarlo.
