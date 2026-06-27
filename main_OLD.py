# main.py - Lógica principal de la aplicación (Migrado a BLE)
import time
from boot import global_vars
import gc #Para chequear la memoria
from machine import WDT, ADC, Pin #Importo el WatchDog, conversor AD y Pin
import json
from ble_service import BLEService

# Inicialización de hardware básico
ref = global_vars['ref'] #Es el LED AZUL indicador del tablero
ref.value(1) #Prendo el led para avisar que puede detenerse el programa
time.sleep(3) #Tiempo para detener el programa antes de que entre el WD con Ctrl + C
ref.value(0)

# Inicializar el Watchdog con un timeout de 10 segundos (en milisegundos)
wdt = WDT(timeout=10000)

# Variables globales - Version con RTC DS3231 + BLE
version = "V2.4_BLE"
estado_dosificador = "inactivo"
tiempo_inicio_espera = 0
tiempo_inicio_dosis = 0
ultima_verificacion = 0
mensaje_temporal = ""
tiempo_mensaje = 0
duracion_mensaje = 3  # Segundos que durará el mensaje
ultima_dosis_saltada = 0  # Timestamp de última dosis saltada

# Definicion de la varuiable para anular dosis
POS_DOSISNO = 32
POS_PILETAGRANDE = 33

# Configuración de patrones LED (segundos)
LED_PATRONES = {
    'inactivo':             [('OFF', 0.1), ('ON', 5.0)],
    'inactivo_refuerzo':    [('OFF', 0.1), ('ON', 0.1), ('OFF', 0.1), ('ON', 5.0)],
    'horario':              [('OFF', 0.1), ('ON', 2.0)],
    'horario_refuerzo':     [('OFF', 0.1), ('ON', 0.1), ('OFF', 0.1), ('ON', 2.0)],
    'dosificando':          [('OFF', 1.0), ('ON', 1.0)],
    'dosificando_refuerzo': [('OFF', 5.0), ('ON', 0.1)]
}

estado_led_actual = {
    'patron': LED_PATRONES['inactivo'],
    'indice': 0,
    'ultimo_cambio': 0,
    'estado': 0  # 0=OFF, 1=ON
}

# Acceso a variables de hardware adicionales
man = global_vars['man'] #Es la válvula
led1 = global_vars['led1'] #Es el LED interno
reloj = global_vars['reloj'] #Es el RTC
eeprom = global_vars['eeprom'] #Es la EEPROM del RTC

# Configuración del pin ADC donde está conectado el sensor
adc = ADC(Pin(34))
adc.atten(ADC.ATTN_11DB)
UMBRAL_TENSION = 2.75

# Inicialización de EEPROM
defaults = {
    'Fverano': '1030',
    'Finvierno': '0330',
    'Refuerzo': '0',
    'InNoche': '2000',
    'FinNoche': '0700',
    'DosisSi': '0',
    'CantDosis': '00',
    'GPInicio': '0800',
    'GPFin': '2200',
    'GPCantDosis': '2',
    'DosisAnulada': '0',
    'PiletaGrande': '0',
    'DosisNo': 0,
    'UltDosis': '200101010100',
    'UltBomba': '200101010100',
    'UltBombaAp': '200101010100',
    'EsperaMin': '01',
    'Espera': '30',
    'Dosis': '30',
    'DosisMin': '01'
}

variables = {
    'Fverano': (0, 4, False),
    'Finvierno': (5, 4, False),
    'Refuerzo': (13, 1, False), 
    'DosisSi': (14, 1, False),
    'InNoche': (16, 4, False),
    'FinNoche': (20, 4, False),
    'CantDosis': (25, 2, False),
    'EsperaMin': (27, 2, False),
    'Espera': (30, 2, False),
    'DosisNo': (32, 1, False),
    'PiletaGrande': (33, 1, False),
    'GPInicio': (34, 4, False),
    'GPCantDosis': (38, 1, False),
    'DosisAnulada': (39, 1, False),
    'UltDosis': (40, 12, False) ,
    'UltBomba': (60, 12, False) ,
    'UltBombaAp': (75, 12, False) ,
    'GPFin': (55, 4, False),
    'DosisMin': (9, 2, False),
    'Dosis': (11, 2, False),
}

