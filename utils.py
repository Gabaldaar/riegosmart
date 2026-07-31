# utils.py - Utilidades compartidas entre módulos del firmware Smart Riego
# Fix #3: AsyncQueue movida aquí para eliminar definición duplicada en
#          riego_core.py y ble_service.py.

import uasyncio as asyncio

class AsyncQueue:
    """Cola asíncrona simple para comunicación inter-tareas (asyncio).
    Utiliza un asyncio.Event interno para que el consumidor no realice
    busy-waiting, liberando el CPU para el resto del event-loop.
    Incluye un límite maxsize opcional para evitar desbordamientos de RAM.
    """
    def __init__(self, maxsize=20):
        self._queue = []
        self._event = None
        self._maxsize = maxsize

    def _get_event(self):
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    async def put(self, item):
        if self._maxsize and len(self._queue) >= self._maxsize:
            self._queue.pop(0)  # Descartar elemento más antiguo para prevenir desbordamiento
        self._queue.append(item)
        self._get_event().set()

    async def get(self):
        ev = self._get_event()
        while not self._queue:
            ev.clear()
            await ev.wait()
        return self._queue.pop(0)
