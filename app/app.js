import { initializeApp } from "https://www.gstatic.com/firebasejs/10.8.1/firebase-app.js";
import { getAuth, signInWithEmailAndPassword, signOut, onAuthStateChanged, GoogleAuthProvider, signInWithPopup } from "https://www.gstatic.com/firebasejs/10.8.1/firebase-auth.js";
import { getFirestore, doc, getDoc, setDoc, updateDoc, onSnapshot, collection, getDocs, deleteDoc, query, where, orderBy, limit } from "https://www.gstatic.com/firebasejs/10.8.1/firebase-firestore.js";

// === FIREBASE INIT ===
const firebaseConfig = {
  apiKey: "AIzaSyCkkrfiHOcMG1_djAxg1G3ZzrD7F8SwcOY",
  authDomain: "dosimat-iot.firebaseapp.com",
  projectId: "dosimat-iot",
  storageBucket: "dosimat-iot.firebasestorage.app",
  messagingSenderId: "547969144575",
  appId: "1:547969144575:web:d7934b008655932cf29eca"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

document.getElementById('btnGuardarWifi').onclick = async () => {
    const ssid = document.getElementById('inpWifiSsid').value.trim();
    const pwd = document.getElementById('inpWifiPwd').value;
    if (!ssid) {
        customAlert("Debes ingresar el nombre de la red.");
        return;
    }
    
    if (!pwd) {
        if (!await customConfirm("No ingresaste ninguna contraseña. La mayoría de las redes Wi-Fi requieren una. ¿Estás seguro de que tu red es abierta y deseas continuar?", "Red sin contraseña", "Sí, continuar", "Cancelar")) {
            return;
        }
    }
    
    const isConnected = modoConexion !== "OFFLINE";
    const title = isConnected ? "Cambiar Wi-Fi" : "Vincular a Wi-Fi";
    const btnText = isConnected ? "Sí, cambiar" : "Sí, vincular";
    const warnText = isConnected ? `Si los datos son incorrectos, perderás la conexión remota.` : `El equipo intentará conectarse a internet.`;
    
    if (await customConfirm(`¿Estás seguro de enviar estas credenciales? El equipo se reiniciará para conectarse a "<b>${ssid}</b>". ${warnText}`, title, btnText, "Cancelar")) {
        sendCommand({comando: "config_wifi", ssid: ssid, pass: pwd});
        document.getElementById('inpWifiSsid').value = "";
        document.getElementById('inpWifiPwd').value = "";
    }
};

// Toggle Password Visibility
const btnToggleWifiPwd = document.getElementById('btnToggleWifiPwd');
if (btnToggleWifiPwd) {
    btnToggleWifiPwd.onclick = () => {
        const inp = document.getElementById('inpWifiPwd');
        if (inp.type === "password") {
            inp.type = "text";
            btnToggleWifiPwd.innerText = "🙈";
        } else {
            inp.type = "password";
            btnToggleWifiPwd.innerText = "👁️";
        }
    };
}

// ==========================================
// PWA INSTALL LOGIC
// ==========================================
let deferredPrompt;
const installBanner = document.getElementById('installBanner');
const btnInstall = document.getElementById('btnInstall');
const btnInstallDismiss = document.getElementById('btnInstallDismiss');

window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (!localStorage.getItem('pwa_dismissed')) {
        installBanner.style.display = 'flex';
    }
});

btnInstall.addEventListener('click', async () => {
    installBanner.style.display = 'none';
    if (deferredPrompt) {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        console.log(`User response to the install prompt: ${outcome}`);
        deferredPrompt = null;
    }
});

btnInstallDismiss.addEventListener('click', () => {
    installBanner.style.display = 'none';
    localStorage.setItem('pwa_dismissed', 'true');
});

window.addEventListener('appinstalled', () => {
    installBanner.style.display = 'none';
    deferredPrompt = null;
    console.log('PWA was installed');
});

// === USER / ADMIN STATE ===
let currentUser = null;
let currentMac = null; // MAC del equipo actual
let isAdmin = false;
let isSuperAdmin = false;
let superAdminEmail = "gab.aldazabal@gmail.com";
let firstSync = true;
let soporteConfig = { whatsapp: "+5491138571681", email: "soporte@dosimat.com.ar", web: "https://www.dosimat.com.ar" };
let modoConexion = "OFFLINE"; // "OFFLINE", "NUBE", "BLE"
let unsubSnapshot = null;
let unsubHistorial = null;
let unsubSysLog = null;

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('service-worker.js');
}

// === UI ELEMENTS ===
const authOverlay = document.getElementById('authOverlay');
const txtEmail = document.getElementById('txtEmail');
const txtPassword = document.getElementById('txtPassword');
const btnLogin = document.getElementById('btnLogin');
const btnGoogleLogin = document.getElementById('btnGoogleLogin');
const lblAuthError = document.getElementById('lblAuthError');
const btnLogout = document.getElementById('btnLogout');
const lblConnMode = document.getElementById('lblConnMode');

// Permitir abrir el panel BLE manualmente tocando el estado
lblConnMode.addEventListener('click', () => {
    document.getElementById('connectOverlay').style.display = 'flex';
    document.getElementById('connectStatus').innerText = "Busca tu equipo cercano...";
});
lblConnMode.style.cursor = 'pointer';

const tabs = {
    'nav-panel': document.getElementById('tab-panel'),
    'nav-prog': document.getElementById('tab-prog'),
    'nav-hist': document.getElementById('tab-hist'),
    'nav-avanzado': document.getElementById('tab-avanzado'),
    'nav-admin': document.getElementById('tab-admin'),
    'nav-soporte': document.getElementById('tab-soporte')
};
const navButtons = document.querySelectorAll('nav button');

// Timer Local y Unsaved Changes
let localTimer = null;
let currentDosisSec = 0;
let currentEstado = "";
let currentTipo = "";
let currentTEspera = 0;
let currentTDosis = 0;
let isDosisActive = false;

let unsavedChanges = false;
function setUnsaved() { unsavedChanges = true; }
function clearUnsaved() {
    unsavedChanges = false;
    document.querySelectorAll('.pulse').forEach(el => el.classList.remove('pulse'));
}

// Sun Mode
let sunMode = false;


// Tabs
navButtons.forEach(btn => {
    btn.addEventListener('click', async (e) => {
        if (unsavedChanges && e.target.id !== 'nav-prog') {
            if (!await customConfirm("Tienes cambios sin guardar. Pulsa Volver para seguir editando o Descartar para continuar sin guardar.", "Cambios sin guardar", "Descartar", "Volver")) {
                const affectedBtns = document.querySelectorAll('#tab-prog .btn');
                affectedBtns.forEach(b => {
                    if(b.id.startsWith('btnGuardar')) b.classList.add('pulse');
                });
                // Quitar pulse tras 4s
                setTimeout(() => {
                    affectedBtns.forEach(b => b.classList.remove('pulse'));
                }, 4000);
                return;
            }
            clearUnsaved();
        }
        navButtons.forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        Object.values(tabs).forEach(t => t.classList.remove('active'));
        tabs[e.target.id].classList.add('active');
    });
});

function switchTab(tabId) {
    document.getElementById(`nav-${tabId}`).click();
}

// Swipe Logic para cambiar de pestañas
let touchStartX = 0;
let touchEndX = 0;
document.body.addEventListener('touchstart', e => {
    touchStartX = e.changedTouches[0].screenX;
}, {passive: true});

document.body.addEventListener('touchend', e => {
    touchEndX = e.changedTouches[0].screenX;
    if (touchEndX < touchStartX - 50) navigateTab(1); // Swipe left
    if (touchEndX > touchStartX + 50) navigateTab(-1); // Swipe right
}, {passive: true});