def inicializar_eeprom_si_es_necesario(eeprom, defaults, variables):
    for nombre, (addr, length, _) in variables.items():
        try:
            valor = eeprom.read(addr, length)
            if valor == b'\xff' * length:
                raise ValueError("EEPROM vacía")
            valor.decode('utf-8')
        except:
            valor_defecto = str(defaults[nombre])
            eeprom.write(addr, valor_defecto.encode('utf-8'))
            print(f"Inicializado {nombre} con '{valor_defecto}'")

inicializar_eeprom_si_es_necesario(eeprom, defaults, variables)

# Registrar reinicio
t = reloj.get_time()
UltInicio = f"{t[2]:02d}/{t[1]:02d}/{t[0]} - {t[3]:02d}:{t[4]:02d}" 

def escribir_ultdosis(direcc, caso):
    global UltDosis, UltBomba, UltBombaAp
    try:
        fecha_hora_actual = reloj.get_time()
        ult_dosis_str = (
            f"{fecha_hora_actual[0]:04d}{fecha_hora_actual[1]:02d}"
            f"{fecha_hora_actual[2]:02d}{fecha_hora_actual[3]:02d}"
            f"{fecha_hora_actual[4]:02d}"
        )
        ult_dosis_bytes = bytes(ult_dosis_str, 'utf-8')
        if caso == 0:
            UltDosis = ult_dosis_bytes
            eeprom.write(40, ult_dosis_bytes)
        elif caso == 1:
            UltBomba = ult_dosis_bytes
            eeprom.write(60, ult_dosis_bytes)
        else:
            UltBombaAp = ult_dosis_bytes
            eeprom.write(75, ult_dosis_bytes)
        return ult_dosis_str
    except Exception as e:
        print(f"Error al escribir en la EEPROM: {e}")
        return None

# Carga inicial de variables
Bomba = 0 
Fverano = eeprom.read(0, 4).decode('utf-8')
Finvierno = eeprom.read(5, 4).decode('utf-8')
Refuerzo = int(eeprom.read(13, 1)) if hasattr(eeprom, 'read') else 0
DosisSi = int(eeprom.read(14, 1))
InNoche = eeprom.read(16, 4).decode('utf-8')
FinNoche = eeprom.read(20, 4).decode('utf-8')
CantDosis = int(eeprom.read(25, 2).decode('utf-8'))
GPInicio = eeprom.read(34, 4).decode('utf-8')
GPFin = eeprom.read(55, 4).decode('utf-8')
GPCantDosis = eeprom.read(38, 1).decode('utf-8')
DosisAnulada = eeprom.read(39,1).decode('utf-8')
PiletaGrande = int(eeprom.read(33, 1).decode('utf-8'))
DosisNo = int(eeprom.read(32, 1).decode('utf-8'))
UltDosis = eeprom.read(40, 12)
UltBomba = eeprom.read(60, 12)
UltBombaAp = eeprom.read(75, 12)
Espera = int(eeprom.read(30, 2).decode('ascii').strip('\x00'))
Dosis = int(eeprom.read(11, 2).decode('ascii').strip('\x00'))
DosisMin = int(eeprom.read(9, 2).decode('ascii').strip('\x00'))
EsperaMin = int(eeprom.read(27, 2).decode('ascii').strip('\x00'))

def detectar_bomba():
    lectura_adc = adc.read()
    voltaje = (lectura_adc / 4095) * 3.3
    return voltaje > UMBRAL_TENSION

def formatear_fecha(fecha):
    return f"{fecha[2:]}/{fecha[:2]}"

def formatear_hora(hora):
    return f"{hora[:2]}:{hora[2:]}"

def guardar_pileta(valor):
    try:
        valor_guardar = 1 if str(valor).lower() in ('1', 'true', 'on', 'si', 'yes') else 0
        eeprom.write(POS_PILETAGRANDE, str(valor_guardar).encode())
        global PiletaGrande
        PiletaGrande = valor_guardar
        return True
    except Exception as e:
        print(f"Error al guardar: {e}")
        eeprom.write(POS_PILETAGRANDE, b'0')  
        PiletaGrande = 0
        return False    

