# ota_updater.py - Actualizador remoto seguro para ESP32 MicroPython
import gc
import os
import json
import time
import machine

# Configuración del repositorio
GITHUB_REPO = "Gabaldaar/riegosmart"
GITHUB_BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/version.json"
LOCAL_VERSION_FILE = "firmware_version.json"

DEFAULT_VERSION = {
    "hw_version": "1.0.0",
    "hw_version_code": 100,
    "last_update": "2026-09-10"
}


def obtener_version_actual():
    """Lee la versión instalada actualmente en la memoria flash."""
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_VERSION.copy()


def guardar_version_actual(v_dict):
    """Guarda los datos de la versión actual instalada."""
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            json.dump(v_dict, f)
    except Exception as e:
        print("[OTA] Error guardando versión:", e)


async def descargar_archivo_chunked(url, destino, wdt_ref=None, tam_chunk=1024):
    """
    Descarga un archivo por HTTPS en bloques pequeños para evitar saturar la RAM.
    Escribe directamente en un archivo temporal (.new).
    """
    import socket
    import ssl

    gc.collect()
    print(f"[OTA] Descargando {url} -> {destino}.new...")

    try:
        # Parsear URL: https://raw.githubusercontent.com/Gabaldaar/riegosmart/main/version.json
        proto, _, host, path = url.split("/", 3)
        puerto = 443 if proto == "https:" else 80

        dest_tmp = destino + ".new"
        
        # Crear socket con timeout
        ai = socket.getaddrinfo(host, puerto)[0]
        s = socket.socket(ai[0], ai[1], ai[2])
        s.settimeout(12.0)

        if wdt_ref: wdt_ref.feed()
        s.connect(ai[-1])
        if proto == "https:":
            s = ssl.wrap_socket(s, server_hostname=host)

        # Enviar petición HTTP GET
        req = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: ESP32-RiegoSmart-OTA\r\n"
            f"Connection: close\r\n\r\n"
        )
        s.write(req.encode('utf-8'))

        # Leer encabezados HTTP
        cabeceras = b""
        while b"\r\n\r\n" not in cabeceras:
            if wdt_ref: wdt_ref.feed()
            chunk = s.read(128)
            if not chunk:
                break
            cabeceras += chunk

        if b"200 OK" not in cabeceras and b"302 Found" not in cabeceras:
            print("[OTA] Error HTTP en respuesta:", cabeceras[:50])
            s.close()
            return False

        # Extraer el inicio del cuerpo si quedó en el buffer de cabeceras
        partes = cabeceras.split(b"\r\n\r\n", 1)
        cuerpo_inicial = partes[1] if len(partes) > 1 else b""

        bytes_totales = len(cuerpo_inicial)
        with open(dest_tmp, "wb") as f_out:
            if cuerpo_inicial:
                f_out.write(cuerpo_inicial)

            while True:
                if wdt_ref: wdt_ref.feed()
                gc.collect()
                datos = s.read(tam_chunk)
                if not datos:
                    break
                f_out.write(datos)
                bytes_totales += len(datos)

        s.close()
        print(f"[OTA] Descarga completada: {bytes_totales} bytes guardados.")
        return bytes_totales > 0

    except Exception as e:
        print(f"[OTA] Error durante la descarga de {destino}:", e)
        try: s.close()
        except: pass
        try: os.remove(destino + ".new")
        except: pass
        return False


async def consultar_actualizacion_disponible(wdt_ref=None):
    """Consulta version.json en GitHub para saber si hay una versión superior."""
    try:
        ok = await descargar_archivo_chunked(VERSION_URL, "version_manifest.json", wdt_ref=wdt_ref)
        if not ok:
            return None
        
        with open("version_manifest.json.new", "r") as f:
            manifest = json.load(f)
        try: os.remove("version_manifest.json.new")
        except: pass

        version_local = obtener_version_actual()
        remote_code = manifest.get("hw_version_code", 0)
        local_code = version_local.get("hw_version_code", 0)

        disponible = remote_code > local_code
        return {
            "disponible": disponible,
            "version_local": version_local.get("hw_version", "1.0.0"),
            "version_remota": manifest.get("hw_version", "1.0.0"),
            "changelog": manifest.get("changelog", ""),
            "release_date": manifest.get("release_date", ""),
            "files": manifest.get("files", [])
        }
    except Exception as e:
        print("[OTA] Error consultando actualización:", e)
        return None