function navigateTab(direction) {
    const activeBtn = document.querySelector('nav button.active');
    if(!activeBtn) return;
    const btns = Array.from(document.querySelectorAll('nav button')).filter(b => b.style.display !== 'none');
    let idx = btns.indexOf(activeBtn);
    idx += direction;
    if(idx >= 0 && idx < btns.length) {
        btns[idx].click();
    }
}

// Help Icons Logic
document.getElementById('helpCronograma').addEventListener('click', () => {
    customAlert("Cronograma de Filtrado/Dosificación:\nAgrega aquí los horarios de filtrado de tu pileta. Puedes añadir hasta 10 programas diferentes.\n\n<b>Horario inicio:</b> horario de comienzo del filtrado\n<b>T.Filtrado:</b> duración del filtrado (en minutos)\n<b>Dosificar:</b> si está tildado, pondrá una dosis durante ese filtrado (La dosis será la especificada en Configuración de Dosis)\n<b>Programa Automático:</b> Elimina todos los horarios programados y genera una programación estándar para una pileta hogareña, incluyendo la dosificación.\n\nImportante: en piletas hogareñas es recomendable que la dosificación se haga durante un horario nocturno. \nLa bomba debe continuar funcionando al menos 1/2 hora luego de la dosificación para distribuir bien el cloro.", "Ayuda: Cronograma");
});

document.getElementById('helpConfig').addEventListener('click', () => {
    customAlert("Desde Configuración de Dosis podés ajustar la dosis de cloro de tu equipo.\n\n<b>Tiempo de Dosis:</b> tiempo durante el cual estará ingresando cloro a la pileta. Depende de las condiciones de la bomba y del sistema de filtrado. (Ver ¿Cómo calibrar la Dosis? en la sección Sistema)\n<b>Espera:</b> intervalo de tiempo que el equipo aguarda antes de comenzar a dosificar cloro.\nEste tiempo es útil cuando el sistema de circulación de agua tarda en estabilizarse, lo que suele ocurrir si entra aire en las cañerías. \n<b>Temporada de Verano:</b> especifica las fechas en las que aumenta la cantidad de cloro a colocar.\nDurante las fechas de verano se coloca el doble de cloro.", "Ayuda: Configuración");
});

document.getElementById('helpAnular').addEventListener('click', () => {
    customAlert("<b>Anular Dosis:</b> permite saltar las proximas dosis. Puede anular hasta 5 dosis. Luego el sistema retorna automaticamente a su estado normal.\nA veces es necesario anular la dosis si se va a utilizar la pileta en ese horario o se le va a realizar mantenimiento.\nNo se recomienda anular más de 2 dosis seguidas. Podría deteriorase el estado del agua.", "Ayuda: Anular Dosis");
});

document.getElementById('helpManual').addEventListener('click', () => {
    customAlert("<b>Bomba:</b> permite encender la bomba de filtrado. Esta quedará encendida hasta que se la detenga manualmente.\n<b>Dosis Manual:</b> agrega una dosis de cloro en el momento. Si la bomba está apagada, la encenderá y la apagará 30 minutos después de terminada la dosificación.\n<b>Refuerzo:</b> permite duplicar la dosis (ya sea automática o manual) por única vez. Luego de colocada la dosis, el Refuerzo se apaga.", "Ayuda: Control Manual");
});

document.getElementById('helpHistorial').addEventListener('click', () => {
    customAlert("El Historial guarda los datos de las últimas dosis. Si la dosis no se pudo realizar porque el equipo estaba desconectado, resalta el registro en rojo. También indica si el equipo se ha reiniciado después de un corte de tensión. No es necesario limpiar el historial. Se va sobreescribiendo con los últimos datos.", "Ayuda: Historial");
});

document.getElementById('helpSistemaWifi').addEventListener('click', () => {
    customAlert("Permite vincular el equipo a una red Wi-Fi.\n\nSi el equipo está OFFLINE o conectado por Bluetooth, enviará las credenciales para que se conecte a internet.\n\nSi ya está conectado por Wi-Fi (NUBE), podrás indicarle que cambie a otra red distinta. En ambos casos, el equipo se reiniciará para aplicar los cambios.", "Ayuda: Red Wi-Fi");
});

document.getElementById('helpSistemaGestion').addEventListener('click', () => {
    customAlert("Las configuraciones de fábrica restablecen todas las variables y borran el historial de dosificaciones y configuración Wi-Fi.\n\nTambién puedes borrar únicamente el historial, o sincronizar la hora interna de la placa (RTC) usando la hora actual de este dispositivo.", "Ayuda: Gestión de Sistema");
});

document.getElementById('helpTelemetria')?.addEventListener('click', () => {
    customAlert("El LED del equipo indica su estado actual:\n\n• Parpadeo corto c/ 5 seg: Inactivo.\n• Parpadeo lento y constante: Dosificando.\n• Parpadeo constante y veloz: Solo Bomba encendida.\n• Luz fija con cortes breves: Dosificando con refuerzo.\n\nNOTA: Si el Refuerzo está activo, se agregan parpadeos rápidos extra a los estados normales.", "Ayuda: LED y Telemetría");
});

const timeToStr = (hhmm) => {
    if(!hhmm || hhmm.length !== 4) return "";
    return `${hhmm.substring(0,2)}:${hhmm.substring(2,4)}`;
};
const dateToStr = (mmdd) => {
    if(!mmdd || mmdd.length !== 4) return "";
    const year = new Date().getFullYear();
    return `${year}-${mmdd.substring(0,2)}-${mmdd.substring(2,4)}`;
};

// === AUTHENTICATION LOGIC ===
btnLogin.addEventListener('click', async () => {
    try {
        lblAuthError.innerText = "Iniciando sesión...";
        await signInWithEmailAndPassword(auth, txtEmail.value, txtPassword.value);
    } catch (error) {
        lblAuthError.innerText = "Error: " + error.message;
    }
});

btnGoogleLogin.addEventListener('click', async () => {
    try {
        lblAuthError.innerText = "Abriendo ventana de Google...";
        const provider = new GoogleAuthProvider();
        await signInWithPopup(auth, provider);
    } catch (error) {
        lblAuthError.innerText = "Error: " + error.message;
    }
});

btnLogout.addEventListener('click', async () => {
    if(await customConfirm("¿Estás seguro que deseas salir de tu cuenta?", "Cerrar Sesión")) {
        await signOut(auth);
    }
});

onAuthStateChanged(auth, async (user) => {
    if (user) {
        currentUser = user;
        authOverlay.style.display = 'none';
        lblAuthError.innerText = "";
        
        const userName = user.displayName || user.email.split('@')[0];
        document.getElementById('lblUserName').innerText = userName;
        
        await loadUserProfile(user.uid);
    } else {
        currentUser = null;
        currentMac = null;
        isAdmin = false;
        isSuperAdmin = false;
        authOverlay.style.display = 'flex';
        setConexionModo("OFFLINE");
        if(unsubSnapshot) unsubSnapshot();
    }
});