def guardar_hora_inicio(hora_str, POS):
    global GPInicio, GPFin
    try:
        hora = hora_str.replace(":", "")[:4]
        if len(hora) == 4 and hora.isdigit():
            if POS == 34:
                GPInicio = hora.encode().decode('utf-8')
            elif POS == 55:
                GPFin = hora.encode().decode('utf-8')
            eeprom.write(POS, hora.encode())
            return True
    except Exception as e:
        print(f"Error al guardar hora inicio: {e}")
    return False

def guardar_cantidad_dosis(cantidad):
    global GPCantDosis
    try:
        if str(cantidad) in {'2', '3', '4','5', '6','7', '8'}:
            GPCantDosis = str(cantidad)
            eeprom.write(38, GPCantDosis.encode())
            return True
    except Exception as e:
        print(f"Error al guardar cantidad dosis: {e}")
    return False

# FUNCIONES DE TIEMPO
def esta_en_horario_nocturno(InNoche, FinNoche):
    ahora = reloj.get_time()
    hora_actual = ahora[3]
    minuto_actual = ahora[4]

    def tiempo_a_minutos(hhmm):
        return int(hhmm[:2]) * 60 + int(hhmm[2:])

    if PiletaGrande == 0:
        inicio_noche = InNoche
        fin_noche = FinNoche
        inicio_min = tiempo_a_minutos(inicio_noche)
        fin_min = tiempo_a_minutos(fin_noche)
        actual_min = hora_actual * 60 + minuto_actual

        if inicio_min < fin_min:
            return inicio_min <= actual_min < fin_min
        else:
            return actual_min >= inicio_min or actual_min < fin_min
    else:
        horarios_inicio, horarios_fin = calcular_horarios_dosis(GPInicio, GPFin, GPCantDosis)
        actual_min = hora_actual * 60 + minuto_actual
        for inicio_noche, fin_noche in zip(horarios_inicio, horarios_fin):
            inicio_min = tiempo_a_minutos(inicio_noche)
            fin_min = tiempo_a_minutos(fin_noche)
            if inicio_min < fin_min:
                if inicio_min <= actual_min < fin_min:
                    return True
            else:
                if actual_min >= inicio_min or actual_min < fin_min:
                    return True
        return False

