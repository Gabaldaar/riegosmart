#VERSION LINEA 305 - ACTIVAR WD
# main.py - Lógica principal de la aplicación
import socket
import time
import select
from boot import global_vars
import gc #Para chequear la memoria
from machine import WDT, ADC #Importo el WatchDog y conversor AD
#rtc = ds3231.DS3231(i2c)

ref.value(1) #Prendo el led para avisar que puede detenerse el programa
time.sleep(3) #Tiempo para detener el programa antes de que entre el WD con Ctrl + C
ref.value(0)

# Inicializar el Watchdog con un timeout de 5 segundos (en milisegundos)
wdt = WDT(timeout=10000)  # Si no se alimenta al watchdog en 5 segundos, el ESP32 se reiniciará
# wdt=WDT(0)
# Variables globales - Version con RTC DS3231
version = "V2.3"
estado_dosificador = "inactivo"
tiempo_inicio_espera = 0
tiempo_inicio_dosis = 0
ultima_verificacion = 0
mensaje_temporal = ""
tiempo_mensaje = 0
duracion_mensaje = 3  # Segundos que durará el mensaje
ultima_dosis_saltada = 0  # Timestamp de última dosis saltada


# Definicion de la varuiable para anular dosis
POS_DOSISNO = 32  # Asumiendo que 31 es la última posición usada
POS_PILETAGRANDE = 33 # 1 byte (0=False, 1=True)

# Configuración de patrones LED (segundos)
#Prende en OFF - Apaga en ON
LED_PATRONES = {
    'inactivo':             [('OFF', 0.1), ('ON', 5.0)],
    'inactivo_refuerzo':    [('OFF', 0.1), ('ON', 0.1), ('OFF', 0.1), ('ON', 5.0)],
    'horario':              [('OFF', 0.1), ('ON', 2.0)],
    'horario_refuerzo':     [('OFF', 0.1), ('ON', 0.1), ('OFF', 0.1), ('ON', 2.0)],
    'dosificando':          [('OFF', 1.0), ('ON', 1.0)],
    'dosificando_refuerzo': [('OFF', 5.0), ('ON', 0.1)]# , ('OFF', 0.1), ('ON', 0.1)]
}

estado_led_actual = {
    'patron': LED_PATRONES['inactivo'],
    'indice': 0,
    'ultimo_cambio': 0,
    'estado': 0  # 0=OFF, 1=ON
}

# Acceso a variables de hardware
man = global_vars['man'] #Es la válvula
led1 = global_vars['led1'] #Es el LED interno
ref = global_vars['ref'] #Es el LED AZUL indicador del tablero
reloj = global_vars['reloj'] #Es el RTC
eeprom = global_vars['eeprom'] #Es la EEPROM del RTC

# Configuración del pin ADC donde está conectado el sensor
adc = ADC(Pin(34))  # Cambia 34 por el pin que estás utilizando
adc.atten(ADC.ATTN_11DB)  # Configura la atenuación para mayor rango de lectura

# Define un umbral para la presencia de tensión
UMBRAL_TENSION = 2.75  # Ajusta este valor según las lecturas de tu sensor

#Lectura e nicialización de variables desde EEPROM
#------------------------------------------------------------
     
    # Primero define los defaults
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
    'EsperaMin': '01', # Minutos de Espera (0-30)
    'Espera': '30', # Segundos de Espera (0-59)
    'Dosis': '30',     # Nuevo default (0-59)
    'DosisMin': '01'    # Nueva variable (0-15)
}

variables = {
    'Fverano': (0, 4, False), #Comienzo de dosis doble
    'Finvierno': (5, 4, False), #Comienzo de dosis de mantenimiento
    'Refuerzo': (13, 1, False), 
    'DosisSi': (14, 1, False),
    'InNoche': (16, 4, False), #Inicio de horario de dosificación
    'FinNoche': (20, 4, False), #Fin de horario de dosificación
    'CantDosis': (25, 2, False),
    'EsperaMin': (27, 2, False), #Minutos de espera entre 0-30-Def:1
    'Espera': (30, 2, False),    #Segundos de espera entre 0-59-Def:30
    'DosisNo': (32, 1, False),
    'PiletaGrande': (33, 1, False), #1 Indica que está en modo Pileta Pública
    'GPInicio': (34, 4, False), #Inicio del horario de dosificación para piletas públicas
    'GPCantDosis': (38, 1, False), #Cantidad de dosis para piletas públicas
    'DosisAnulada': (39, 1, False), #Cantidad de Dosis que se van a saltear   
    'UltDosis': (40, 12, False) ,
    'UltBomba': (60, 12, False) ,
    'UltBombaAp': (75, 12, False) ,
    'GPFin': (55, 4, False), #Fin del horario de dosificación para piletas públicas
    'DosisMin': (9, 2, False),   # Nueva variable (0-15)
    'Dosis': (11, 2, False),     # Cambiada de (9,4,True) a (10,1,False)
}

def inicializar_eeprom_si_es_necesario(eeprom, defaults, variables):
    for nombre, (addr, length, _) in variables.items():
        try:
            valor = eeprom.read(addr, length)
            if valor == b'\xff' * length:
                raise ValueError("EEPROM vacía")
            valor.decode('utf-8')  # Verifica si es texto válido
        except:
            # Si falla la lectura o decodificación, escribe el valor por defecto
            valor_defecto = str(defaults[nombre])
            eeprom.write(addr, valor_defecto.encode('utf-8'))
            print(f"Inicializado {nombre} con '{valor_defecto}'")

# Llamada a la rutina
inicializar_eeprom_si_es_necesario(eeprom, defaults, variables)

#Registrar reinicio
#------------------------------------------------------------------------------------
t = reloj.get_time()
UltInicio = f"{t[2]:02d}/{t[1]:02d}/{t[0]} - {t[3]:02d}:{t[4]:02d}" 

#Escribir UltDosis
# ---------------------------------------------------    
def escribir_ultdosis(direcc,caso ):
    # direcc es la direccion eeprom y caso es para diferenciar si es dodis o bomba
    global UltDosis, UltBomba,UltBombaAp
    """
    Escribe la fecha y hora actuales en la EEPROM si los datos no son válidos.
    """
    try:
        # Obtener fecha y hora actuales del RTC
        fecha_hora_actual = reloj.get_time()
        ult_dosis_str = (
            f"{fecha_hora_actual[0]:04d}{fecha_hora_actual[1]:02d}"
            f"{fecha_hora_actual[2]:02d}{fecha_hora_actual[3]:02d}"
            f"{fecha_hora_actual[4]:02d}"
        )  # Formato: YYYYMMDDHHMM
        ult_dosis_bytes = bytes(ult_dosis_str, 'utf-8')
        if caso ==0: #Está guradando últioma dosis
            UltDosis=ult_dosis_bytes
            # Escribir los datos en la EEPROM
            eeprom.write(40, ult_dosis_bytes)  # Escribir 12 bytes en la dirección 43
        elif caso==1: # Guarda u´ltimo enc. de bomba
            UltBomba=ult_dosis_bytes
            # Escribir los datos en la EEPROM
            eeprom.write(60, ult_dosis_bytes)  # Escribir 12 bytes en la dirección 60
        else:
            UltBombaAp=ult_dosis_bytes
            # Escribir los datos en la EEPROM
            eeprom.write(75, ult_dosis_bytes)  # Escribir 12 bytes en la dirección 60
        return ult_dosis_str
    except Exception as e:
        print(f"Error al escribir en la EEPROM: {e}")
        return None