async function loadUserProfile(uid) {
    try {
        const email = currentUser.email.toLowerCase();
        isSuperAdmin = (email === superAdminEmail.toLowerCase());
        
        // Verificar si es administrador
        const adminRef = doc(db, "administradores", email);
        const adminSnap = await getDoc(adminRef);
        isAdmin = adminSnap.exists() || isSuperAdmin;
        
        if (isSuperAdmin && !adminSnap.exists()) {
            await setDoc(adminRef, { rol: "super_admin" });
        }
        
        // Cargar config global de soporte
        try {
            const configSnap = await getDoc(doc(db, "configuracion", "soporte"));
            if (configSnap.exists()) {
                soporteConfig = configSnap.data();
                document.getElementById('inpAdminWhatsApp').value = soporteConfig.whatsapp || "";
                document.getElementById('inpAdminEmail').value = soporteConfig.email || "";
                document.getElementById('inpAdminWeb').value = soporteConfig.web || "";
            }
        } catch (e) {
            console.error("Error loading config", e);
        }

        if (isAdmin) {
            document.getElementById('nav-admin').style.display = 'block';
            if (isSuperAdmin) {
                document.getElementById('cardSuperAdmin').style.display = 'block';
                loadAdminEmails();
            }
            loadAdminGlobal();
        } else {
            document.getElementById('nav-admin').style.display = 'none';
        }

        const userRef = doc(db, "usuarios", uid);
        const userSnap = await getDoc(userRef);
        
        if (userSnap.exists()) {
            const data = userSnap.data();
            
            if (currentUser.displayName) {
                await setDoc(userRef, { nombre: currentUser.displayName }, { merge: true });
            }

            if (isAdmin) {
                document.getElementById('connectOverlay').style.display = 'none';
                document.getElementById('headerTechMode').style.display = 'block';
                document.getElementById('headerTechMode').style.background = 'var(--accent)';
                document.getElementById('headerTechMode').innerHTML = `⚠ PORTAL TÉCNICO: Debe seleccionar un equipo`;
                switchTab('admin');
            } else if (data.id_equipo) {
                currentMac = data.id_equipo;
                document.getElementById('lblMac').innerText = currentMac;
                connectNube();
            } else {
                document.getElementById('connectOverlay').style.display = 'flex';
                document.getElementById('connectStatus').innerText = "No tienes equipo asignado. Conéctate por BLE la primera vez.";
            }
        } else {
            // Usuario nuevo sin documento
            await setDoc(userRef, { email: currentUser.email, id_equipo: "" });
            if (isAdmin) {
                document.getElementById('connectOverlay').style.display = 'none';
                switchTab('admin');
            } else {
                document.getElementById('connectOverlay').style.display = 'flex';
                document.getElementById('connectStatus').innerText = "Bienvenido. Conéctate por BLE para asociar tu equipo.";
            }
        }
    } catch (e) {
        console.error("Error loading profile", e);
    }
}

// === HYBRID NETWORK LOGIC ===
function setConexionModo(modo) {
    modoConexion = modo;
    lblConnMode.className = "conn-badge";
    const btnWifi = document.getElementById('btnGuardarWifi');
    if (modo === "NUBE") {
        lblConnMode.innerText = "NUBE (WiFi)";
        lblConnMode.classList.add("conn-nube");
        if(btnWifi) btnWifi.innerText = "Cambiar de Red Wi-Fi";
    } else if (modo === "BLE") {
        lblConnMode.innerText = "LOCAL (BLE)";
        lblConnMode.classList.add("conn-ble");
        if(btnWifi) btnWifi.innerText = "Vincular a Wi-Fi";
    } else {
        lblConnMode.innerText = "OFFLINE";
        lblConnMode.classList.add("conn-offline");
        if(btnWifi) btnWifi.innerText = "Vincular a Wi-Fi";
    }
    
    const btnSyncRtcBLE = document.getElementById('btnSyncRtcBLE');
    if (btnSyncRtcBLE) {
        btnSyncRtcBLE.style.display = (modo === "BLE") ? "block" : "none";
    }
}

function connectNube() {
    if (!currentMac) return;
    if(unsubSnapshot) unsubSnapshot();
    if(unsubSysLog) unsubSysLog();
    
    unsubSnapshot = onSnapshot(doc(db, "equipos", currentMac), (docSnap) => {
        if (docSnap.exists()) {
            const data = docSnap.data();
            window.lastDocData = data;
            if (modoConexion !== "BLE") { // No sobrescribir si estamos en BLE en vivo
                let isOffline = false;
                if (data.rtc_fecha && data.rtc_hora) {
                    const parts = data.rtc_fecha.split("-");
                    const timeParts = data.rtc_hora.split(":");
                    const rtcDate = new Date(parts[0], parts[1]-1, parts[2], timeParts[0], timeParts[1], timeParts[2]);
                    const diffMins = (new Date() - rtcDate) / 60000;
                    if (diffMins > 20) isOffline = true; // 20 minutos sin reportar = OFFLINE
                }
                
                // Si procesó el comando (no hay comando pendiente), está vivo AHORA MISMO
                if (window.cmdTimeout && !data.comando_pendiente) {
                    clearTimeout(window.cmdTimeout);
                    window.cmdTimeout = null;
                    isOffline = false; // Anula la detección por hora si acaba de responder
                }

                setConexionModo(isOffline ? "OFFLINE" : "NUBE");
                updateUI(data);
            }
        } else {
            console.log("El equipo no está reportando datos en la nube");
        }
    }, (error) => {
        console.error("Error en Firestore:", error);
        if (modoConexion !== "BLE") setConexionModo("OFFLINE");
    });

    unsubSysLog = onSnapshot(query(collection(db, `equipos/${currentMac}/sys_log`), orderBy("fecha", "desc"), limit(100)), (snapshot) => {
        const logs = [];
        snapshot.forEach(doc => {
            const data = doc.data();
            logs.push(`${data.fecha || ""} - ${data.evento || ""}`);
        });
        renderSysLog(logs.reverse()); // Reverse to show oldest first in the terminal, or keep as is.
    });
}

// === BLE LOGIC ===
const SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"; 
const RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"; 

let bleDevice = null;
let bleServer = null;
let dosimatService = null;
let txCharacteristic = null;
let rxCharacteristic = null;
let isConnecting = false;
let rxBuffer = "";

// Variables para el LED replicado en UI
let globalEstadoDosificador = "inactivo";
let globalRefuerzo = 0;

const LED_PATRONES = {
    'inactivo':             [[1, 200], [0, 5000]],
    'inactivo_refuerzo':    [[1, 200], [0, 200], [1, 200], [0, 5000]],
    'dosificando':          [[1, 1000], [0, 1000]],
    'dosificando_refuerzo': [[1, 5000], [0, 200]],
    'solo_bomba':           [[1, 500], [0, 500]],
    'solo_bomba_refuerzo':  [[1, 200], [0, 200], [1, 200], [0, 500]],
    'esperando_manual':     [[1, 1000], [0, 1000]]
};

let estado_led_actual = {
    patron: LED_PATRONES['inactivo'],
    indice: 0,
    ultimo_cambio: Date.now()
};

let pwaEstadoAnterior = "";
let pwaInicioEstado = Date.now();

setInterval(() => {
    let base_patron = 'inactivo';
    if (globalEstadoDosificador === "dosificando" || globalEstadoDosificador === "manual") {
        base_patron = 'dosificando';
    } else if (globalEstadoDosificador === "esperando_dosis" || globalEstadoDosificador === "esperando_manual") {
        base_patron = 'esperando_dosis';
    } else if (globalEstadoDosificador === "solo_bomba") {
        base_patron = 'solo_bomba';
    }
    
    let patron_sel = (globalRefuerzo == 1) ? `${base_patron}_refuerzo` : base_patron;
    let patron_esperado = LED_PATRONES[patron_sel] || LED_PATRONES['inactivo'];
    
    if (estado_led_actual.patron !== patron_esperado) {
        estado_led_actual.patron = patron_esperado;
        estado_led_actual.indice = 0;
        estado_led_actual.ultimo_cambio = Date.now();
        setLedUi(patron_esperado[0][0]);
    }
    
    let ahora = Date.now();
    let paso_actual = estado_led_actual.patron[estado_led_actual.indice];
    
    if (ahora - estado_led_actual.ultimo_cambio >= paso_actual[1]) {
        estado_led_actual.indice = (estado_led_actual.indice + 1) % estado_led_actual.patron.length;
        estado_led_actual.ultimo_cambio = ahora;
        let siguiente_paso = estado_led_actual.patron[estado_led_actual.indice];
        setLedUi(siguiente_paso[0]);
    }
}, 50);

