import sys
import os
import json

try:
    import firebase_admin
    from firebase_admin import credentials
    from firebase_admin import firestore
except ImportError:
    print("Error: La librería 'firebase-admin' no está instalada.")
    print("Por favor, instálala usando: pip install firebase-admin")
    sys.exit(1)

# Verificar la existencia del archivo de credenciales
KEY_FILE = "serviceAccountKey.json"
if not os.path.exists(KEY_FILE):
    print(f"Error: No se encontró el archivo '{KEY_FILE}' en el directorio actual.")
    print("Por favor, descarga la clave privada de tu cuenta de servicio desde la consola de Firebase")
    print("y guárdala en esta carpeta con el nombre 'serviceAccountKey.json'.")
    sys.exit(1)

# Inicializar Firebase Admin SDK
try:
    cred = credentials.Certificate(KEY_FILE)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"Error inicializando Firebase Admin SDK: {e}")
    sys.exit(1)

print("--- Inicializador de Base de Datos Firestore RiegoSmart ---")
chip_id = input("Por favor, introduce el Chip ID del ESP32 (ej: 240A64C2E9F0): ").strip().upper()

if not chip_id:
    print("Error: El Chip ID no puede estar vacío.")
    sys.exit(1)

# Configuración mínima por defecto compatible con la nueva arquitectura del ESP32
doc_data = {
    "config_version": 1,
    "modo_bomba": True,
    "timestamp_rain_delay": 0,
    "token_acceso": "token_por_defecto_1234",
    "nombres_zonas": {
        "Z1": "Zona 1 (Jardín)",
        "Z2": "Zona 2 (Huerta)",
        "Z3": "Zona 3 (Macetas)",
        "Z4": "Zona 4 (Césped)",
        "Z5": "Zona 5",
        "Z6": "Zona 6",
        "Z7": "Zona 7",
        "Z8": "Zona 8"
    },
    "programas": {
        "P1": {
            "activo": False,
            "dias_semana": [1, 3, 5],
            "horas_arranque": ["08:00"],
            "zonas": {
                "Z1": {"minutos": 10, "cycle_min": 5, "soak_min": 1}
            }
        }
    }
}

try:
    print(f"Creando/Actualizando documento dispositivos/{chip_id}...")
    doc_ref = db.collection("dispositivos").document(chip_id)
    doc_ref.set(doc_data)
    
    # Crear un evento inicial en la subcolección de logs
    print(f"Creando log de evento inicial en dispositivos/{chip_id}/logs...")
    import time
    log_data = {
        "ts": time.time(),
        "tipo": "info",
        "msg": "Base de datos inicializada correctamente en la nube."
    }
    doc_ref.collection("logs").add(log_data)
    
    print("\n¡Base de datos Firestore inicializada exitosamente!")
    print(f"Dispositivo registrado: dispositivos/{chip_id}")
    print("Ya puedes desplegar tu App móvil y tu ESP32 se sincronizará al arrancar en WiFi.")
except Exception as e:
    print(f"Error escribiendo en Firestore: {e}")