#------------------------------------------------------------------
Bomba=0 #Para registrar el último horario de encendido
Fverano = eeprom.read(0, 4).decode('utf-8')
Finvierno = eeprom.read(5, 4).decode('utf-8')
Refuerzo= int(eeprom.read(13, 1)) if hasattr(eeprom, 'read') else 0
DosisSi = int(eeprom.read(14, 1))
InNoche = eeprom.read(16, 4).decode('utf-8')
FinNoche = eeprom.read(20, 4).decode('utf-8')
CantDosis = int(eeprom.read(25, 2).decode('utf-8'))
GPInicio =(eeprom.read(34, 4).decode('utf-8')) # Hora primera dosis
GPFin =(eeprom.read(55, 4).decode('utf-8')) # Hora primera dosis
GPCantDosis=(eeprom.read(38, 1).decode('utf-8')) # Cant. dosis diarias
DosisAnulada=eeprom.read(39,1).decode('utf-8') #Lee si la dosis fue anulada por el usuario
PiletaGrande = int(eeprom.read(33, 1).decode('utf-8'))
DosisNo = int(eeprom.read(32, 1).decode('utf-8'))
UltDosis= eeprom.read(40, 12)
UltBomba= eeprom.read(60, 12)
UltBombaAp= eeprom.read(75, 12)
Espera = int(eeprom.read(30, 2).decode('ascii').strip('\x00'))
Dosis = int(eeprom.read(11, 2).decode('ascii').strip('\x00'))
DosisMin = int(eeprom.read(9, 2).decode('ascii').strip('\x00'))
EsperaMin = int(eeprom.read(27, 2).decode('ascii').strip('\x00'))

#FUNCION PARA DETECTAR ENTRADA DE BOMBA EN EL ADC
#-------------------------------------------------
def detectar_bomba():
    # Leer el valor del ADC y convertirlo a voltios
    lectura_adc = adc.read()  # Valor entre 0 y 4095
    voltaje = (lectura_adc / 4095) * 3.3  # Convertir lectura ADC a voltios
    # Determinar si hay tensión presente
    return voltaje > UMBRAL_TENSION  # Retorna True si hay tensión
#----------------------------------------------------
# Funciones auxiliares
def formatear_fecha(fecha):
    return f"{fecha[2:]}/{fecha[:2]}"

def formatear_hora(hora):
    return f"{hora[:2]}:{hora[2:]}"


#FUNCION PARA GUARDAR EL INGRESO DE PILETAS
#----------------------------------------------------------------
def guardar_pileta(valor):
    """Guarda el estado de pileta grande (1) o chica (0) como entero"""
    try:
        # Conversión robusta a 1 o 0
        valor_guardar = 1 if str(valor).lower() in ('1', 'true', 'on', 'si', 'yes') else 0
        
        # Guardar en EEPROM
        eeprom.write(POS_PILETAGRANDE, str(valor_guardar).encode())
        
        # Actualizar variable global (como entero)
        global PiletaGrande
        PiletaGrande = valor_guardar
        
        return True
    except Exception as e:
        print(f"Error al guardar: {e}")
        # Resetear a 0 si hay error
        eeprom.write(POS_PILETAGRANDE, b'0')  
        PiletaGrande = 0
        return False    
#-----------------------------------------------------------------    
def guardar_hora_inicio(hora_str, POS):
    global GPInicio, GPFin
    #Guarda la hora de primera dosis en formato HHMM
    try:
        hora = hora_str.replace(":", "")[:4]  # Convierte "08:00" -> "0800"
        if len(hora) == 4 and hora.isdigit():
            if POS == 34:
                GPInicio =hora.encode().decode('utf-8')
            elif POS == 55:
                GPFin =hora.encode().decode('utf-8')
            eeprom.write(POS, hora.encode())  # Posición 34 para hora inicio
            return True
    except Exception as e:
        print(f"Error al guardar hora inicio: {e}")
    return False

def guardar_cantidad_dosis(cantidad):
    global GPCantDosis
    """Guarda la cantidad de dosis diarias (1 byte)"""
    try:
        if cantidad in {'2', '3', '4','5', '6','7', '8'}:
            GPCantDosis = cantidad
            eeprom.write(38, cantidad.encode())  # Posición 38 para cantidad
            return True
    except Exception as e:
        print(f"Error al guardar cantidad dosis: {e}")
    return False   