function setLedUi(estado) {
    const led = document.getElementById('panelLed');
    if (led) {
        if (estado === 1) {
            led.classList.remove('off');
            led.classList.add('on');
        } else {
            led.classList.remove('on');
            led.classList.add('off');
        }
    }
} 

const connectBtn = document.getElementById('btnConnect');
const connectStatus = document.getElementById('connectStatus');
const connectOverlay = document.getElementById('connectOverlay');
const btnCancelBLE = document.getElementById('btnCancelBLE');
const btnForceBLE = document.getElementById('btnForceBLE');
const btnSyncRtcBLE = document.getElementById('btnSyncRtcBLE');

if (btnSyncRtcBLE) {
    btnSyncRtcBLE.addEventListener('click', () => {
        syncRTC();
        customAlert("Se envió el comando para sincronizar la hora del equipo a través de Bluetooth.", "Sincronizando");
    });
}

btnForceBLE.addEventListener('click', () => { 
    if (modoConexion === "NUBE") {
        customAlert("Actualmente estás conectado por Wi-Fi (Nube). El Bluetooth del equipo solo se habilita automáticamente cuando no hay conexión Wi-Fi disponible.", "Conexión Activa");
        return;
    }
    if (!navigator.bluetooth) {
        customAlert("Tu navegador o dispositivo no soporta Bluetooth Web. En iOS/iPhone debes usar un navegador especial como 'WebBLE' o 'Bluefy'.\n\nEste botón sirve para conectarse directamente al equipo por Bluetooth en caso de que esté sin internet.", "Bluetooth no disponible");
        return;
    }
    connectOverlay.style.display = 'flex'; 
});
btnCancelBLE.addEventListener('click', () => { connectOverlay.style.display = 'none'; });

connectBtn.addEventListener('click', async () => {
    try {
        connectStatus.innerText = "Buscando dispositivos...";
        bleDevice = await navigator.bluetooth.requestDevice({
            filters: [{ name: "DosimatBLE" }],
            optionalServices: [SERVICE_UUID]
        });

        bleDevice.addEventListener('gattserverdisconnected', onDisconnected);
        connectStatus.innerText = "Conectando al servidor GATT...";
        bleServer = await bleDevice.gatt.connect();

        connectStatus.innerText = "Obteniendo Servicio UART...";
        const service = await bleServer.getPrimaryService(SERVICE_UUID);

        connectStatus.innerText = "Obteniendo Características...";
        rxCharacteristic = await service.getCharacteristic(RX_UUID);
        txCharacteristic = await service.getCharacteristic(TX_UUID);

        await txCharacteristic.startNotifications();
        txCharacteristic.addEventListener('characteristicvaluechanged', handleNotifications);

        connectStatus.innerText = "Conectado!";
        setConexionModo("BLE");
        setTimeout(() => {
            connectOverlay.style.display = 'none';
        }, 500);

        syncRTC();
    } catch (error) {
        connectStatus.innerText = "Error: " + error.message;
        console.error(error);
    }
});

function onDisconnected() {
    console.log("BLE Disconnected. Intentando reconectar...");
    rxCharacteristic = null;
    txCharacteristic = null;
    
    if (bleDevice && currentMac) {
        setConexionModo("OFFLINE");
        document.getElementById('lblConnMode').innerText = "Reconectando BLE...";
        
        setTimeout(async () => {
            try {
                if (bleDevice.gatt.connected) return;
                bleServer = await bleDevice.gatt.connect();
                const service = await bleServer.getPrimaryService(SERVICE_UUID);
                rxCharacteristic = await service.getCharacteristic(RX_UUID);
                txCharacteristic = await service.getCharacteristic(TX_UUID);
                await txCharacteristic.startNotifications();
                txCharacteristic.addEventListener('characteristicvaluechanged', handleNotifications);
                setConexionModo("BLE");
                showToast("Bluetooth reconectado automáticamente");
            } catch (e) {
                console.error("Fallo auto-reconexión BLE:", e);
                setConexionModo("NUBE"); // Fallback final
            }
        }, 2000);
    } else {
        if (currentMac) {
            setConexionModo("NUBE");
        } else {
            setConexionModo("OFFLINE");
            connectOverlay.style.display = 'flex';
            connectStatus.innerText = "Dispositivo desconectado.";
        }
    }
}

async function handleNotifications(event) {
    const value = event.target.value;
    const decoder = new TextDecoder('utf-8');
    const chunk = decoder.decode(value);
    
    rxBuffer += chunk;
    try {
        const data = JSON.parse(rxBuffer);
        rxBuffer = ""; 
        
        // Multi-user pairing: If no mac assigned to user, get it from telemetry
        if (!currentMac && data.id_equipo && currentUser) {
            currentMac = data.id_equipo;
            document.getElementById('lblMac').innerText = currentMac;
            await setDoc(doc(db, "usuarios", currentUser.uid), { id_equipo: currentMac }, { merge: true });
            connectNube();
        }
        
        updateUI(data);
    } catch (e) {
        if (!(e instanceof SyntaxError)) {
            console.error("Error parseando telemetría:", e);
            rxBuffer = ""; 
        }
    }
}

// === COMMAND ROUTER (Hybrid) ===
async function sendCommand(obj) {
    showToast("Enviando orden al dispositivo...", true);
    if (modoConexion === "BLE" && rxCharacteristic) {
        const jsonStr = JSON.stringify(obj) + "\n";
        const encoder = new TextEncoder();
        const data = encoder.encode(jsonStr);
        try {
            for (let i = 0; i < data.length; i += 20) {
                const chunk = data.slice(i, i + 20);
                if (rxCharacteristic.writeValueWithoutResponse) {
                    await rxCharacteristic.writeValueWithoutResponse(chunk);
                } else {
                    await rxCharacteristic.writeValue(chunk);
                }
                await new Promise(r => setTimeout(r, 20));
            }
        } catch (error) {
            console.error("Error enviando comando BLE:", error);
        }
    } else if ((modoConexion === "NUBE" || modoConexion === "OFFLINE") && currentMac) {
        try {
            // Write command to Firestore as stringified JSON so ESP32 REST API can read it easily
            await setDoc(doc(db, "equipos", currentMac), { comando_pendiente: JSON.stringify(obj) }, { merge: true });
            console.log("Comando enviado a la Nube");
            
            if (window.cmdTimeout) clearTimeout(window.cmdTimeout);
            window.cmdTimeout = setTimeout(() => {
                setConexionModo("OFFLINE");
                showToast("El equipo no responde. Parece estar APAGADO.", true);
                window.cmdTimeout = null;
            }, 15000); // 15 segundos de tolerancia
            
        } catch(e) {
            console.error("Error enviando comando a Nube:", e);
            customAlert("No hay conexión con la Nube. Conectate por BLE.");
        }
    } else {
        customAlert("Sin conexión disponible.");
    }
}

// === UI UPDATER ===
let ultimoMensaje = "";

function showToast(msg, isWarning=false) {
    if (!msg) return;
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    if(isWarning) toast.classList.add('warning');
    toast.innerText = msg;
    container.appendChild(toast);
    
    // Trigger animation
    setTimeout(() => toast.classList.add('show'), 10);
    
    // Remove after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function customAlert(mensaje, titulo = "Atención", btnOkText = "Cerrar") {
    const modal = document.getElementById('customModal');
    document.getElementById('modalTitle').innerText = titulo;
    document.getElementById('modalMessage').innerHTML = mensaje.replace(/\n/g, '<br>');
    const btnCancel = document.getElementById('btnModalCancel');
    const btnOk = document.getElementById('btnModalOk');
    
    btnCancel.style.display = 'none';
    btnOk.innerText = btnOkText;
    modal.style.display = 'flex';
    
    return new Promise((resolve) => {
        btnOk.onclick = () => {
            modal.style.display = 'none';
            resolve(true);
        };
    });
}

