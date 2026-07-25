# utils.py - Utilidades compartidas entre módulos del firmware Smart Riego
# Fix #3: AsyncQueue movida aquí para eliminar definición duplicada en
#          riego_core.py y ble_service.py.

import uasyncio as asyncio

class AsyncQueue:
    """Cola asíncrona simple para comunicación inter-tareas (asyncio).
    Utiliza un asyncio.Event interno para que el consumidor no realice
    busy-waiting, liberando el CPU para el resto del event-loop.
    """
    def __init__(self):
        self._queue = []
        self._event = None

    def _get_event(self):
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    async def put(self, item):
        self._queue.append(item)
        self._get_event().set()

    async def get(self):
        ev = self._get_event()
        while not self._queue:
            ev.clear()
            await ev.wait()
        return self._queue.pop(0)