def calcular_horarios_dosis(GPInicio, GPFin, GPCantDosis):
    GPCantDosis = int(GPCantDosis)
    if GPCantDosis < 2:
        GPCantDosis = 2
            
    hora_inicio = int(GPInicio[:2])
    minuto_inicio = int(GPInicio[2:])
    hora_fin = int(GPFin[:2])
    minuto_fin = int(GPFin[2:])

    minutos_inicio = hora_inicio * 60 + minuto_inicio
    minutos_fin = (hora_fin * 60 + minuto_fin) - 60

    if minutos_fin < minutos_inicio:
        minutos_fin += 1440

    intervalo_total = minutos_fin - minutos_inicio
    intervalo_dosis = intervalo_total // (GPCantDosis - 1)

    horarios_inicio = []
    horarios_fin = []

    for i in range(GPCantDosis):
        minutos_dosis_inicio = minutos_inicio + (i * intervalo_dosis)
        hora_dosis_inicio = (minutos_dosis_inicio // 60) % 24
        minuto_dosis_inicio = minutos_dosis_inicio % 60
        inicio_dosis = f"{hora_dosis_inicio:02d}{minuto_dosis_inicio:02d}"
        horarios_inicio.append(inicio_dosis)

        minutos_dosis_fin = minutos_dosis_inicio + 60
        hora_dosis_fin = (minutos_dosis_fin // 60) % 24
        minuto_dosis_fin = minutos_dosis_fin % 60
        fin_dosis = f"{hora_dosis_fin:02d}{minuto_dosis_fin:02d}"
        horarios_fin.append(fin_dosis)

    return horarios_inicio, horarios_fin

def esta_en_temporada_verano():
    fecha_actual = f"{reloj.get_time()[1]:02d}{reloj.get_time()[2]:02d}"
    if Finvierno < Fverano:
        en_invierno = Finvierno <= fecha_actual < Fverano
    else:
        en_invierno = fecha_actual >= Finvierno or fecha_actual < Fverano
    return not en_invierno

def calcular_dosis_total(Refuerzo, Dosis, DosisMin):
    multiplicador_verano = 2 if esta_en_temporada_verano() else 1
    multiplicador_refuerzo = 2 if Refuerzo == 1 else 1
    segundos = DosisMin * 60 + Dosis
    dosis_total = segundos * multiplicador_verano * multiplicador_refuerzo
    return dosis_total

def verificar_fin_horario_dosificacion():
    global DosisSi, DosisNo
    if not esta_en_horario_nocturno(InNoche, FinNoche):
        if DosisSi == 1:
            if DosisNo > 0:
                DosisNo -= 1
                eeprom.write(32, str(DosisNo).encode())
            DosisSi = 0
            eeprom.write(14, b'0')

def resetear_contador_diario():
    global CantDosis
    if not esta_en_horario_nocturno(InNoche, FinNoche):
        if DosisSi == 0:
            cant_actual = int(CantDosis)
            nueva_cant = f"{cant_actual + 1:02d}"[:2]
            if nueva_cant != f"{cant_actual:02d}":
                eeprom.write(25, nueva_cant.encode())
                CantDosis = int(nueva_cant)
                man.value(1)
                time.sleep(3)
                man.value(0)

def verificar_dosificacion():
    global estado_dosificador, tiempo_inicio_espera, tiempo_inicio_dosis, ultima_verificacion
    global DosisNo, DosisSi, ultima_dosis_saltada, Refuerzo, Espera, EsperaMin
    global CantDosis, Dosis, DosisAnulada, InNoche, FinNoche, DosisMin
    global man, ref
    ahora = time.time()
     
    if ahora - ultima_verificacion < 1.0:
        return
    ultima_verificacion = ahora
    
    tiempo_espera = EsperaMin * 60 + Espera
    dosis_si = DosisSi
    
    if estado_dosificador == "dosificando_manual":
        dosis_total = calcular_dosis_total(Refuerzo, Dosis, DosisMin)
        if ahora - tiempo_inicio_dosis >= dosis_total:
            man.value(0)
            estado_dosificador = "inactivo"
            escribir_ultdosis(40, 0)
            CantDosis = 0
            eeprom.write(25, b'00')
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo = 0
                ref.value(0)
        elif not detectar_bomba():
            man.value(0)
            estado_dosificador = "inactivo"
        return
    
    if dosis_si == 1:
        return

    if DosisNo > 0 and esta_en_horario_nocturno(InNoche, FinNoche) and detectar_bomba():
        eeprom.write(39, '1')
        DosisAnulada = "1"
        eeprom.write(14, b'1')
        DosisSi = 1
        ultima_dosis_saltada = ahora
        return
    else:
        if DosisAnulada == '1':
            eeprom.write(39, '0')
            DosisAnulada = '0'

    if estado_dosificador == "inactivo" and DosisNo == 0:
        if esta_en_horario_nocturno(InNoche, FinNoche) and detectar_bomba():
            estado_dosificador = "esperando_90s"
            tiempo_inicio_espera = ahora
    
    elif estado_dosificador == "esperando_90s":
        if not detectar_bomba():
            estado_dosificador = "inactivo"
        elif ahora - tiempo_inicio_espera >= tiempo_espera:
            if detectar_bomba():
                estado_dosificador = "dosificando"
                tiempo_inicio_dosis = ahora
                man.value(1)
            else:
                estado_dosificador = "inactivo"
    
    elif estado_dosificador == "dosificando":
        if ahora - tiempo_inicio_dosis >= calcular_dosis_total(Refuerzo, Dosis, DosisMin):
            man.value(0)
            estado_dosificador = "inactivo"
            escribir_ultdosis(40, 0)
            eeprom.write(14, b'1')
            DosisSi = 1
            eeprom.write(25, b'00')
            CantDosis = 0
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo = 0
                ref.value(0)
        elif not detectar_bomba():
            man.value(0)
            estado_dosificador = "inactivo"

def actualizar_led():
    global estado_led_actual, Refuerzo, estado_dosificador, InNoche, FinNoche
    en_horario = esta_en_horario_nocturno(InNoche, FinNoche)
    
    if estado_dosificador in ["dosificando", "dosificando_manual"]:
        base_patron = 'dosificando'
    elif en_horario:
        base_patron = 'horario'
    else:
        base_patron = 'inactivo'
    
    patron_seleccionado = f"{base_patron}_refuerzo" if Refuerzo else base_patron
    
    if estado_led_actual['patron'] != LED_PATRONES[patron_seleccionado]:
        estado_led_actual = {
            'patron': LED_PATRONES[patron_seleccionado],
            'indice': 0,
            'ultimo_cambio': time.time(),
            'estado': 0
        }
        ref.value(0)
    
    ahora = time.time()
    paso_actual = estado_led_actual['patron'][estado_led_actual['indice']]
    tiempo_transcurrido = ahora - estado_led_actual['ultimo_cambio']
    
    if tiempo_transcurrido >= paso_actual[1]:
        nuevo_estado = 1 if paso_actual[0] == 'ON' else 0
        ref.value(nuevo_estado)
        estado_led_actual['indice'] = (estado_led_actual['indice'] + 1) % len(estado_led_actual['patron'])
        estado_led_actual['ultimo_cambio'] = ahora
        estado_led_actual['estado'] = nuevo_estado

# ======================================================================
# COMUNICACIÓN BLE
# ======================================================================

# Inicializar servidor BLE
ble_server = BLEService(name="DosimatBLE")

def procesar_comando_ble(datos_str):
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, Espera, EsperaMin
    global estado_dosificador, tiempo_inicio_dosis, Dosis, DosisNo, DosisMin
    global InNoche, FinNoche, PiletaGrande, Finvierno, Fverano, GPCantDosis, GPInicio, GPFin
    
    try:
        data = json.loads(datos_str)
        comando = data.get("comando", "")
        
        if comando == "manualsi":
            if detectar_bomba():
                estado_dosificador = "dosificando_manual"
                tiempo_inicio_dosis = time.time()
                man.value(1)
            else:
                mensaje_temporal = "Error: Bomba Apagada"
                tiempo_mensaje = time.time()
        
        elif comando in ("manualno", "cancelar_dosis"):
            man.value(0)
            estado_dosificador = "inactivo"
            
        elif comando == "refuerzosi":
            ref.value(1)
            Refuerzo = 1
            eeprom.write(13, b"1")
            
        elif comando == "refuerzono":
            ref.value(0)
            Refuerzo = 0
            eeprom.write(13, b"0")
            
        elif comando == "config_fechas":
            Fverano = data.get("Fverano", Fverano)
            Finvierno = data.get("Finvierno", Finvierno)
            eeprom.write(0, Fverano.encode())
            eeprom.write(5, Finvierno.encode())
            
        elif comando == "config_horas":
            InNoche = data.get("InNoche", InNoche)
            FinNoche = data.get("FinNoche", FinNoche)
            eeprom.write(16, InNoche.encode())
            eeprom.write(20, FinNoche.encode())
            
        elif comando == "config_dosis":
            Dosis = max(0, min(int(data.get("Dosis", Dosis)), 59))
            DosisMin = max(0, min(int(data.get("DosisMin", DosisMin)), 15))
            eeprom.write(11, f"{Dosis:02d}".encode('ascii'))
            eeprom.write(9, f"{DosisMin:02d}".encode('ascii'))
            
        elif comando == "config_espera":
            Espera = max(0, min(int(data.get("Espera", Espera)), 59))
            EsperaMin = max(0, min(int(data.get("EsperaMin", EsperaMin)), 30))
            eeprom.write(30, f"{Espera:02d}".encode('ascii'))
            eeprom.write(27, f"{EsperaMin:02d}".encode('ascii'))
            
        elif comando == "config_anular":
            DosisNo = int(data.get("DosisNo", DosisNo))
            if 0 <= DosisNo <= 9:
                eeprom.write(32, str(DosisNo).encode())
                
        elif comando == "config_pileta":
            PiletaGrande_in = data.get("PiletaGrande", PiletaGrande)
            guardar_pileta(PiletaGrande_in)
            GPInicio_in = data.get("GPInicio", GPInicio)
            guardar_hora_inicio(GPInicio_in, 34)
            GPFin_in = data.get("GPFin", GPFin)
            guardar_hora_inicio(GPFin_in, 55)
            cant_dosis_in = data.get("GPCantDosis", GPCantDosis)
            guardar_cantidad_dosis(cant_dosis_in)

        elif comando == "sync_rtc":
            fecha = data.get("fecha", "")  # Formato YYYY-MM-DD
            hora = data.get("hora", "")    # Formato HH:MM
            if fecha and hora:
                year = int(fecha[:4])
                month = int(fecha[5:7])
                day = int(fecha[8:10])
                hour = int(hora[:2])
                minute = int(hora[5:7])
                second = 0
                
                # Zeller's algorithm para día de la semana (0=lunes, 6=domingo)
                m_t = month
                y_t = year
                if m_t < 3:
                    m_t += 12
                    y_t -= 1
                q = day
                K = y_t % 100
                J = y_t // 100
                h = (q + 13*(m_t + 1)//5 + K + K//4 + J//4 + 5*J) % 7
                weekday = (h + 5) % 7
                
                nueva_fecha = (year, month, day, hour, minute, second, weekday, 0)
                reloj.set_time(nueva_fecha)

    except Exception as e:
        print("BLE: Error interpretando comando", e)

ble_server.on_write(procesar_comando_ble)

# ======================================================================
# MAIN LOOP
# ======================================================================

def run_main_loop():
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, estado_dosificador
    global PiletaGrande, Espera, EsperaMin, Dosis, DosisMin, Bomba
    
    gc.collect()
    print("Servidor BLE iniciado:", version)

    ultima_verificacion_diaria = 0
    ultimo_envio_telemetria = time.time()

    while True:
        try:
            ahora = time.time()
            
            # Alimentar watchdog
            wdt.feed()
            
            # Registrar encendido/apagado de bomba
            if detectar_bomba():
                if Bomba == 0:
                    escribir_ultdosis(60, 1)
                    Bomba = 1
            else:
                if Bomba == 1:
                    escribir_ultdosis(75, 2)
                    Bomba = 0
            
            # Tareas del sistema
            verificar_fin_horario_dosificacion()
            actualizar_led()
            
            if mensaje_temporal and (ahora - tiempo_mensaje > duracion_mensaje):
                mensaje_temporal = ""
                
            if ahora - ultima_verificacion_diaria >= 90000:
                resetear_contador_diario()
                ultima_verificacion_diaria = ahora
                
            verificar_dosificacion()

            # Enviar Telemetría por BLE cada 2 segundos
            if ahora - ultimo_envio_telemetria >= 2.0:
                en_horario = esta_en_horario_nocturno(InNoche, FinNoche)
                temporada = "Verano" if esta_en_temporada_verano() else "Mantenimiento"
                t_rtc = reloj.get_time()
                fecha_str = f"{t_rtc[0]:04d}-{t_rtc[1]:02d}-{t_rtc[2]:02d}"
                hora_str = f"{t_rtc[3]:02d}:{t_rtc[4]:02d}:{t_rtc[5]:02d}"
                
                dosistot = calcular_dosis_total(Refuerzo, Dosis, DosisMin)
                
                telemetria = {
                    "version": version,
                    "estado": estado_dosificador,
                    "mensaje": mensaje_temporal,
                    "bomba": Bomba,
                    "horario": en_horario,
                    "temporada": temporada,
                    "Refuerzo": Refuerzo,
                    "DosisSi": DosisSi,
                    "DosisNo": DosisNo,
                    "Dosis": Dosis,
                    "DosisMin": DosisMin,
                    "Espera": Espera,
                    "EsperaMin": EsperaMin,
                    "Fverano": Fverano,
                    "Finvierno": Finvierno,
                    "InNoche": InNoche,
                    "FinNoche": FinNoche,
                    "PiletaGrande": PiletaGrande,
                    "GPInicio": GPInicio,
                    "GPFin": GPFin,
                    "GPCantDosis": GPCantDosis,
                    "CantDosis": CantDosis,
                    "rtc_fecha": fecha_str,
                    "rtc_hora": hora_str,
                    "dosis_total_seg": dosistot
                }
                
                ble_server.send_json(telemetria)
                gc.collect() # <-- Limpieza de RAM tras generar el JSON
                ultimo_envio_telemetria = ahora
                    
        except Exception as e:
            print(f"Error en run_main_loop: {e}")
        
        # Pausa para no bloquear el CPU
        time.sleep(0.1)

# Iniciar bucle principal
run_main_loop()