function customConfirm(mensaje, titulo = "Confirmación", btnOkText = "Aceptar", btnCancelText = "Cancelar") {
    const modal = document.getElementById('customModal');
    document.getElementById('modalTitle').innerText = titulo;
    document.getElementById('modalMessage').innerHTML = mensaje.replace(/\n/g, '<br>');
    const btnCancel = document.getElementById('btnModalCancel');
    const btnOk = document.getElementById('btnModalOk');
    
    btnOk.innerText = btnOkText;
    btnCancel.innerText = btnCancelText;
    
    btnCancel.style.display = 'block';
    modal.style.display = 'flex';
    
    return new Promise((resolve) => {
        btnOk.onclick = () => {
            modal.style.display = 'none';
            resolve(true);
        };
        btnCancel.onclick = () => {
            modal.style.display = 'none';
            resolve(false);
        };
    });
}

function updateLocalTimerDisplay() {
    const lblEstadoSubtexto = document.getElementById('lblEstadoSubtexto');
    if (currentEstado === "esperando_dosis" || currentEstado === "esperando_manual") {
        let displaySec = Math.max(0, currentTEspera - currentDosisSec);
        lblEstadoSubtexto.innerText = `Dosis ${currentTipo} - T.Espera: ${displaySec}s (Dosis de ${currentTDosis}s)`;
    } else if (currentEstado === "dosificando" || currentEstado === "manual") {
        let displaySec = Math.max(0, currentTDosis - currentDosisSec);
        lblEstadoSubtexto.innerText = `Dosis ${currentTipo} - Dosificando: ${displaySec}s restantes`;
    } else if (currentEstado === "solo_bomba") {
        if (currentDosisSec > 31000000) {
            lblEstadoSubtexto.innerText = "Apagado de forma manual";
        } else {
            const mins = Math.ceil(currentDosisSec / 60);
            lblEstadoSubtexto.innerText = `Apagado dentro de ${mins} minuto${mins !== 1 ? 's' : ''}`;
        }
    }
}

function updateUI(data) {
    if (!data) return;
    globalEstadoDosificador = data.estado || "inactivo";
    globalRefuerzo = data.Refuerzo || 0;
    
    const panelEstado = document.getElementById('panelEstado');
    const lblEstado = document.getElementById('lblEstado');
    const lblEstadoSubtexto = document.getElementById('lblEstadoSubtexto');
    
    // Predeterminado
    panelEstado.className = "panel-estado bg-orange-soft text-orange-dark";
    lblEstadoSubtexto.className = "estado-subtexto text-black";
    lblEstado.innerText = data.estado || "Desconocido";
    lblEstadoSubtexto.innerText = "Esperando eventos...";
    
    if (data.estado === "dosificando" || data.estado === "manual" || data.estado === "esperando_dosis" || data.estado === "esperando_manual" || data.estado === "solo_bomba") {
        panelEstado.className = "panel-estado bg-green-soft text-green-dark";
        lblEstadoSubtexto.className = "estado-subtexto text-green-dark";
        
        let t_espera = (parseInt(data.EsperaMin) || 0) * 60 + (parseInt(data.Espera) || 0);
        let baseTDosis = (parseInt(data.DosisMin) || 0) * 60 + (parseInt(data.Dosis) || 0);
        let t_dosis = (data.dosis_total_seg !== undefined && data.dosis_total_seg !== null) ? parseInt(data.dosis_total_seg) : baseTDosis;
        
        currentEstado = data.estado;
        currentTipo = (data.estado === "manual" || data.estado === "esperando_manual") ? "Manual" : "Programada";
        currentTEspera = t_espera;
        currentTDosis = t_dosis;
        
        // Calcular antiguedad de los datos usando rtc_fecha y rtc_hora de la placa
        let ageSeconds = 0;
        if (data.rtc_fecha && data.rtc_hora) {
            const boardTime = new Date(`${data.rtc_fecha}T${data.rtc_hora}`).getTime();
            if (!isNaN(boardTime)) {
                ageSeconds = Math.floor((Date.now() - boardTime) / 1000);
                if (ageSeconds < 0 || ageSeconds > 86400) ageSeconds = 0; // Sanity check
            }
        }
        
        if (data.estado && data.estado !== pwaEstadoAnterior) {
            pwaEstadoAnterior = data.estado;
            pwaInicioEstado = Date.now();
            currentDosisSec = 0;
        }

        if (data.estado === "solo_bomba") {
            let realRemaining = (data.t_bomba_off_seg || 0) - ageSeconds;
            if (realRemaining < 0) realRemaining = 0;
            
            if (Math.abs(currentDosisSec - realRemaining) > 5 || !localTimer) {
                currentDosisSec = realRemaining;
            }
            lblEstado.innerText = "Solo Bomba";
        } else {
            let realElapsed = (data.t_estado || 0) + ageSeconds;
            let pwaElapsed = Math.floor((Date.now() - pwaInicioEstado) / 1000);
            
            // Fix para versiones viejas de la placa (v4.0) que no resetean t_estado al pasar de espera a dosis
            if (Math.abs(realElapsed - pwaElapsed) > 8) {
                realElapsed = pwaElapsed;
            }
            
            if (realElapsed < 0) realElapsed = 0;
            
            if (Math.abs(currentDosisSec - realElapsed) > 3 || !localTimer) {
                currentDosisSec = realElapsed;
            }
            if (data.estado === "esperando_dosis" || data.estado === "esperando_manual") lblEstado.innerText = "Esperando Dosis";
            else lblEstado.innerText = "Dosificando";
        }
        
        updateLocalTimerDisplay();
        
        if (!localTimer) {
            localTimer = setInterval(() => {
                if(currentEstado === "solo_bomba") {
                    if(currentDosisSec > 0) currentDosisSec--;
                } else {
                    currentDosisSec++;
                }
                updateLocalTimerDisplay();
            }, 1000);
        }
    } else {
        if (localTimer) {
            clearInterval(localTimer);
            localTimer = null;
        }
        lblEstado.innerText = "Inactivo";
        lblEstadoSubtexto.innerText = "Esperando eventos...";
    }
    
    // Mostrar dosis anuladas
    const lblDosisAnuladas = document.getElementById('lblDosisAnuladas');
    if (data.DosisNo && data.DosisNo > 0) {
        lblDosisAnuladas.innerText = `Dosis Anuladas: ${data.DosisNo}`;
        lblDosisAnuladas.style.display = 'block';
    } else {
        if(lblDosisAnuladas) lblDosisAnuladas.style.display = 'none';
    }
    
    const panelBomba = document.getElementById('panelBomba');
    const lblBomba = document.getElementById('lblBomba');
    if (data.bomba) {
        panelBomba.className = "panel-small bg-green-soft text-green-dark";
        lblBomba.innerText = "ON";
        document.getElementById('tglBomba').checked = true;
    } else {
        panelBomba.className = "panel-small bg-red-soft text-red-dark";
        lblBomba.innerText = "OFF";
        document.getElementById('tglBomba').checked = false;
    }
    
    if (data.temp_rtc !== undefined) {
        const temp = Math.round(data.temp_rtc);
        document.getElementById('lblTemp').innerText = `${temp}°C`;
        
        const panelTemp = document.getElementById('panelTemp');
        const lblTempWarning = document.getElementById('lblTempWarning');
        
        if (temp > 30) {
            panelTemp.className = "panel-small bg-red-soft text-red-dark";
            lblTempWarning.style.display = 'block';
        } else if (temp >= 28 && temp <= 30) {
            panelTemp.className = "panel-small bg-orange-soft text-orange-dark";
            lblTempWarning.style.display = 'none';
        } else {
            panelTemp.className = "panel-small bg-blue-soft text-blue-dark";
            lblTempWarning.style.display = 'none';
        }
    } else {
        document.getElementById('lblTemp').innerText = "--°C";
        document.getElementById('panelTemp').className = "panel-small bg-blue-soft text-blue-dark";
        if(document.getElementById('lblTempWarning')) document.getElementById('lblTempWarning').style.display = 'none';
    }
    
    const panelRefuerzo = document.getElementById('panelRefuerzo');
    const lblRefuerzo = document.getElementById('lblRefuerzo');
    if (data.Refuerzo) {
        panelRefuerzo.className = "panel-small bg-green-soft text-green-dark";
        lblRefuerzo.innerText = "ON";
        document.getElementById('tglRefuerzo').checked = true;
    } else {
        panelRefuerzo.className = "panel-small bg-orange-soft text-orange-dark";
        lblRefuerzo.innerText = "OFF";
        document.getElementById('tglRefuerzo').checked = false;
    }
    
    document.getElementById('tglDosisManual').checked = (data.estado === "manual");

    document.getElementById('lblTemporada').innerText = data.temporada || "-";
    document.getElementById('lblDosisTotal').innerText = data.dosis_total_seg || "0";
    document.getElementById('lblRTC').innerText = `${data.rtc_fecha || ''} ${data.rtc_hora || ''}`;
    if(data.id_equipo) document.getElementById('lblMac').innerText = data.id_equipo;

    
    if (data.mensaje && data.mensaje !== ultimoMensaje) {
        showToast(data.mensaje);
    }
    ultimoMensaje = data.mensaje || "";

    if (data.cronograma && data.rtc_hora && data.rtc_fecha) {
        document.getElementById('lblProxDosis').innerText = calcularProximaDosis(data.cronograma, data.rtc_fecha, data.rtc_hora);
    }
    
    if (data.historial) {
        renderHistorial(data.historial);
    }

    if (!unsavedChanges) {
        if(document.getElementById('inpFverano')) document.getElementById('inpFverano').value = dateToStr(data.Fverano);
        if(document.getElementById('inpFinvierno')) document.getElementById('inpFinvierno').value = dateToStr(data.Finvierno);
        if(document.getElementById('inpDosis')) document.getElementById('inpDosis').value = data.Dosis;
        if(document.getElementById('inpDosisMin')) document.getElementById('inpDosisMin').value = data.DosisMin;
        if(document.getElementById('inpEspera')) document.getElementById('inpEspera').value = data.Espera;
        if(document.getElementById('inpEsperaMin')) document.getElementById('inpEsperaMin').value = data.EsperaMin;
        
        if (data.wifi_ssid !== undefined) {
            const inpWifiSsid = document.getElementById('inpWifiSsid');
            if (inpWifiSsid && !document.activeElement.isEqualNode(inpWifiSsid)) {
                inpWifiSsid.value = data.wifi_ssid;
            }
        }
        
        let container = document.getElementById('cronogramaContainer');
        if (container) {
            container.innerHTML = "";
            if (data.cronograma) {
                let cronData = data.cronograma;
                if (typeof cronData === 'string') {
                    try { cronData = JSON.parse(cronData); } catch(e) { cronData = []; }
                }
                if (Array.isArray(cronData)) {
                    cronData.forEach(c => {
                        agregarFilaCronograma(timeToStr(c.on || c.hora), c.duracion, (c.dosis !== undefined ? c.dosis : (c.dosificar ? 1 : 0)), c.dias || "0123456");
                    });
                }
            }
        }
        
        if (document.getElementById('inpDosisNo')) document.getElementById('inpDosisNo').value = data.DosisNo;
        
        if (data.rtc_fecha && data.rtc_hora) {
            const parts = data.rtc_fecha.split("-");
            const timeParts = data.rtc_hora.split(":");
            const rtcDate = new Date(parts[0], parts[1]-1, parts[2], timeParts[0], timeParts[1], timeParts[2]);
            const diffMins = Math.abs((new Date() - rtcDate) / 60000);
            
            // La auto-sincronización RTC se eliminó porque el ESP32 usa NTP.
            // Para modo BLE, existe el botón manual.
        }
    }
}