async def ejecutar_actualizacion(manifest_data=None, wdt_ref=None, tx_queue=None, origen="ALL"):
    """
    Ejecuta el proceso completo de descarga y aplicación de actualización segura.
    1. Descarga todos los archivos como .new
    2. Si todo descargó bien, respalda actuales a .bak
    3. Renombra .new a .py
    4. Actualiza firmware_version.json
    5. Reinicia el ESP32
    """
    try:
        if tx_queue:
            await tx_queue.put({"tipo": "OTA_STATUS", "estado": "DESCARGANDO", "progreso": 10, "_destino": origen})

        # 1. Si no nos pasaron el manifiesto, descargarlo
        if not manifest_data:
            ok = await descargar_archivo_chunked(VERSION_URL, "version_manifest.json", wdt_ref=wdt_ref)
            if not ok:
                if tx_queue:
                    await tx_queue.put({"tipo": "OTA_STATUS", "estado": "ERROR_MANIFEST", "_destino": origen})
                return False
            with open("version_manifest.json.new", "r") as f:
                manifest_data = json.load(f)
            try: os.remove("version_manifest.json.new")
            except: pass

        archivos = manifest_data.get("files", [])
        if not archivos:
            print("[OTA] No hay lista de archivos para actualizar.")
            return False

        # 2. Descargar cada archivo a .new
        archivos_descargados = []
        total = len(archivos)
        for idx, item in enumerate(archivos):
            path = item.get("path")
            url = item.get("url")
            if not path or not url:
                continue

            if tx_queue:
                progreso = int(20 + (idx / total) * 50)
                await tx_queue.put({"tipo": "OTA_STATUS", "estado": f"Descargando {path}...", "progreso": progreso, "_destino": origen})

            ok = await descargar_archivo_chunked(url, path, wdt_ref=wdt_ref)
            if not ok:
                print(f"[OTA] Falló descarga de {path}. Abortando actualización.")
                # Limpiar los temporales que se hayan descargado
                for a in archivos_descargados:
                    try: os.remove(a + ".new")
                    except: pass
                if tx_queue:
                    await tx_queue.put({"tipo": "OTA_STATUS", "estado": "ERROR_DESCARGA", "_destino": origen})
                return False
            archivos_descargados.append(path)

        # 3. Aplicación atómica: Respaldar a .bak y renombrar .new a destino final
        if tx_queue:
            await tx_queue.put({"tipo": "OTA_STATUS", "estado": "APLICANDO", "progreso": 85, "_destino": origen})

        for path in archivos_descargados:
            # Crear backup del existente
            try:
                try: os.remove(path + ".bak")
                except: pass
                os.rename(path, path + ".bak")
            except Exception as e:
                print(f"[OTA] No se pudo crear backup de {path}:", e)

            # Promover .new a archivo final
            try:
                os.rename(path + ".new", path)
                print(f"[OTA] Archivo {path} actualizado con éxito.")
            except Exception as e:
                print(f"[OTA] Error fatal al renombrar {path}:", e)
                # Intentar restaurar backup
                try: os.rename(path + ".bak", path)
                except: pass

        # 4. Guardar nueva versión
        guardar_version_actual({
            "hw_version": manifest_data.get("hw_version", "1.0.0"),
            "hw_version_code": manifest_data.get("hw_version_code", 100),
            "last_update": manifest_data.get("release_date", str(time.time()))
        })

        if tx_queue:
            await tx_queue.put({"tipo": "OTA_STATUS", "estado": "EXITO_REINICIANDO", "progreso": 100, "_destino": origen})

        print("[OTA] Actualización finalizada exitosamente. Reiniciando equipo en 2 segundos...")
        if wdt_ref: wdt_ref.feed()
        time.sleep(2)
        machine.reset()
        return True

    except Exception as e:
        print("[OTA] Error crítico en ejecución de OTA:", e)
        if tx_queue:
            await tx_queue.put({"tipo": "OTA_STATUS", "estado": "ERROR_CRITICO", "detalle": str(e), "_destino": origen})
        return False