#------------------------------------------------------------------
# PAGINAS WEB
#PAGINA DE INICIO
#-----------------------------------------------------------
def web_page():
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo
    #gc.collect()  # Liberar basura antes de conectar
    # Limpiar mensaje si ha expirado
    estado_actual = "Pública" if PiletaGrande == 1 else "Hogar"
    tipodosis = "Verano" if esta_en_temporada_verano() else "Mantenimiento"
    #Version = version
    if mensaje_temporal and (time.time() - tiempo_mensaje > duracion_mensaje):
        mensaje_temporal = ""
    refuerzo = Refuerzo 
    estadoref = "Encendido" if Refuerzo == 1 else "Apagado"
    dosistot = calcular_dosis_total(Refuerzo, Dosis, DosisMin)
    m,s = dosistot//60, (dosistot%60)%60
    total_segundos = EsperaMin * 60 + Espera
    estadoman = {
        "inactivo": "Inactivo",
        "esperando_90s": f"Esperando {total_segundos}Seg.",
        "dosificando": f"Dosificando {m}m:{s}s",
        "dosificando_manual": "Dosis Manual en Curso"
    }.get(estado_dosificador, "Inactivo")
    
    colors = {
        "man": "#33cd1c" if man.value() == 1 else "grey",
        "refuerzo": "#f11313" if Refuerzo == 1 else "grey",
        "bomba": "yellow" if detectar_bomba() == True else "grey",
        "horario": "yellow" if esta_en_horario_nocturno(InNoche, FinNoche) else "grey"
    }
    estadobomb = "Encendida" if detectar_bomba() == True else "Apagada"
    habilitacion = "En Horario de Dosificación" if esta_en_horario_nocturno(InNoche, FinNoche) else "Fuera del Horario de Dosificación"

    if DosisAnulada == '1':
        estado = "Anulada por el usuario"
    elif DosisSi == 1:
        estado = "Completada"
    else:
        estado = "Pendiente"
    mensaje_html = f'<div style="color:red; font-weight:bold;">{mensaje_temporal}</div>' if mensaje_temporal else ''
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="2">
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
.btn{{padding:6px 16px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer;font-size:14px;margin:2px}}
.btn-no{{background:#f44336}}
.estado{{display:inline-block;width:20px;height:20px;border-radius:50%;border:2px solid #000;margin:0 10px;vertical-align:middle}}
</style>
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin:0">Controles del dosificador</h3>
</div>    
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<a>Control | </a><a href="/config">Fechas | </a><a href="/config_horas">Horas | </a><a href="/config_dosis">Dosis</a><br>
<a href="/config_espera">Espera | </a><a href="/anular_dosis">Anular | </a><a href="/config_pileta">Pileta | </a><a href="/config_sist">Sistema</a>
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<h3 style="margin:0">Dosis Manual</h3>  
<span class="estado" style="background:{colors["man"]}"></span>  
<a class="btn" href="/manualsi">SI</a>
<a class="btn btn-no" href="/manualno">NO</a>   
<span style="margin-left:10px">{estadoman}</span><br><br>  
<h3 style="margin:0">Refuerzo</h3>  
<span class="estado" style="background:{colors["refuerzo"]}"></span> 
<a class="btn" href="/refuerzosi">SI</a>  
<a class="btn btn-no" href="/refuerzono">NO</a>  
<span style="margin-left:10px">{estadoref}</span><br><br>
<h3 style="margin:0">Bomba</h3>
<span class="estado" style="background:{colors["bomba"]}"></span>   
<span style="margin-left:10px">{estadobomb}</span><br><br>
<h3 style="margin:0">Horario de dosificación</h3>
<span class="estado" style="background:{colors["horario"]}"></span>   
<span style="margin-left:10px">{habilitacion}</span><br>
<p><strong>Dosis de hoy:</strong> <span style="color:{"green" if DosisSi else "red"}">{estado}</span></p>
<p><strong>Dosis:</strong> {tipodosis}</p>
<p><strong>Tipo de Pileta:</strong> {estado_actual}</p>
{mensaje_html}<hr>
<p><strong>Dosis Manual</strong> agrega una dosis en cualquier momento. La bomba debe estar encendida. Considera el Refuerzo.</p>
</body>
</html>"""
    return html

#PAGINA DE FECHAS
#-----------------------------------------------------------
def pagina_configuracion():
    global Fverano, Finvierno
    #gc.collect()
    FveranoPrint = formatear_fecha(Fverano)
    FinviernoPrint = formatear_fecha(Finvierno)
    año_actual = reloj.get_time()[0]
    fecha_verano = f"{año_actual}-{Fverano[:2]}-{Fverano[2:]}"
    fecha_invierno = f"{año_actual}-{Finvierno[:2]}-{Finvierno[2:]}"
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
h1,h3{{margin:0}}
.btn{{padding:10px 20px;background:#4CAF50;color:#fff;border:none;border-radius:4px;cursor:pointer}}
input[type=date]{{margin-bottom:10px}}
</style>
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3>Config. Fechas de Cambio de Dosis</h3>
</div>
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<a href=/control>Control | </a><a>Fechas | </a><a href=/config_horas>Horas | </a><a href=/config_dosis>Dosis</a><br>
<a href=/config_espera>Espera | </a><a href=/anular_dosis>Anular | </a><a href=/config_pileta>Pileta | </a><a href=/config_sist>Sistema</a>
<hr>
<form action=/guardar_configuracion method=GET>
<label for=fechai>Inicio Dosis Mant.:&#160&#160</label> <input type=date id=fechai name=fechai value={fecha_invierno} style='width:100px'required><br>
<label for=fechav>Inicio Dosis Verano:</label> <input type=date id=fechav name=fechav value={fecha_verano} style='width:100px'required><br><br>
<input type=submit class=btn value="Guardar">        
</form>
<hr>
<p><strong>Inicio Dosis Mant.:</strong> Fin de dosis de Verano, Inicio de dosis de mantenimiento.</p>
<p><strong>Inicio Dosis Verano:</strong> Inicio de dosis de Verano (Doble de la de Mantenimiento.).</p>
</body>
</html>"""
    return html

#PAGINA DE HORAS
#-----------------------------------------------------------
def pagina_config_horas(InNoche,FinNoche,UltBomba,UltBombaAp):
    #gc.collect()  # Liberar basura antes de conectar
    HoraInPrint = formatear_hora(InNoche)
    HoraFinPrint = formatear_hora(FinNoche)
    ult_bomba_str = f"{int(UltBomba[6:8]):02d}/{int(UltBomba[4:6]):02d}/{UltBomba[:4].decode()} - {int(UltBomba[8:10]):02d}:{int(UltBomba[10:12]):02d}"       
    ult_bombaap_str = f"{int(UltBombaAp[6:8]):02d}/{int(UltBombaAp[4:6]):02d}/{UltBombaAp[:4].decode()} - {int(UltBombaAp[8:10]):02d}:{int(UltBombaAp[10:12]):02d}"       
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
<div style="background:#64daf8; padding:10px;">
   <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin: 0;">Config. de Horario de habilitación</h3>
 </div>     
    <hr style="margin:10px 0;border:0;height:1px;background:#ccc">
    <a href="/control">Control | </a><a href="/config">Fechas | </a><a>Horas | </a><a href="/config_dosis">Dosis</a><br>
    <a href="/config_espera">Espera | </a><a href="/anular_dosis">Anular | </a><a href="/config_pileta">Pileta | </a><a href="/config_sist">Sistema</a>
    <hr>
  
    <form action="/guardar_horas" method="GET">
        <label>Hora de Inicio:</label> <input type="time" id="horainicio" name="horainicio" value="{HoraInPrint}" style='width:80px' required><br><br>
        <label>Hora de Fin:&#160&#160&#160&#160</label> <input type="time" id="horafin" name="horafin" value="{HoraFinPrint}" style='width:80px'required><br><br>
        <input type="submit" value="Guardar" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer">        
    </form>
    <p><strong>Últ. Enc. Bomba:</strong> {ult_bomba_str}</p>
    <p><strong>Últ. Apag. Bomba:</strong> {ult_bombaap_str}</p>
    <hr>
    <ul>   
        <li>Horario de habilitación del dosificado (Normalmente nocturno).</li>
        <li>El encendido de la <strong>bomba</strong> debe configurarse dentro de estos horarios.</li>    
    </ul>
</body>
</html>"""
    return html

#PAGINA DE DOSIS 
#-----------------------------------------------------------
def pagina_config_dosis(Dosis, DosisMin):
    #gc.collect()
    Dosis = min(max(0, int(Dosis)), 59)
    DosisMin = min(max(0, int(DosisMin)), 15)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin:0">Configuración de la Dosis</h3>
</div>    
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<a href="/control">Control | </a><a href="/config">Fechas | </a><a href="/config_horas">Horas | </a><a>Dosis</a><br>
<a href="/config_espera">Espera | </a><a href="/anular_dosis">Anular | </a> <a href="/config_pileta">Pileta | </a><a href="/config_sist">Sistema</a>
<hr>
<form action="/guardar_dosis">
    <label>Minutos (0-15):&#160</label>
    <input type='number' name='minutos' value='{DosisMin}' min='0' max='15' 
       style='width:20px; padding:5px; margin:5px 0' required><br>
    <label>Segundos(0-59):</label>
    <input type='number' name='segundos' value='{Dosis}' min='0' max='59' 
       style='width:20px; padding:5px; margin:5px 0' required><br><br>
    <input type="submit" value="Guardar" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer">        
</form>
<hr>
<ul>
    <li>La dosis representa el tiempo durante el cual se agrega cloro a la pileta.</li>
    <li>La Dosis configurada aquí corresponde al modo <strong>Mantenimiento</strong>. Recuerde que en el período de Verano se duplica.</li>
    <li><strong><span style="color: red;">Ajuste con precaución.</span></strong></li>
</ul>    
</body></html>"""

#PAGINA DE ESPERA
#-----------------------------------------------------------
def pagina_config_espera(Espera,EsperaMin,version):
    #gc.collect()
    Espera = min(max(0, int(Espera)), 59)  
    EsperaMin = min(max(0, int(EsperaMin)), 30)
    Version=version
    html_template = """<!DOCTYPE html><html><head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {Version}</span></h1>
    <h3 style='margin:0'>Configuración del Tiempo de Espera</h3>
</div>    
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<a href='/control'>Control | </a> 
<a href='/config'>Fechas | </a> 
<a href='/config_horas'>Horas | </a> 
<a href='/config_dosis'>Dosis</a><br>
<a>Espera | </a> 
<a href='/anular_dosis'>Anular | </a> 
<a href='/config_pileta'>Pileta | </a> 
<a href='/config_sist'>Sistema</a>
<hr>
<form action='/guardar_espera'>

<label>Minutos (0-30):&#160</label>
<input type='number' name='minutos' value='{esperamin}' min='0' max='30' 
       style='width:20px; padding:5px; margin:5px 0' required><br>
<label>Segundos(0-59):</label>
<input type='number' name='segundos' value='{espera}' min='0' max='59' 
       style='width:20px; padding:5px; margin:5px 0' required><br><br>
<input type='submit' value='Guardar' 
       style='padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer'>
</form><hr>
<p>Tiempo entre el encendido de la bomba y el inicio de la dosificación, para que el caudal entre en regimen.</p>
</body></html>"""
    
    try:
        return html_template.format(espera=Espera, esperamin=EsperaMin, Version=version)
    except MemoryError:
        gc.collect()
        return "<html><body>Error: memoria insuficiente</body></html>"
    

#PAGINA DE ANULACION DE DOSIS
#-----------------------------------------------------------
def pagina_anular_dosis():
    global DosisNo
    #gc.collect()
    opciones = ''.join(f'<option value="{i}"{" selected" if i==DosisNo else ""}>{i}</option>' for i in range(10))

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
<div style="background:#64daf8; padding:10px;">
   <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
   <h3 style="margin: 0;">Anulación de Próximas Dosis</h3>
</div>    
    <hr style="margin:10px 0;border:0;height:1px;background:#ccc">
    <a href="/control">Control | </a><a href="/config">Fechas | </a><a href="/config_horas">Horas | </a><a href="/config_dosis">Dosis</a><br>
    <a href="/config_espera">Espera | </a><a>Anular | </a><a href="/config_pileta">Pileta | </a><a href="/config_sist">Sistema</a>
    <hr>
    <form action="/guardar_anular">
        <br><label>Dosis a anular:</label>
        <select name="dosisno">{opciones} style='width:70px'</select><br><br>
       <input type="submit" value="Guardar" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer">        
    </form>
    <p><strong>Configuración actual:</strong> Anular {DosisNo} dosis siguientes.</p>
    <hr>
<ul>
        <li>No se recomienda anular más de 2 dosis consecutivas.</li>
        <li>El sistema retomará la dosificación automáticamente.</li>
    </ul>
</body>
</html>"""
    return html


#PAGINA DE SISTEMA
#-----------------------------------------------------------
def pagina_config_sist(UltDosis,UltInicio):
    #gc.collect()  # Liberar basura antes de conectar
    fecha_hora_actual = reloj.get_time()
    fecha_formateada = f"{fecha_hora_actual[0]:04d}-{fecha_hora_actual[1]:02d}-{fecha_hora_actual[2]:02d}"
    hora_inicial = f"{fecha_hora_actual[3]:02d}:{fecha_hora_actual[4]:02d}"
    ult_dosis_str = f"{int(UltDosis[6:8]):02d}/{int(UltDosis[4:6]):02d}/{UltDosis[:4].decode()} - {int(UltDosis[8:10]):02d}:{int(UltDosis[10:12]):02d}"       
 #   temp_str = rtc.temperature() 
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
</head>
<body>
<div style="background:#64daf8; padding:10px;">
   <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin: 0;">Configuración del Sistema</h3>
</div>    
    <hr style="margin:10px 0;border:0;height:1px;background:#ccc">
    <a href="/control">Control | </a><a href="/config">Fechas | </a><a href="/config_horas">Horas | </a><a href="/config_dosis">Dosis</a><br>
    <a href="/config_espera">Espera | </a><a href="/anular_dosis">Anular | </a><a href="/config_pileta">Pileta | </a><a>Sistema</a>
    <hr>
    <form action="/Enviar" method="GET">
        <br><label>Fecha:</label> <input type="date" name="date" value="{fecha_formateada}" style='width:100px'required><br><br>
        <label>Hora:&#160&#160</label> <input type="time" name="time" value="{hora_inicial}" style='width:80px' required><br><br>
        <button type="button" onclick="actualizarHora()" style="padding:10px 20px;background:#2196F3;color:white;border:none;border-radius:4px;cursor:pointer;margin-right:10px">
        Actualizar
        </button>
       <input type="submit" value="Guardar" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer"> 
    </form>
    <p><strong>Última Dosis:</strong> {ult_dosis_str}</p>
    <p><strong>Último Inicio:</strong> {UltInicio}</p>
    <hr>
    <p>Configure Fecha y Hora del sistema. Actualizar toma los datos del móvil.</p>
    
        <script>
        function actualizarHora() {{
            const ahora = new Date();
            const fecha = ahora.toISOString().slice(0, 10); // YYYY-MM-DD
            const hora = ahora.toTimeString().slice(0, 5);  // HH:MM
            document.querySelector('input[name="date"]').value = fecha;
            document.querySelector('input[name="time"]').value = hora;
        }}
        </script>

    </body>
    </html>"""
    return html

#PAGINA CONGIGURACION OK
#-----------------------------------------------------------
def pagina_config_ok():
    #gc.collect()  # Liberar basura antes de conectar
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin: 0;">Configuración Guardada Correctamente</h3>
</div>    
    <hr style="margin:10px 0;border:0;height:1px;background:#ccc">
    <a href=/control>Control | </a><a href=/config>Fechas | </a><a href=/config_horas>Horas | </a><a href=/config_dosis>Dosis</a><br>
    <a href=/config_espera>Espera | </a><a href=/anular_dosis>Anular | </a><a href=/config_pileta>Pileta | </a><a href=/config_sist>Sistema</a>
    <hr>
    <button onclick="location.href='/control'" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer">Volver a Control</button> 
    <br>
    <hr>
</body>
</html>"""
    return html

    
#PAGINA DE DOSIS MANUAL - Acá me manda al poner /manualsi por el problema del refresco
#---------------------------------------------------------
def dosis_manual(dosis_total):
    #gc.collect()  # Liberar basura antes de conectar
    R = Refuerzo #int(eeprom.read(13, 1))
    #h,m,s = dosis_total//3600, (dosis_total%3600)//60, dosis_total%60
    m,s = dosis_total//60, (dosis_total%60)%60
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
.b{{background:#4CAF50;color:#fff;border:none;border-radius:4px;padding:10px}}
.s{{background:#e74c3c}}
h1,h3{{margin:0}}
</style>
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3>Dosis Manual</h3>
</div>    
<hr style="margin:10px 0;border:0;height:1px;background:#ccc">
<a href=/control>Control | </a><a href=/config>Fechas | </a><a href=/config_horas>Horas | </a><a href=/config_dosis>Dosis</a><br>
<a href=/config_espera>Espera | </a><a href=/anular_dosis>Anular | </a><a href=/config_pileta>Pileta | </a><a href=/config_sist>Sistema</a>
<hr>
<h3>Dosis Manual en Curso</h3>
<p><strong>Refuerzo:</strong> {"Encendido"if R else"Apagado"}</p>
<p><strong>Dosis:</strong> {"Verano"if esta_en_temporada_verano()else"Mantenimiento"}</p>
<p><strong>Tiempo:</strong> {m}m:{s}s</p>
<button onclick="location.href='/cancelar_dosis'" class="b s">⚠️ Detener</button>
<button onclick="location.href='/control'" class=b>Volver a Control</button>
<hr>
</body>
</html>"""
    return html

 #PAGINA CONFIRACION DE PILETA GDE-CHICA
#----------------------------------------------	
def pagina_config_pileta(PiletaGrande,GPInicio,GPFin,GPCantDosis):
    #gc.collect()  # Liberar basura antes de conectar
    estado_actual = PiletaGrande
    try:
        GPInicioPrint = formatear_hora(str(GPInicio)) if GPInicio else "08:00"
        GPFinPrint = formatear_hora(str(GPFin)) if GPFin else "20:00"
    except:
        GPInicioPrint = "08:00"
        GPFinPrint = "20:00"
 
    GPCantDosis = str(GPCantDosis) if 'GPCantDosis' in globals() else "2"
    inicio, fin = calcular_horarios_dosis(GPInicio,GPFin,GPCantDosis)
    horarios_str = formatear_horarios(inicio, fin)
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"> 
    <meta charset="UTF-8">
 
</head>
<body>
<div style="background:#64daf8; padding:10px;">
    <h1 style="margin:0; display:inline-block;">DOSIMAT IoT<span style="font-size:0.6em; vertical-align:baseline;"> _ {version}</span></h1>
    <h3 style="margin: 0;">Configuración Tipo de Pileta</h3>
</div>    
    <hr style="margin:10px 0;border:0;height:1px;background:#ccc">
    <a href="/control">Control | </a><a href="/config">Fechas | </a><a href="/config_horas">Horas | </a><a href="/config_dosis">Dosis</a><br>
    <a href="/config_espera">Espera | </a><a href="/anular_dosis">Anular | </a><a>Pileta | </a><a href="/config_sist">Sistema</a>

<hr style="margin: 10px 0; border: 0; height: 1px; background-color: #ccc;">
<br>
    <form action="/setpileta" method="GET">
        <label class="switch">
            <input type="checkbox" name="pileta" {'checked' if estado_actual == 1 else ''}><label>Pileta Pública</label>
            <span class="slider"></span>
        </label>
        <br><br>
        <label for="primdosis">Primera Dosis:</label>
        <input type="time" id="primdosis" name="horainicio" style='width:80px' required value="{GPInicioPrint}">
        <br><br>
        <label for="ultdosis">Última Dosis:&#160&#160&#160</label>
        <input type="time" id="ultdosis" name="horafin" style='width:80px' required value="{GPFinPrint}">
        <br><br>
               <label for="dosisdiarias">Dosis diarias:&#160&#160&#160</label>
        <select id="dosisdiarias" name="cant_dosis" style='width:30px' required>
            <option value="2" {'selected' if GPCantDosis == '2' else ''}>2</option>
            <option value="3" {'selected' if GPCantDosis == '3' else ''}>3</option>
            <option value="4" {'selected' if GPCantDosis == '4' else ''}>4</option>
            <option value="5" {'selected' if GPCantDosis == '5' else ''}>5</option>
            <option value="6" {'selected' if GPCantDosis == '6' else ''}>6</option>
            <option value="7" {'selected' if GPCantDosis == '7' else ''}>7</option>
            <option value="8" {'selected' if GPCantDosis == '8' else ''}>8</option>
        </select>
        <br><br>
        <input type="submit" value="Guardar" style="padding:10px 20px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer"><br><br>
        <label><strong>Horarios habilitados: </strong><br>{horarios_str}</label>
    </form>
    <hr style="margin: 20px 0; border: 0; height: 1px; background-color: #ddd;">
<ul>
    <li>Para piletas que requieran varias dosis al día, tilde <strong>Pileta Pública</strong>.</li>
    <li>Los horarios de dosificación se espaciarán uniformemente entre los de la Primera y la Última dosis.</li>
    <li>Cada habilitación durará 1 hora. Asegúrese de programar el encendido de la bomba dentro de estos tiempos.</li>    
</ul>  
</body>
</html>"""
    return html
 

#MANEJO DE REQUESTS
#-----------------------------------------------------------
def handle_request(path):
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, Espera,EsperaMin
    global estado_dosificador, tiempo_inicio_dosis, dosis_total, Dosis, DosisNo, DosisMin
    global InNoche, FinNoche, PiletaGrande, Finvierno, Fverano, GPCantDosis,GPInicio,GPFin
    
    gc.collect()  # Liberar basura antes de conectar
    if path == '/':
        response = web_page() # Respuesta inicial 
    elif path == '/cancelar_dosis':
        man.value(0)
        estado_dosificador = "inactivo"
        return web_page()
    elif path == '/config':
        response = pagina_configuracion()
    elif path == '/control':
        response = web_page( )
    elif path == '/config_pileta':
        response = pagina_config_pileta(PiletaGrande,GPInicio,GPFin,GPCantDosis)          
    elif path == '/config_horas': 
        response = pagina_config_horas(InNoche,FinNoche,UltBomba,UltBombaAp)    
    elif path == '/config_dosis':  
        content = pagina_config_dosis(Dosis,DosisMin)
        headers = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
        return headers + content        
    elif path=='/anular_dosis':
        response = pagina_anular_dosis()
    elif path == '/config_sist':
        response = pagina_config_sist(UltDosis,UltInicio)       
    elif path == '/config_espera':
        response = pagina_config_espera(Espera,EsperaMin,version)        
 #--------------------------------------------   
    elif path.startswith('/setpileta'):
        try:
            params = path.split('?')[1]
            param_dict = dict(pair.split('=') for pair in params.split('&') if '=' in pair)
            
            # Procesar checkbox de pileta (puede venir como 'on' o no venir)
            pileta_value = 1 if 'pileta' in param_dict else 0
            
            # Guardar configuración
            if guardar_pileta(pileta_value):
                # Procesar otros parámetros (horainicio, cant_dosis)
                if 'horainicio' in param_dict:
                    # Decodificar el valor de hora_inicio antes de guardarlo
                    horainicio_decodificada = param_dict['horainicio'].replace('%3A', ':')
                    POS=34
                    #GPInicio= horainicio
                    guardar_hora_inicio(horainicio_decodificada, POS)
                if 'horafin' in param_dict:
                    # Decodificar el valor de hora_inicio antes de guardarlo
                    horafin_decodificada = param_dict['horafin'].replace('%3A', ':')
                    POS=55
                    guardar_hora_inicio(horafin_decodificada, POS)                
                if 'cant_dosis' in param_dict:
                    guardar_cantidad_dosis(param_dict['cant_dosis'])
                
                return pagina_config_ok()
            else:
                return "HTTP/1.0 500 Error\r\n\r\nError al guardar configuración"
                
        except Exception as e:
            print(f"Error en /setpileta: {e}")
            return "HTTP/1.0 400 Error\r\n\r\nDatos inválidos"   
#-----------------------------------------------    
    elif path.startswith('/guardar_anular'):
        try:
            params = path.split('?')[1]
            dosisno = int(params.split('=')[1])
            if 0 <= dosisno <= 9:
                DosisNo = dosisno
                eeprom.write(32, str(dosisno).encode())
                return pagina_config_ok()
        except:
            pass
        return "HTTP/1.0 400 Bad Request\r\n\r\nValor inválido"
    elif path.startswith('/guardar_horas'):  # Guardar las horas
        # Procesar parámetros de URL
        params = path.split('?')[1]
        param_dict = dict(x.split('=') for x in params.split('&'))
        hinicio = param_dict.get('horainicio', '')  # Hora de inicio
        hfin = param_dict.get('horafin', '')        # Hora de fin
        horainicio=f"{hinicio[:2]}{hinicio[5:7]}"
        horafin=f"{hfin[:2]}{hfin[5:7]}"
        # Guardar las horas en la EEPROM
        InNoche = horainicio
        FinNoche = horafin
        eeprom.write(16, horainicio.encode())  
        eeprom.write(20, horafin.encode())    
        # Redirigir a la página de confirmación
        response = pagina_config_ok()    
    elif path.startswith('/guardar_configuracion'):  
        # Procesar parámetros de URL
        params = path.split('?')[1]
        param_dict = dict(x.split('=') for x in params.split('&'))
        fechai = param_dict.get('fechai', '')  # Fecha invierno
        fechav = param_dict.get('fechav', '')  # Fecha verano
        #Extrae mes y dia de las fechas ingresadas - El año se descarta
        mesv=fechav[5:7]
        diav=fechav[8:10]
        mesi=fechai[5:7]
        diai=fechai[8:10]
        Finvierno=f"{mesi}{diai}"
        Fverano=f"{mesv}{diav}"
        # Guardar las fechas en la EEPROM
        eeprom.write(0, Fverano.encode())  # Guardar `fechav`
        eeprom.write(5, Finvierno.encode())  # Guardar `fechai`
        # Redirigir a la página de confirmación
        response = pagina_config_ok()    
    elif path.startswith('/guardar_dosis'):
        # Procesar los datos enviados para guardar Dosis
        params = path.split('?')[1]
        param_dict = dict(x.split('=') for x in params.split('&'))
        
        # Obtener directamente los segundos (nuevo formato)
        segundos = int(param_dict.get('segundos', '30'))  # Cambio clave aquí
#         segundos = max(0, min(segundos, 59)) # Rango (0-59)
        Dosis = segundos
        #print("Dosis",Dosis)
        eeprom.write(11, f"{Dosis:02d}".encode('ascii'))
        #Obtener y validar el valor de 'minutos'
        minutos = int(param_dict.get('minutos', '1'))
        #minutos = max(0, min(minutos, 15))  # Rango (0-15)
        #minutos = 2
        DosisMin = minutos
        eeprom.write(9, f"{DosisMin:02d}".encode('ascii'))        
        response = pagina_config_ok()
    elif path.startswith('/Enviar'):
        params = path.split('?')[1]
        param_dict = dict(x.split('=') for x in params.split('&'))
        
        fecha = param_dict.get('date', '')
        hora = param_dict.get('time', '')
        if fecha and hora:
            # Extraer componentes de fecha
            year = int(fecha[:4])
            month = int(fecha[5:7])
            day = int(fecha[8:10])
            # Extraer componentes de hora
            hour = int(hora[:2])
            minute = int(hora[5:7])
            second = 0  # Podrías cambiarlo si quieres permitir segundos
            # Obtener día de la semana (0=lunes, 6=domingo)
            # Usamos el algoritmo de Zeller para calcular el día de la semana
            # IMPORTANTE: usar variables temporales para no modificar los valores originales
            month_temp = month
            year_temp = year
            if month_temp < 3:
                month_temp += 12
                year_temp -= 1
            q = day
            m = month_temp
            K = year_temp % 100
            J = year_temp // 100
            h = (q + 13*(m + 1)//5 + K + K//4 + J//4 + 5*J) % 7
            # Ajustar para que 0=domingo, 1=lunes,...,6=sábado (como espera el RTC)
            weekday = (h + 5) % 7
            # Crear tupla para el RTC (año, mes, día, hora, minuto, segundo, weekday, 0)
            nueva_fecha = (year, month, day, hour, minute, second, weekday, 0)
            # Configurar el RTC
            reloj.set_time(nueva_fecha)
            response = pagina_config_ok()
        else:
            response = "HTTP/1.0 400 Bad Request\r\n\r\nFaltan parámetros"     
    elif path == '/manualsi':
        if detectar_bomba() == True:  # Solo si bomba está encendida
            #global estado_dosificador, tiempo_inicio_dosis
            estado_dosificador = "dosificando_manual"
            tiempo_inicio_dosis = time.time()
            man.value(1)
            dosis_total = calcular_dosis_total(Refuerzo, Dosis, DosisMin)  # Usamos la función centralizada
            response = dosis_manual(dosis_total)
        else:
            #global mensaje_temporal, tiempo_mensaje
            mensaje_temporal = "❌ Error: Bomba Apagada"
            tiempo_mensaje = time.time()
            # Redirigir para evitar reenvíos del formulario
            response = web_page( )  # Mostrar la misma página con el mensaje
    elif path == '/manualno':
        man.value(0)  # Apagar el rele
        estado_dosificador = "inactivo"
        response = web_page( )
    elif path == '/refuerzosi':
        ref.value(1)  
        # Escritura en la memoria AT24C32N
        Refuerzo= 1
        eeprom.write(13, "1") # Asegurarse de que el valor esté en bytes
        response = web_page()
    elif path == '/refuerzono':
        ref.value(0)
        Refuerzo= 0
        eeprom.write(13, "0")
        response = web_page()
    elif path == '/estado_horario': #Devuelve 1 si está en horario de dosificación
        return '1' if esta_en_horario_nocturno(InNoche, FinNoche) else '0'
    elif path.startswith('/guardar_espera'):
        try:
            params = path.split('?')[1]
            param_dict = dict(pair.split('=') for pair in params.split('&'))
            
            # Obtener directamente los segundos (nuevo formato)
            total_segundos = int(param_dict.get('segundos', '30'))  # Cambio clave aquí
            total_segundos = max(0, min(total_segundos, 59)) # Rango (0-59)
            Espera = total_segundos
            eeprom.write(30, f"{Espera:02d}".encode('ascii'))
            # Obtener y validar el valor de 'minutos'
            minutos = int(param_dict.get('minutos', '1'))
            minutos = max(0, min(minutos, 30))  # Rango (0-30)
            EsperaMin = minutos
            eeprom.write(27, f"{EsperaMin:02d}".encode('ascii'))
            return pagina_config_ok()
            
        except (ValueError, KeyError) as e:
            print(f"Error en guardar_espera: {e}")
            return pagina_error("El valor debe ser entre 0-900 segundos")
        except Exception as e:
            print(f"Error inesperado: {e}")
            return pagina_error("Error al guardar la configuración")  
    else:  
        response = "HTTP/1.0 404 Not Found\r\n\r\n"
        
    return "HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\r\n{}".format(len(response), response)
    pass
#------------------------------------------------------------------
#FUNCION PARA DETERMINAR SI ESTOY EN HORARIO DE DOSIFICACION
def esta_en_horario_nocturno(InNoche, FinNoche):

    # Obtener hora actual del RTC
    ahora = reloj.get_time()
    hora_actual = ahora[3]  # Horas
    minuto_actual = ahora[4]  # Minutos

    # Convertir a minutos desde medianoche para comparación
    def tiempo_a_minutos(hhmm):
        return int(hhmm[:2]) * 60 + int(hhmm[2:])

    # Comportamiento para piletas chicas (actual lógica)
    if PiletaGrande == 0:
        inicio_noche = InNoche #eeprom.read(16, 4).decode('utf-8')  # Formato "HHMM"
        fin_noche = FinNoche #eeprom.read(20, 4).decode('utf-8')     # Formato "HHMM"

        inicio_min = tiempo_a_minutos(inicio_noche)
        fin_min = tiempo_a_minutos(fin_noche)
        actual_min = hora_actual * 60 + minuto_actual

        # Caso normal: inicio < fin (ej. 20:00 a 07:00)
        if inicio_min < fin_min:
            return inicio_min <= actual_min < fin_min
        # Caso que cruza medianoche (ej. 22:00 a 06:00)
        else:
            return actual_min >= inicio_min or actual_min < fin_min

    # Comportamiento para piletas grandes
    else:
        horarios_inicio, horarios_fin = calcular_horarios_dosis(GPInicio,GPFin,GPCantDosis)  # Llama a la función que generaste
        actual_min = hora_actual * 60 + minuto_actual

        # Verificar si el tiempo actual está dentro de algún rango de dosificación
        for inicio_noche, fin_noche in zip(horarios_inicio, horarios_fin):
            inicio_min = tiempo_a_minutos(inicio_noche)
            fin_min = tiempo_a_minutos(fin_noche)

            # Caso normal: inicio < fin
            if inicio_min < fin_min:
                if inicio_min <= actual_min < fin_min:
                    return True
            # Caso que cruza medianoche
            else:
                if actual_min >= inicio_min or actual_min < fin_min:
                    return True

        # Si no está dentro de ningún rango, retorna False
        #gc.collect()  # Liberar basura antes de conectar
        return False

#FUNCION PARA CALCULO DE HORARIOS PILETA PUBLICA
#----------------------------------------------------------
def calcular_horarios_dosis(GPInicio,GPFin,GPCantDosis):

    GPCantDosis = int(GPCantDosis)
    if GPCantDosis < 2:  # Mínimo 2 dosis
        GPCantDosis = 2
            
    # Extraer horas y minutos desde GPInicio y GPFin
    hora_inicio = int(GPInicio[:2])
    minuto_inicio = int(GPInicio[2:])
    hora_fin = int(GPFin[:2])
    minuto_fin = int(GPFin[2:])

    # Calcular el intervalo total entre GPInicio y GPFin menos 1 hora (60 minutos)
    minutos_inicio = hora_inicio * 60 + minuto_inicio
    minutos_fin = (hora_fin * 60 + minuto_fin) - 60  # Resta 1 hora al fin

    if minutos_fin < minutos_inicio:
        minutos_fin += 1440  # Ajustar si el intervalo pasa de medianoche

    intervalo_total = minutos_fin - minutos_inicio

    # Calcular el intervalo entre dosis (en minutos)
    intervalo_dosis = intervalo_total // (GPCantDosis - 1)  # Menos 1 para ajustar

    horarios_inicio = []  # Lista para los horarios de inicio
    horarios_fin = []     # Lista para los horarios de fin

    for i in range(GPCantDosis):
        # Calcular horario de inicio
        minutos_dosis_inicio = minutos_inicio + (i * intervalo_dosis)
        hora_dosis_inicio = (minutos_dosis_inicio // 60) % 24
        minuto_dosis_inicio = minutos_dosis_inicio % 60

        # Formatear como hhmm
        inicio_dosis = f"{hora_dosis_inicio:02d}{minuto_dosis_inicio:02d}"
        horarios_inicio.append(inicio_dosis)

        # Calcular horario de fin (60 minutos después del inicio)
        minutos_dosis_fin = minutos_dosis_inicio + 60
        hora_dosis_fin = (minutos_dosis_fin // 60) % 24
        minuto_dosis_fin = minutos_dosis_fin % 60

        # Formatear como hhmm
        fin_dosis = f"{hora_dosis_fin:02d}{minuto_dosis_fin:02d}"
        horarios_fin.append(fin_dosis)

    # Devolver las listas con los horarios de inicio y fin
    #gc.collect()  # Liberar basura
    return horarios_inicio, horarios_fin


# FORMATEA HORARIOS DE PILETA PUBLICA PARA MOSTRAR EN LA WEB
#----------------------------------------------------------
def formatear_horarios(inicio, fin):
    horarios = []
    for i, f in zip(inicio, fin):
        hi, mi = i[:2], i[2:]
        hf, mf = f[:2], f[2:]
        horarios.append(f"({hi}:{mi} - {hf}:{mf})")
    return "  ".join(horarios)

#------------------------------------------------------------
#FUNCION PARA DETERMINAR SI ESTOY EN VERANO O INIERNO
def esta_en_temporada_verano():

    fecha_actual = f"{reloj.get_time()[1]:02d}{reloj.get_time()[2]:02d}"
    
    # Determinar si está en el rango de invierno
    if Finvierno < Fverano:
        en_invierno = Finvierno <= fecha_actual < Fverano
    else:
        en_invierno = fecha_actual >= Finvierno or fecha_actual < Fverano
    
    # Verano es cuando NO está en invierno
    return not en_invierno

#FUNCION PARA CALCULAR LA DOSIS
#---------------------------------------------------------
def calcular_dosis_total(Refuerzo, Dosis, DosisMin):
    
    # Calcular multiplicadores
    multiplicador_verano = 2 if esta_en_temporada_verano() else 1
    multiplicador_refuerzo = 2 if Refuerzo == 1 else 1
    segundos= DosisMin * 60 + Dosis
    # Calcular dosis total
    dosis_total = segundos * multiplicador_verano * multiplicador_refuerzo
    return dosis_total
#-----------------------------------------------------------
def verificar_fin_horario_dosificacion():
    global DosisSi, DosisNo

    # Solo resetear DosisSi si estamos fuera del horario de dosificación

    if not esta_en_horario_nocturno(InNoche, FinNoche):
        if DosisSi == 1:
            if DosisNo > 0:
                DosisNo -= 1
                eeprom.write(32, str(DosisNo).encode())
            DosisSi=0
            eeprom.write(14, b'0') #DosisSi Indica si ya dosificó        
            
#FUNCION PARA RESETEAR DosisSi - Incrementa CantDosis si no dosificó
#-------------------------------------------------------------
def resetear_contador_diario():
    global CantDosis
    if not esta_en_horario_nocturno(InNoche, FinNoche):  # Si es horario diurno
        # Solo actuar si no se ha dosificado hoy
        if DosisSi == 0: #DosisSi    
            # Incrementar CantDosis
            cant_actual = CantDosis #int(eeprom.read(25, 2).decode('utf-8'))
            nueva_cant = f"{cant_actual + 1:02d}"[:2]  # Asegurar 2 dígitos
            
            # Activar dosificador por 3 segundos solo si aumentó CantDosis
            if nueva_cant != f"{cant_actual:02d}":
                eeprom.write(25, nueva_cant.encode()) #Indica la cantidad de dosis que no se pusieron
                CantDosis= nueva_cant
                man.value(1)  # Activar dosificador
                time.sleep(3)  # Esperar 3 segundos
                man.value(0)  # Desactivar dosificador
            
#FUNCION PARA EMPEZAR LA DOSIFICACION
#-----------------------------------------------------------
def verificar_dosificacion():
    global estado_dosificador, tiempo_inicio_espera, tiempo_inicio_dosis, ultima_verificacion
    global DosisNo, DosisSi, ultima_dosis_saltada, Refuerzo, Espera, EsperaMin
    global CantDosis, Dosis, DosisAnulada, InNoche, FinNoche, DosisMin
    global man, ref
    ahora = time.time()
     
    # Solo verificamos cada 1 segundo para no sobrecargar el sistema
    if ahora - ultima_verificacion < 1.0:
        return
    ultima_verificacion = ahora
    
    # Leer tiempo de espera configurado
    tiempo_espera = EsperaMin * 60 + Espera #int.from_bytes(espera_bytes, 'big')
        
    # Leer estado DosisSi (0=pendiente, 1=ya dosificó)
    dosis_si = DosisSi #int(eeprom.read(14, 1))
    
    # Manejo exclusivo para dosis manual
    if estado_dosificador == "dosificando_manual":
        dosis_total = calcular_dosis_total(Refuerzo, Dosis, DosisMin)
        
        if ahora - tiempo_inicio_dosis >= dosis_total:
            man.value(0)
            estado_dosificador = "inactivo"
            escribir_ultdosis(40,0) #Guarda la fecha de la última dosis
            # RESET DE CANTDOSIS PERO NO PONER DOSISSI=1
            CantDosis = 0
            eeprom.write(25, b'00')  # Resetear CantDosis
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo=0
                ref.value(0)
        elif not detectar_bomba():
            man.value(0)
            estado_dosificador = "inactivo"
        return
    
    # Lógica para dosificación automática
    if dosis_si == 1:
        return  # No hacer nada si ya se dosificó

    
    # Primero verificar si debemos saltar dosis
    if DosisNo > 0 and esta_en_horario_nocturno(InNoche, FinNoche) and detectar_bomba():
        #if (ahora - ultima_dosis_saltada) >= 90000:  # 25 horas
        eeprom.write(39,'1') #DosisAnulada por usuario
        DosisAnulada = "1"
        eeprom.write(14, b'1') #Pone DosisSi en 1 para que no vuelva a pasar por acá
        DosisSi = 1
        ultima_dosis_saltada = ahora
        return
    else:
        if DosisAnulada == '1':
            eeprom.write(39,'0')
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
        if ahora - tiempo_inicio_dosis >= calcular_dosis_total(Refuerzo, Dosis,DosisMin):
            man.value(0)
            estado_dosificador = "inactivo"
            escribir_ultdosis(40,0) #Guarda la fecha de la última dosis
            # SOLO PARA DOSIS AUTOMÁTICA: Poner DosisSi=1
            eeprom.write(14, b'1')  # Marcar como dosificado
            DosisSi = 1
            eeprom.write(25, b'00')  # Resetear CantDosis
            CantDosis = 0
            if Refuerzo == 1:
                eeprom.write(13, b'0')
                Refuerzo = 0
                ref.value(0)
        elif not detectar_bomba():
            man.value(0)
            estado_dosificador = "inactivo"
    #gc.collect()  # Liberar basura antes de conectar        

#------------------------------------------------------------------
def send_all(sock, data, chunk_size=512):
    encoded = data.encode()
    total = len(encoded)
    total_sent = 0
    while total_sent < total:
        try:
            sent = sock.send(encoded[total_sent:total_sent+chunk_size])
            if sent is None or sent == 0:
                # Si no se pudo enviar nada, espera un poco para reintentar
                time.sleep(0.01)
                continue
            total_sent += sent
        except OSError as e:
            if e.errno == 11:  # EAGAIN
                # Esperar un poco y reintentar
                time.sleep(0.01)
            else:
                raise

#INDICACIONES CON LED
#-----------------------------------------------------------
def actualizar_led():
    global estado_led_actual, Refuerzo, estado_dosificador, InNoche, FinNoche
    
    # 1. Determinar el patrón según el estado del sistema
    en_horario = esta_en_horario_nocturno(InNoche, FinNoche)
    
    # Selección del patrón base
    if estado_dosificador in ["dosificando", "dosificando_manual"]:
        base_patron = 'dosificando'
    elif en_horario:
        base_patron = 'horario'
    else:
        base_patron = 'inactivo'
    
    # Aplicar sufijo _refuerzo si corresponde
    patron_seleccionado = f"{base_patron}_refuerzo" if Refuerzo else base_patron
    
    # 2. Cambiar patrón solo si es diferente al actual
    if estado_led_actual['patron'] != LED_PATRONES[patron_seleccionado]:
        estado_led_actual = {
            'patron': LED_PATRONES[patron_seleccionado],
            'indice': 0,
            'ultimo_cambio': time.time(),
            'estado': 0
        }
        ref.value(0)  # Apagar al cambiar patrón
    
    # 3. Control del estado actual del LED
    ahora = time.time()
    paso_actual = estado_led_actual['patron'][estado_led_actual['indice']]
    tiempo_transcurrido = ahora - estado_led_actual['ultimo_cambio']
    
    if tiempo_transcurrido >= paso_actual[1]:
        # Cambiar al siguiente paso del patrón
        nuevo_estado = 1 if paso_actual[0] == 'ON' else 0
        ref.value(nuevo_estado)
        
        # Actualizar estado del LED
        estado_led_actual['indice'] = (estado_led_actual['indice'] + 1) % len(estado_led_actual['patron'])
        estado_led_actual['ultimo_cambio'] = ahora
        estado_led_actual['estado'] = nuevo_estado

#-------------------------------------------------------------------------------------------
#AGREGADA PARA EL NUEVO MANEJO DEL SERV. NO BLOQUEANTE
def receive_with_timeout(sock, timeout=2.0):
    data = b''
    start_time = time.time()
    
    while (time.time() - start_time) < timeout:
        try:
            chunk = sock.recv(256)
            if chunk:
                data += chunk
                if b'\r\n\r\n' in data:  # Fin de headers
                    break
            else:
                break  # Conexión cerrada
        except OSError as e:
            if e.errno != 11:  # Ignorar EAGAIN/EWOULDBLOCK
                raise
        time.sleep(0.01)
        
    return data if data else None

#---------------------------------------------------------------------------------------------
#PARA MEJOR MANEJO DEL SERVIDOR NO BLOQUEANTE
def handle_client_connection(sock, read_list):
    gc.collect()
    try:
        # 1. Recibir datos con timeout
        data = receive_with_timeout(sock, timeout=2.0)
        if not data:
            raise OSError(104, "Cliente cerró conexión")

        path = data.decode().split('\r\n')[0].split()[1]
        
        # 2. Generar respuesta
        response = handle_request(path)  # Tu función original
        
        # 3. Enviar por partes
        send_all(sock, response)  # Usamos tu función original
        
    except OSError as e:
        if e.errno == 5:  # EIO
            print(f"Error E/S: {e}")
        elif e.errno == 104:  # ECONNRESET
            print("Cliente cerró conexión abruptamente")
        elif e.errno == 11:  # EAGAIN
            print("Socket no listo (timeout)")
        else:
            print(f"Error de socket: {e}")
    except Exception as e:
        print(f"Error manejando cliente: {e}")
    finally:
        if sock in read_list:
            read_list.remove(sock)
        sock.close()
        gc.collect()        

#MANEJO DEL SERVIDOR WEB NO BLOQUEANTE - MAIN
#----------------------------------------------------------
def run_server():
    global mensaje_temporal, tiempo_mensaje, duracion_mensaje, Refuerzo, estado_dosificador, PiletaGrande, Espera, EsperaMin, Dosis, DosisMin, Bomba
    gc.collect()
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(2)
    s.setblocking(False)

    read_list = [s]
    ultima_verificacion_diaria = 0
    print("Servidor HTTP iniciado:", version)

    while True:
        if detectar_bomba():
            if Bomba ==0:
                escribir_ultdosis(60,1 )
                Bomba=1
        elif not detectar_bomba():
             if Bomba ==1:
                escribir_ultdosis(75,2 )
                Bomba=0
            
        try:
            # 1. Tareas del sistema (sin bloqueo)
            wdt.feed()
            verificar_fin_horario_dosificacion()
            actualizar_led()
            
            if mensaje_temporal and (time.time() - tiempo_mensaje > duracion_mensaje):
                mensaje_temporal = ""
                
            ahora = time.time()
            if ahora - ultima_verificacion_diaria >= 90000:
                resetear_contador_diario()
                ultima_verificacion_diaria = ahora
                
            verificar_dosificacion()

            # 2. Manejo de conexiones mejorado
            readable, _, _ = select.select(read_list, [], [], 0.5)  # Timeout reducido
            
            for sock in readable:
                if sock is s:
                    try:
                        conn, addr = s.accept()
                        conn.setblocking(False)
                        read_list.append(conn)
                        print(f"Conexión aceptada desde {addr}")
                    except Exception as e:
                        print(f"Error aceptando conexión: {e}")
                else:
                    handle_client_connection(sock, read_list)  # Nueva función
                    
        except Exception as e:
            print(f"Error en run_server: {e}")
        finally:
            time.sleep(0.1)  # Pausa crítica   

#INICIALIZACION Y EJECUCION
#-----------------------------------------------------------
run_server()