function renderHistorial(historialArray) {
    const btn = document.getElementById('btnActualizarHistorial');
    if (btn) {
        btn.innerHTML = 'Actualizar Historial';
        btn.disabled = false;
    }

    const tbody = document.getElementById('historialBody');
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!historialArray || historialArray.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:1rem; color:var(--text-muted);">Sin registros</td></tr>';
    } else {
        historialArray.forEach(h => {
            const tr = document.createElement('tr');
            const isPerdida = h.tipo === "Perdida";
            const isFalla = h.segundos === 0 || (h.temp && h.temp.includes("Fallo"));
            tr.style.borderBottom = "1px solid var(--border)";
            if (isPerdida) tr.className = "fila-perdida";
            else if (isFalla) tr.className = "falla";
            
            const tipoText = h.tipo ? `(${h.tipo})` : '';
            tr.innerHTML = `<td style="padding:0.5rem 0;">${h.fecha || ''}</td><td style="padding:0.5rem 0;">${h.segundos || 0}s</td><td style="padding:0.5rem 0;">${h.temp || ''} ${h.ref ? '(Ref)' : ''} <span style="color:var(--text-muted); font-size:0.8rem">${tipoText}</span></td>`;
            tbody.appendChild(tr);
        });
    }
}

function renderSysLog(sysLogArray) {
    const term = document.getElementById('terminalLog');
    if (term) {
        if (!sysLogArray || sysLogArray.length === 0) {
            term.innerText = "Esperando eventos de diagnóstico (Nueva arquitectura)...";
        }
    }
}


function calcularProximaDosis(cronograma, rtc_fecha, rtc_hora) {
    let cronData = cronograma;
    if (typeof cronData === 'string') {
        try { cronData = JSON.parse(cronData); } catch(e) { cronData = []; }
    }
    if (!cronData || !Array.isArray(cronData) || cronData.length === 0) return "Sin programar";
    if (!rtc_fecha || !rtc_hora) return "Calculando...";

    const horaActual = rtc_hora.replace(/:/g, "");
    const parts = rtc_fecha.split("-");
    const dObj = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    const currentWeekday = (dObj.getDay() + 6) % 7;
    const nombres = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];

    for (let d = 0; d < 7; d++) {
        const checkDay = (currentWeekday + d) % 7;
        let eventosDelDia = cronData.filter(c => ((c.dosis == 1) || (c.dosificar == true)) && (c.dias || "0123456").includes(checkDay.toString()));
        
        if (d === 0) {
            eventosDelDia = eventosDelDia.filter(c => (c.on || c.hora) > horaActual);
        }
        
        if (eventosDelDia.length > 0) {
            eventosDelDia.sort((a,b) => (a.on || a.hora).localeCompare(b.on || b.hora));
            const nextOn = timeToStr(eventosDelDia[0].on || eventosDelDia[0].hora);
            if (d === 0) return `Hoy ${nextOn}`;
            if (d === 1) return `Mañana ${nextOn}`;
            return `${nombres[checkDay]} ${nextOn}`;
        }
    }
    return "Ningún día";
}

