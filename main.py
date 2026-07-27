# main.py - Punto de entrada principal y orquestación de tareas asíncronas
import machine
import time

# Delay de seguridad: 3 segundos para permitir Ctrl+C en consola de desarrollo
try:
    _led = machine.Pin(2, machine.Pin.OUT)
    _led.value(1)
    print("Iniciando... Tienes 3 segundos para pulsar Ctrl+C y detener el script.")
    time.sleep(3)
    _led.value(0)
except:
    pass

import uasyncio as asyncio
import riego_core
import network_manager
import ble_service
import sys_log

wdt = None


async def main():
    global wdt
    print("[MAIN] Inicializando sistema de riego...")

    # 1. Cargar configuración persistente e inicializar hardware
    await riego_core.cargar_configuracion()
    await riego_core.iniciar_tareas()
    await sys_log.log_event({"tipo": "info", "msg": "Sistema iniciado"}, wifi_activo=True)

    # 2. Indicador LED de estado
    asyncio.create_task(tarea_led())

    # 3. Tareas de red y comunicación (gestionadas por network_manager)
    asyncio.create_task(network_manager.gestionar_interfaces_network())
    asyncio.create_task(network_manager.procesar_cola_ble())
    asyncio.create_task(network_manager.tarea_tx_queue())

    # 4. Watchdog Timer de hardware (reinicia el equipo si el loop se bloquea > 30s)
    try:
        wdt = machine.WDT(timeout=30000)
        network_manager.wdt_ref = wdt   # Compartir referencia para feed() en esperas largas
        print("[MAIN] WDT inicializado (30s).")
    except Exception as e:
        print("[MAIN] WDT no disponible:", e)
        wdt = None

    # Loop principal — sólo alimenta el WDT
    while True:
        if wdt:
            wdt.feed()
        await asyncio.sleep(2)


async def tarea_led():
    """
    Indicador de estado por LED integrado (GPIO 2).
    Patrones:
      Pausa lluvia/manual  : 2000ms ON / 200ms OFF   (destello largo)
      Regando + WiFi       : 500ms ON / 500ms OFF     (pulso rápido)
      Regando + BLE        : 1000ms ON / 200ms OFF    (pulso lento)
      IDLE + WiFi          : 200ms ON / 4000ms OFF    (latido)
      IDLE + BLE conectado : 200-200-200-4000ms       (doble latido)
      Sin ninguna conexión : 100ms ON / 100ms OFF     (parpadeo rápido)
    """
    led = machine.Pin(2, machine.Pin.OUT)
    while True:
        ble_on  = ble_service.is_ble_connected()
        wifi_on = network_manager.wifi_conectado
        estado  = riego_core.estado_riego
        retraso = (
            (riego_core.rain_sensor and riego_core.rain_sensor.value() == 0) or
            (time.time() < riego_core.config_data.get("timestamp_rain_delay", 0))
        )

        if retraso:
            patron = [(1, 2000), (0, 200)]
        elif estado != "IDLE":
            patron = [(1, 500), (0, 500)] if wifi_on else [(1, 1000), (0, 200)]
        else:
            if wifi_on:
                patron = [(1, 200), (0, 4000)]
            elif ble_on:
                patron = [(1, 200), (0, 200), (1, 200), (0, 4000)]
            else:
                patron = [(1, 100), (0, 100)]

        for valor, duracion in patron:
            led.value(valor)
            await asyncio.sleep_ms(duracion)


# Ejecutar loop principal
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("[MAIN] Ejecución interrumpida por el usuario.")
except Exception as e:
    print("[MAIN] Error fatal en el loop principal:", e)
    time.sleep(2)
    machine.reset()