function agregarFilaCronograma(timeVal, durVal, dosisVal, diasVal = "0123456") {
    const container = document.getElementById('cronogramaContainer');
    if (container.querySelectorAll('.crono-row').length >= 10) { customAlert("Máximo 10 horarios permitidos"); return; }
    
    const div = document.createElement('div');
    div.className = 'crono-row';
    div.style.flexDirection = 'column';
    div.style.alignItems = 'stretch';
    
    const topRow = document.createElement('div');
    topRow.style.display = 'flex';
    topRow.style.gap = '0.5rem';
    topRow.style.alignItems = 'center';
    topRow.innerHTML = `<input type="time" class="inp-time" value="${timeVal}" required style="flex:1; padding:0.2rem;"><input type="number" class="inp-dur" value="${durVal}" min="1" max="120" style="flex:1; padding:0.2rem;" placeholder="Min"><label style="flex:1; margin:0; display:flex; justify-content:center;"><input type="checkbox" class="inp-dosis" ${dosisVal ? 'checked' : ''}></label><button class="btn-del" style="padding:0.2rem 0.5rem; width:35px;">X</button>`;
    
    topRow.querySelector('.btn-del').onclick = () => { div.remove(); setUnsaved(); };
    
    const diasRow = document.createElement('div');
    diasRow.className = 'day-container';
    const diasLetras = ['L', 'M', 'X', 'J', 'V', 'S', 'D'];
    diasLetras.forEach((letra, index) => {
        const btn = document.createElement('div');
        btn.className = 'day-btn';
        if (diasVal.includes(index.toString())) btn.classList.add('active');
        btn.innerText = letra;
        btn.dataset.day = index;
        btn.onclick = () => {
            btn.classList.toggle('active');
            setUnsaved();
        };
        diasRow.appendChild(btn);
    });

    div.appendChild(topRow);
    div.appendChild(diasRow);
    container.appendChild(div);
}

// === COMMAND SWITCHES ===
document.getElementById('tglBomba').onchange = (e) => {
    if (e.target.checked) sendCommand({comando: "bombasi"});
    else sendCommand({comando: "bombano"});
};

document.getElementById('tglDosisManual').onchange = async (e) => {
    if (e.target.checked) {
        const bombaActiva = document.getElementById('tglBomba').checked;
        if (!bombaActiva) {
            e.target.checked = false;
            if (await customConfirm("La bomba está apagada. Se encenderá para dosificar y se apagará 30 minutos después de terminar. ¿Continuar?", "Dosis Manual", "Aceptar", "Cancelar")) {
                e.target.checked = true;
                sendCommand({comando: "manualsi"});
            }
        } else {
            sendCommand({comando: "manualsi"});
        }
    } else {
        sendCommand({comando: "manualno"});
    }
};

document.getElementById('tglRefuerzo').onchange = (e) => {
    if (e.target.checked) sendCommand({comando: "refuerzosi"});
    else sendCommand({comando: "refuerzono"});
};

document.getElementById('btnGuardarConfig').onclick = () => {
    const dMin = parseInt(document.getElementById('inpDosisMin').value) || 0;
    const dSeg = parseInt(document.getElementById('inpDosis').value) || 0;
    const eMin = parseInt(document.getElementById('inpEsperaMin').value) || 0;
    const eSeg = parseInt(document.getElementById('inpEspera').value) || 0;
    
    const d1 = document.getElementById('inpFverano').value;
    const d2 = document.getElementById('inpFinvierno').value;
    
    let payload = {
        comando: "config_general",
        Dosis: dSeg,
        DosisMin: dMin,
        Espera: eSeg,
        EsperaMin: eMin
    };
    
    if (d1 && d2) {
        payload.Fverano = d1.split('-')[1] + d1.split('-')[2];
        payload.Finvierno = d2.split('-')[1] + d2.split('-')[2];
    }
    
    sendCommand(payload);
    
    const affectedBtns = document.querySelectorAll('#tab-prog .btn');
    affectedBtns.forEach(b => b.classList.remove('pulse'));
    clearUnsaved();
};

document.getElementById('btnGuardarAnular').onclick = () => {
    sendCommand({comando: "config_anular", DosisNo: parseInt(document.getElementById('inpDosisNo').value)});
    clearUnsaved();
};

document.getElementById('btnProgAuto').onclick = async () => {
    if(await customConfirm("Se eliminarán los horarios programados y se establecerá un nuevo esquema de filtrado. ¿Continuar?", "Programa Automático")) {
        const container = document.getElementById('cronogramaContainer');
        container.innerHTML = "";
        agregarFilaCronograma("09:00", 120, 0);
        agregarFilaCronograma("15:00", 120, 0);
        agregarFilaCronograma("21:00", 60, 1);
        setUnsaved();
    }
};

document.getElementById('btnAgregarHorario').onclick = () => {
    agregarFilaCronograma("", 0, 1, "0123456");
    setUnsaved();
};

document.getElementById('btnGuardarCronograma').onclick = () => {
    const cronograma = [];
    document.querySelectorAll('.crono-row').forEach(fila => {
        const timeInput = fila.querySelector('.inp-time').value.replace(":", "");
        const durInput = parseInt(fila.querySelector('.inp-dur').value);
        let diasStr = "";
        fila.querySelectorAll('.day-btn.active').forEach(b => diasStr += b.dataset.day);
        if(timeInput && !isNaN(durInput)) cronograma.push({
            on: timeInput, 
            duracion: durInput, 
            dosis: fila.querySelector('.inp-dosis').checked ? 1 : 0, 
            dias: diasStr
        });
    });
    sendCommand({comando: "config_cronograma", cronograma: cronograma});
    clearUnsaved();
};

// Listeners for unsaved changes in Program Tab
document.getElementById('tab-prog').addEventListener('input', setUnsaved);
document.getElementById('tab-prog').addEventListener('change', setUnsaved);

document.getElementById('btnActualizarHistorial').onclick = () => {
    const btn = document.getElementById('btnActualizarHistorial');
    btn.innerHTML = '<span class="icon spin">sync</span> Actualizando...';
    btn.disabled = true;
    sendCommand({comando: "pedir_historial"});
    
    setTimeout(() => {
        btn.innerHTML = 'Actualizar Historial';
        btn.disabled = false;
    }, 10000);
};

document.getElementById('btnCopyMac').onclick = () => {
    const mac = document.getElementById('lblMac').innerText;
    if (mac && mac !== "-") {
        const user = auth.currentUser;
        let copyText = `MAC/ID: ${mac}`;
        if (user) {
            const nombre = user.displayName || "Usuario";
            const email = user.email || "Sin email";
            copyText = `Nombre: ${nombre}\nEmail: ${email}\nMAC/ID: ${mac}`;
        }
        
        navigator.clipboard.writeText(copyText).then(() => {
            showToast("Datos copiados al portapapeles");
        }).catch(err => {
            console.error('Error al copiar: ', err);
            showToast("No se pudieron copiar los datos", true);
        });
    }
};

function syncRTC() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const h = String(now.getHours()).padStart(2, '0');
    const min = String(now.getMinutes()).padStart(2, '0');
    sendCommand({comando: "sync_rtc", fecha: `${y}-${m}-${d}`, hora: `${h}:${min}`});
}

// === ADMIN PANEL ===

// Tech Mode Connections
document.getElementById('btnConnectRemote').addEventListener('click', () => {
    const rmac = document.getElementById('inpRemoteMac').value.trim();
    if (rmac) {
        if(unsubSnapshot) unsubSnapshot();
        currentMac = rmac;
        document.getElementById('lblMac').innerText = currentMac;
        
        // Restaurar el cartel a su estado "controlando"
        document.getElementById('headerTechMode').style.display = 'block';
        document.getElementById('headerTechMode').style.background = 'var(--danger)';
        document.getElementById('headerTechMode').innerHTML = `⚠ MODO TÉCNICO: Controlando equipo remoto <br>
            <span id="headerTechMac" style="font-family: monospace; font-size: 1rem;">${currentMac}</span>`;
            
        connectNube();
        switchTab('panel');
    } else {
        customAlert("Ingresa un MAC/ID válido.");
    }
});

document.getElementById('btnDisconnectTech').addEventListener('click', async () => {
    if(unsubSnapshot) unsubSnapshot();
    document.getElementById('headerTechMode').style.display = 'none';
    setConexionModo("OFFLINE");
    // Reload user's original device
    await loadUserProfile(currentUser.uid);
    switchTab('admin');
});

window.connectRemoteDevice = (mac) => {
    document.getElementById('inpRemoteMac').value = mac;
    document.getElementById('btnConnectRemote').click();
};

async function loadAdminGlobal() {
    const container = document.getElementById('adminListContainer');
    container.innerHTML = "Cargando equipos...";
    try {
        const equiposSnap = await getDocs(collection(db, "equipos"));
        container.innerHTML = "";
        
        for (const docSnap of equiposSnap.docs) {
            const data = docSnap.data();
            const mac = docSnap.id;
            
            // Buscar si hay un usuario asignado a este equipo
            let owner = "Sin asignar";
            const q = query(collection(db, "usuarios"), where("id_equipo", "==", mac));
            const userSnaps = await getDocs(q);
            if (!userSnaps.empty) {
                const userData = userSnaps.docs[0].data();
                owner = userData.email || "Usuario sin email";
                if (userData.nombre) owner = `${userData.nombre} (${owner})`;
            }
            
            const div = document.createElement('div');
            div.style = "background: var(--bg-color); padding: 1rem; border-radius: var(--radius); margin-bottom: 1rem; border: var(--border-width) solid var(--border);";
            
            const btnHtml = `
                <div style="display:flex; gap:0.5rem; margin-top:1rem;">
                    <button class="btn" style="padding: 0.5rem; flex:1;" onclick="connectRemoteDevice('${mac}')">Controlar Equipo</button>
                    <button class="btn danger" style="padding: 0.5rem; flex:1;" onclick="adminResetEquipo('${mac}')">Reset de Fábrica</button>
                </div>
            `;
            
            let firmwareStr = data.version || 'Desconocida';
            if (firmwareStr === "V4.0_NUBE") firmwareStr = "V4.0 (Nube)";
            
            div.innerHTML = `
                <h3 style="color: var(--accent); margin-bottom: 0.5rem;">ID: ${mac}</h3>
                <p><strong>Usuario:</strong> ${owner}</p>
                <p><strong>Última Sincro:</strong> ${data.rtc_fecha || '-'} ${data.rtc_hora || '-'}</p>
                <p><strong>Firmware:</strong> ${firmwareStr}</p>
                ${btnHtml}
            `;
            container.appendChild(div);
        }
        
        if(equiposSnap.empty) container.innerHTML = "No hay equipos registrados.";
    } catch(e) {
        console.error("Error cargando admin:", e);
        container.innerHTML = "Error cargando la lista.";
    }
}

// === SUPER ADMIN ===
async function loadAdminEmails() {
    const container = document.getElementById('adminEmailsContainer');
    container.innerHTML = "Cargando...";
    try {
        const snap = await getDocs(collection(db, "administradores"));
        container.innerHTML = "";
        snap.forEach(docSnap => {
            const email = docSnap.id;
            const div = document.createElement('div');
            div.style = "display:flex; justify-content:space-between; align-items:center; padding: 0.5rem; border-bottom: 1px solid var(--border);";
            div.innerHTML = `
                <span style="font-weight:bold; color:var(--text-main);">${email}</span>
                ${email !== superAdminEmail.toLowerCase() ? `<button class="btn danger" style="padding:0.2rem 0.5rem; width:auto;" onclick="removeAdmin('${email}')">Eliminar</button>` : '<span style="color:var(--text-muted); font-size:0.8rem;">(Súper Admin)</span>'}
            `;
            container.appendChild(div);
        });
    } catch(e) {
        console.error(e);
        container.innerHTML = "Error cargando administradores.";
    }
}

document.getElementById('btnAddAdmin').addEventListener('click', async () => {
    const email = document.getElementById('inpNewAdminEmail').value.trim().toLowerCase();
    if(!email) return;
    try {
        await setDoc(doc(db, "administradores", email), { rol: "tecnico" });
        document.getElementById('inpNewAdminEmail').value = "";
        loadAdminEmails();
    } catch(e) {
        console.error(e);
        customAlert("Error al agregar administrador.");
    }
});

window.removeAdmin = async (email) => {
    if(await customConfirm(`¿Estás seguro de quitar el acceso técnico a ${email}?`, "Remover Técnico")) {
        try {
            await deleteDoc(doc(db, "administradores", email));
            loadAdminEmails();
        } catch(e) {
            console.error(e);
        }
    }
};

window.adminResetEquipo = async function(mac) {
    const confirm1 = await customConfirm(`¿Estás seguro de resetear el equipo ${mac}? Esto borrará la configuración WiFi y los horarios en la placa.`, "Reset de Fábrica");
    if (!confirm1) return;

    const confirm2 = await customConfirm(`¿Estás TOTALMENTE SEGURO?\nEl equipo perderá su conexión y no podrás controlarlo remotamente hasta que alguien lo vuelva a vincular por Bluetooth.`, "Doble Confirmación", "Sí, resetear", "Cancelar");
    if (!confirm2) return;

    try {
        // 1. Enviar comando a la placa
        const cmdObj = { comando: "reset_fabrica" };
        // 2. Limpiar datos en la nube (dejando comando_pendiente)
        await setDoc(doc(db, "equipos", mac), { 
            comando_pendiente: JSON.stringify(cmdObj),
            estado: "Fábrica (Reseteado)",
            cronograma: [],
            historial: []
        }, { merge: true });
        
    } catch(e) {
        console.error("Error reseteando", e);
        showToast("Error al resetear", true);
    }
}

// Actualizar reloj de la interfaz
setInterval(() => {
    const now = new Date();
    document.getElementById('lblCurrentTime').innerText = now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
}, 1000);

// === SOPORTE TAB LOGIC ===
document.getElementById('btnSoporteWsp').addEventListener('click', () => {
    let msg = "Hola Dosimat. ";
    if (currentMac) msg += `Mi equipo es el MAC/ID: ${currentMac}. `;
    msg += "Necesito hacerles una consulta.";
    const url = `https://wa.me/${soporteConfig.whatsapp.replace(/\+/g, '')}?text=${encodeURIComponent(msg)}`;
    window.open(url, '_blank');
});

document.getElementById('btnSoporteMail').addEventListener('click', () => {
    let subject = "Consulta de Soporte Técnico";
    let body = "Hola Dosimat,\n\n";
    if (currentMac) body += `Mi MAC/ID es: ${currentMac}\n\n`;
    const url = `mailto:${soporteConfig.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    window.location.href = url;
});

document.getElementById('btnRecomendar').addEventListener('click', async () => {
    if (navigator.share) {
        try {
            await navigator.share({
                title: 'Dosimat IoT',
                text: '¡Hola! Te recomiendo Dosimat, el automatizador de cloro para piletas que estoy usando.',
                url: soporteConfig.web
            });
        } catch (err) {
            console.log("Error al compartir: ", err);
        }
    } else {
        // Fallback
        const txt = `¡Hola! Te recomiendo Dosimat, el automatizador de cloro para piletas que estoy usando: ${soporteConfig.web}`;
        navigator.clipboard.writeText(txt).then(() => {
            showToast("Texto copiado para compartir");
        });
    }
});

document.getElementById('btnSaveAdminContact').addEventListener('click', async () => {
    const w = document.getElementById('inpAdminWhatsApp').value.trim();
    const e = document.getElementById('inpAdminEmail').value.trim();
    const web = document.getElementById('inpAdminWeb').value.trim();
    
    try {
        await setDoc(doc(db, "configuracion", "soporte"), {
            whatsapp: w, email: e, web: web
        });
        soporteConfig = { whatsapp: w, email: e, web: web };
        showToast("Contacto actualizado");
    } catch (err) {
        console.error(err);
        customAlert("Error guardando configuración.");
    }
});
