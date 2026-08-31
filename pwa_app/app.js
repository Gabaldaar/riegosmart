// app.js - UI Controller

// Configuración de Firebase del proyecto RiegoSmart (Reemplaza "TU_API_KEY_AQUI" con tu Web API Key)
const firebaseConfig = {
  apiKey: "AIzaSyDOXu7MTGkr0In2NluAggFejTW7Ukap604", 
  authDomain: "riego-smart-b8487.firebaseapp.com",
  projectId: "riego-smart-b8487",
  storageBucket: "riego-smart-b8487.firebasestorage.app",
  messagingSenderId: "127532086869",
  appId: "1:127532086869:web:b3a1a142e9fd4f9dc186dc"
};

let db   = null;
let auth = null;
let currentUser = null;
let firestoreUnsubscribe = null;

// Inicializar Firebase (Auth + Firestore)
if (typeof firebase !== 'undefined' && firebaseConfig.apiKey !== "TU_API_KEY_AQUI") {
    try {
        firebase.initializeApp(firebaseConfig);
        db   = firebase.firestore();
        auth = firebase.auth();
    } catch (e) {
        console.error("Error inicializando Firebase SDK:", e);
    }
}

// Estado global de la App
const state = {
    chipId: localStorage.getItem('CHIP_ID') || null,
    token: localStorage.getItem('TOKEN') || null,
    telemetria: null,
    deviceConfig: {
        max_zonas: 8,
        modo_bomba: true,
        calibracion_temp: 0.0,
        sensor_lluvia_activo: true,
        sensor_lluvia_tipo: "NA",
        ajuste_estacional: 100,
        ajustes_estacionales: [
            {"nombre": "Verano", "inicio": "12-21", "fin": "03-20", "porcentaje": 100},
            {"nombre": "Otono", "inicio": "03-21", "fin": "06-20", "porcentaje": 100},
            {"nombre": "Invierno", "inicio": "06-21", "fin": "09-20", "porcentaje": 100},
            {"nombre": "Primavera", "inicio": "09-21", "fin": "12-20", "porcentaje": 100}
        ],
        nombres_zonas: {
            "Z1": "Zona 1", "Z2": "Zona 2", "Z3": "Zona 3", "Z4": "Zona 4",
            "Z5": "Zona 5", "Z6": "Zona 6", "Z7": "Zona 7", "Z8": "Zona 8"
        },
        programas: {}
    },
    activeProgTab: 'A',
    tempProgData: {}, // Datos del programa que se está editando
    hasUnsavedChanges: false
};

// Cargar nombres de zonas guardados localmente si existen
if (state.chipId) {
    const localNames = localStorage.getItem(`NOMBRES_ZONAS_${state.chipId}`);
    if (localNames) {
        try {
            state.deviceConfig.nombres_zonas = JSON.parse(localNames);
        } catch (e) {
            console.error("[INIT] Error parseando nombres de zonas locales:", e);
        }
    }
}

// Helper para traducir IDs de zonas de forma híbrida
function obtenerNombreZona(zonaId) {
    if (!state.deviceConfig.nombres_zonas) return `Zona ${zonaId}`;
    const zKey = String(zonaId).toUpperCase().startsWith('Z') ? String(zonaId).toUpperCase() : `Z${zonaId}`;
    return state.deviceConfig.nombres_zonas[zKey] || state.deviceConfig.nombres_zonas[zonaId] || `Zona ${zonaId}`;
}

// Helper para dar formato DD/MM a las fechas de las temporadas (almacenadas como MM-DD)
function formatSeasonDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string' || !dateStr.includes('-')) return dateStr;
    const parts = dateStr.split('-');
    return `${parts[1]}/${parts[0]}`; // DD/MM
}

// Iconos y colores representativos de cada temporada
const seasonIconMap = {
    "Verano": { icon: "sun", color: "text-amber-500 dark:text-amber-400" },
    "Otono": { icon: "leaf", color: "text-orange-500 dark:text-orange-400" },
    "Invierno": { icon: "snowflake", color: "text-blue-500 dark:text-blue-400" },
    "Primavera": { icon: "sprout", color: "text-emerald-500 dark:text-emerald-400" }
};
function normalizeProgramasData(programas) {
    if (!programas || typeof programas !== 'object') return {};
    const mapKeys = {
        "1": "A", "2": "B", "3": "C", "4": "D",
        "P1": "A", "P2": "B", "P3": "C", "P4": "D",
        "a": "A", "b": "B", "c": "C", "d": "D"
    };
    const norm = {};
    for (const [pKey, pObj] of Object.entries(programas)) {
        if (!pObj || typeof pObj !== 'object') continue;
        const targetPKey = mapKeys[pKey] || pKey;
        
        const pObjCloned = JSON.parse(JSON.stringify(pObj));
        if (pObjCloned.zonas && typeof pObjCloned.zonas === 'object') {
            const zNorm = {};
            for (const [zKey, zVal] of Object.entries(pObjCloned.zonas)) {
                if (!zVal || typeof zVal !== 'object') continue;
                const zNum = String(zKey).toUpperCase().replace('Z', '');
                const targetZKey = `Z${zNum}`;
                
                const mins = parseInt(zVal.minutos) || 0;
                if (mins > 0) {
                    const cMin = parseInt(zVal.cycle_min) || 0;
                    const sMin = parseInt(zVal.soak_min) || 0;
                    const hasCycle = Boolean(sMin > 0 && cMin > 0 && cMin < mins);
                    
                    zNorm[targetZKey] = {
                        minutos: mins,
                        cycle_min: hasCycle ? cMin : 0,
                        soak_min: hasCycle ? sMin : 0
                    };
                }
            }
            pObjCloned.zonas = zNorm;
        } else {
            pObjCloned.zonas = {};
        }
        
        norm[targetPKey] = pObjCloned;
    }
    return norm;
}

function escucharConfiguracionFirestore(chipId) {
    if (!db) {
        console.warn("[FIRESTORE] Firebase no inicializado. Operando en modo local (BLE/MQTT directo).");
        return;
    }
    
    if (firestoreUnsubscribe) {
        firestoreUnsubscribe();
    }
    
    console.log(`[FIRESTORE] Escuchando cambios en tiempo real en: dispositivos/${chipId}`);
    firestoreUnsubscribe = db.collection("dispositivos").doc(chipId).onSnapshot((doc) => {
        if (doc.exists) {
            const data = doc.data();
            console.log("[FIRESTORE] Configuración recibida desde la nube:", data);

            // Reclamar propiedad si el dispositivo no tiene dueño o el owner_uid es de una
            // sesión anónima/antigua diferente al usuario actual.
            if (currentUser && (!data.owner_uid || data.owner_uid !== currentUser.uid)) {
                db.collection("dispositivos").doc(chipId)
                    .update({ owner_uid: currentUser.uid })
                    .then(() => console.log("[AUTH] owner_uid actualizado al usuario actual."))
                    .catch(() => {}); // Silencioso si ya tiene otro dueño real
            }

            // Fusión segura de datos locales y nube
            state.deviceConfig.max_zonas = data.max_zonas || state.deviceConfig.max_zonas;
            state.deviceConfig.modo_bomba = data.modo_bomba !== undefined ? data.modo_bomba : state.deviceConfig.modo_bomba;
            if (data.nombres_zonas) {
                state.deviceConfig.nombres_zonas = data.nombres_zonas;
                localStorage.setItem(`NOMBRES_ZONAS_${chipId}`, JSON.stringify(data.nombres_zonas));
            }
            if (data.ajustes_estacionales) {
                state.deviceConfig.ajustes_estacionales = data.ajustes_estacionales;
            }
            state.deviceConfig.programas = normalizeProgramasData(data.programas || state.deviceConfig.programas);
            state.deviceConfig.config_version = data.config_version || state.deviceConfig.config_version;
            
            // Sincronizar el retraso por lluvia desde la nube (Firestore usa epoch 1970, la app usa epoch 2000 internamente)
            if (data.timestamp_rain_delay !== undefined) {
                state.deviceConfig.timestamp_rain_delay = data.timestamp_rain_delay > 0 ? (data.timestamp_rain_delay - 946684800) : 0;
            }
            if (data.sensor_lluvia_delay_horas !== undefined) {
                state.deviceConfig.sensor_lluvia_delay_horas = data.sensor_lluvia_delay_horas;
            }
            if (data.sensor_lluvia_activo !== undefined) {
                state.deviceConfig.sensor_lluvia_activo = data.sensor_lluvia_activo;
            }
            if (data.sensor_lluvia_tipo !== undefined) {
                state.deviceConfig.sensor_lluvia_tipo = data.sensor_lluvia_tipo;
            }
            if (data.calibracion_temp !== undefined) {
                state.deviceConfig.calibracion_temp = data.calibracion_temp;
            }
            if (data.timestamp_sensor_lluvia_clear !== undefined) {
                state.deviceConfig.timestamp_sensor_lluvia_clear = data.timestamp_sensor_lluvia_clear > 0 ? (data.timestamp_sensor_lluvia_clear - 946684800) : 0;
            }
            
            refreshUIFromConfig();

            // Siempre mantener /usuarios/{uid} actualizado con chip y token actuales.
            // Esto garantiza que otros navegadores/dispositivos reciban los datos correctos.
            if (db && currentUser && state.token) {
                db.collection("usuarios").doc(currentUser.uid).set({
                    chipId: chipId,
                    token:  state.token
                }, { merge: true }).then(() => {
                    console.log("[FIRESTORE] Vínculo usuario→dispositivo actualizado.");
                }).catch(e => {
                    console.warn("[FIRESTORE] No se pudo actualizar vínculo:", e.message);
                });
            }
        } else {
            console.log("[FIRESTORE] Registrando nuevo equipo en Firestore...");
            // Si el documento no existe en Firestore, lo aprovisionamos automáticamente
            const tokenAcceso = state.token || "token_por_defecto_1234";
            db.collection("dispositivos").doc(chipId).set({
                config_version: 1,
                max_zonas: state.deviceConfig.max_zonas,
                modo_bomba: state.deviceConfig.modo_bomba,
                nombres_zonas: state.deviceConfig.nombres_zonas,
                ajustes_estacionales: state.deviceConfig.ajustes_estacionales || [],
                programas: state.deviceConfig.programas,
                timestamp_rain_delay: 0,
                token_acceso: tokenAcceso,
                owner_uid: currentUser ? currentUser.uid : null
            }).catch(err => {
                console.error("[FIRESTORE] Error inicializando regador en la nube:", err);
            });
            // Registrar la vinculación usuario → dispositivo
            if (currentUser) {
                db.collection("usuarios").doc(currentUser.uid).set({
                    chipId: chipId,
                    token:  tokenAcceso
                }, { merge: true }).catch(err => {
                    console.warn("[FIRESTORE] No se pudo guardar vínculo usuario-dispositivo:", err);
                });
            }
        }
    }, (error) => {
        console.error("[FIRESTORE] Error en Snapshot de Firestore:", error);
    });
}

function sincronizarConfigTecnicaAFirestore(nuevaConfigParcial) {
    if (!db || !state.chipId) return;
    
    // Incrementar versión local
    const nuevaVersion = (state.deviceConfig.config_version || 0) + 1;
    state.deviceConfig.config_version = nuevaVersion;
    
    // Preparar payload para la nube
    const payload = {
        ...nuevaConfigParcial,
        config_version: nuevaVersion
    };
    
    console.log("[FIRESTORE] Subiendo cambios a la nube...", payload);
    db.collection("dispositivos").doc(state.chipId).set(payload, { merge: true })
        .then(() => {
            console.log("[FIRESTORE] Subida a Firestore completada.");
        })
        .catch(err => {
            console.error("[FIRESTORE] Error subiendo cambios a Firestore:", err);
        });
}

let pendingCommand = false;
let _pendingLogEntries = []; // Buffer para streaming de LOG_ENTRY
let toastTimeout = null;

function showToast(msg) {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toast-msg');
    if (!toast || !toastMsg) return;
    
    toastMsg.textContent = msg;
    toast.classList.remove('opacity-0', 'translate-y-4');
    
    if (toastTimeout) clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-4');
    }, 3000);
}

// ==========================================
// INICIALIZACIÓN
// ==========================================
document.addEventListener("DOMContentLoaded", async () => {
    initTabs();
    initTheme();
    initSettingsUI();
    initSchedulerUI();
    initHelpModals();
    initAuthUI();

    // Inicializar Clima y Presets
    if (typeof presetsService !== 'undefined') presetsService.init();
    if (typeof weatherService !== 'undefined') {
        weatherService.fetchWeather();
        document.getElementById('btn-refresh-weather')?.addEventListener('click', () => {
            weatherService.fetchWeather(true);
            showToast("Clima actualizado.");
        });
        document.getElementById('btn-smart-delay-dismiss')?.addEventListener('click', () => {
            document.getElementById('smart-rain-delay-card')?.classList.add('hidden');
        });
        document.getElementById('btn-smart-delay-apply')?.addEventListener('click', () => {
            sendCmd({ comando: "RAIN_DELAY", dias: 1 });
            showToast("✓ Riego pausado por 24 horas por lluvia.");
            document.getElementById('smart-rain-delay-card')?.classList.add('hidden');
        });
    }

    // UI inicial con config por defecto (sin esperar auth)
    refreshUIFromConfig();

    // Bindings de comunicación
    comms.onConnectionChange = updateConnectionUI;
    comms.onMessage = handleIncomingMessage;

    startHeaderTimers();
    startLEDSimulator();

    // ── Auth gate: la conexión al dispositivo espera identidad Firebase ──
    if (auth) {
        auth.onAuthStateChanged(async (user) => {
            if (user) {
                await iniciarSesionApp(user);
            } else {
                mostrarPantallaLogin();
            }
        });
    } else {
        // Firebase no disponible (offline o API key sin configurar)
        document.getElementById('login-overlay')?.classList.add('hidden');
        await iniciarConectarDispositivo();
    }
});

// ==========================================
// AUTH — AUTENTICACIÓN FIREBASE
// ==========================================

function initAuthUI() {
    let modoRegistro = false;

    document.getElementById('btn-login-google')?.addEventListener('click', loginConGoogle);

    document.getElementById('btn-register-toggle')?.addEventListener('click', () => {
        modoRegistro = !modoRegistro;
        const btnEmail  = document.getElementById('btn-login-email');
        const btnToggle = document.getElementById('btn-register-toggle');
        if (btnEmail)  btnEmail.textContent = modoRegistro ? 'Registrarse' : 'Iniciar sesión';
        if (btnToggle) btnToggle.innerHTML  = modoRegistro
            ? '¿Ya tenés cuenta? <span class="underline">Iniciar sesión</span>'
            : '¿No tenés cuenta? <span class="underline">Registrarse</span>';
        document.getElementById('login-error')?.classList.add('hidden');
    });

    document.getElementById('btn-login-email')?.addEventListener('click', () => {
        const email = document.getElementById('login-email')?.value.trim();
        const pass  = document.getElementById('login-password')?.value;
        if (!email || !pass) { mostrarErrorLogin('Completá todos los campos.'); return; }
        loginConEmail(email, pass, modoRegistro);
    });

    document.getElementById('login-password')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') document.getElementById('btn-login-email')?.click();
    });

    document.getElementById('btn-logout')?.addEventListener('click', cerrarSesion);
    document.getElementById('btn-logout-settings')?.addEventListener('click', cerrarSesion);
}

async function iniciarSesionApp(user) {
    currentUser = user;
    console.log(`[AUTH] Sesión iniciada: ${user.displayName || user.email}`);

    // Ocultar pantalla de login
    document.getElementById('login-overlay')?.classList.add('hidden');

    // Mostrar nombre de usuario en el header
    const displayEl = document.getElementById('user-display-name');
    if (displayEl) {
        const nombre = user.displayName
            ? user.displayName.split(' ')[0]
            : (user.email ? user.email.split('@')[0] : 'Usuario');
        displayEl.textContent = nombre;
        displayEl.classList.remove('hidden');
    }
    document.getElementById('btn-logout')?.classList.remove('hidden');

    // Tarjeta Cuenta en la pestaña Ajustes
    const settingsName = document.getElementById('settings-user-name');
    if (settingsName) settingsName.textContent = user.displayName || user.email || 'Usuario';
    document.getElementById('card-cuenta')?.classList.remove('hidden');

    // Cargar ubicación de clima y presets del usuario desde Firestore
    if (typeof weatherService !== 'undefined' && db) {
        db.collection('usuarios').doc(user.uid).get().then(doc => {
            if (doc.exists && doc.data().ubicacion_clima) {
                const u = doc.data().ubicacion_clima;
                weatherService.location = { lat: parseFloat(u.lat), lon: parseFloat(u.lon), name: u.name };
                weatherService.fetchWeather(true);
            }
        }).catch(err => console.log("[AUTH] Error leyendo ubicacion:", err));
    }

    if (typeof presetsService !== 'undefined') {
        presetsService.cargarPresetsFirestore();
    }

    await iniciarConectarDispositivo();
}

async function iniciarConectarDispositivo() {
    // /usuarios en Firestore es siempre la fuente de verdad.
    // Se consulta siempre, incluso si hay datos en localStorage,
    // para evitar que sesiones obsoletas apunten al dispositivo equivocado.
    if (db && currentUser) {
        try {
            const userDoc = await db.collection("usuarios").doc(currentUser.uid).get();
            if (userDoc.exists && userDoc.data().chipId && userDoc.data().token) {
                const fsChipId = userDoc.data().chipId;
                const fsToken  = userDoc.data().token;

                if (state.chipId && state.chipId !== fsChipId) {
                    console.warn(`[AUTH] localStorage tiene "${state.chipId}" pero Firestore dice "${fsChipId}". Usando Firestore.`);
                }

                // Firestore gana siempre
                state.chipId = fsChipId;
                state.token  = fsToken;
                localStorage.setItem('CHIP_ID', fsChipId);
                localStorage.setItem('TOKEN',   fsToken);
                console.log(`[AUTH] Dispositivo verificado desde cuenta: ${fsChipId}`);

            } else if (!state.chipId) {
                console.log('[AUTH] No hay dispositivo registrado para esta cuenta. Ir a Ajustes.');
            }
        } catch (err) {
            console.warn('[AUTH] No se pudo leer /usuarios (sin conexión?):', err.message);
            // Si hay datos en localStorage, los usamos como fallback offline
        }
    }

    if (state.chipId && state.token) {
        await comms.initConnection(state.chipId, state.token);
        escucharConfiguracionFirestore(state.chipId);
        setTimeout(() => {
            sendCmd({comando: "GET_CONFIG"});
            sendCmd({comando: "GET_STATE"});
            sendCmd({comando: "GET_TEMP"});
        }, 1000);

        // Heartbeat y reconexión automática en background cada 20 segundos
        if (!window._appHeartbeatInterval) {
            window._appHeartbeatInterval = setInterval(async () => {
                if (!document.hidden && state.chipId && state.token) {
                    if (!comms.isConnected()) {
                        console.log("[HEARTBEAT] Conexión caída. Reintentando conectar...");
                        const ok = await comms.initConnection(state.chipId, state.token);
                        if (ok) {
                            sendCmd({comando: "GET_CONFIG"});
                            sendCmd({comando: "GET_STATE"});
                            sendCmd({comando: "GET_TEMP"});
                        }
                    } else {
                        // Refrescar estado y temperatura periódicamente (solicita GET_STATE)
                        sendCmd({comando: "GET_STATE"});
                    }
                }
            }, 20000);
        }
    } else {
        // Sin dispositivo vinculado → ir a Ajustes para configurar por BLE
        document.querySelector('.nav-btn[data-target="view-settings"]')?.click();
    }
}


function mostrarPantallaLogin(error) {
    document.getElementById('login-overlay')?.classList.remove('hidden');
    if (error) mostrarErrorLogin(error);
}

function mostrarErrorLogin(msg) {
    const errEl = document.getElementById('login-error');
    if (!errEl) return;
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
}

async function loginConGoogle() {
    if (!auth) return;
    const provider = new firebase.auth.GoogleAuthProvider();
    try {
        document.getElementById('login-error')?.classList.add('hidden');
        await auth.signInWithPopup(provider);
        // onAuthStateChanged maneja el resto automáticamente
    } catch (err) {
        console.error('[AUTH] Error Google Sign-In:', err);
        mostrarErrorLogin(traducirErrorAuth(err.code));
    }
}

async function loginConEmail(email, password, isRegister) {
    if (!auth) return;
    try {
        document.getElementById('login-error')?.classList.add('hidden');
        if (isRegister) {
            await auth.createUserWithEmailAndPassword(email, password);
        } else {
            await auth.signInWithEmailAndPassword(email, password);
        }
        // onAuthStateChanged maneja el resto automáticamente
    } catch (err) {
        console.error('[AUTH] Error Email Sign-In:', err);
        mostrarErrorLogin(traducirErrorAuth(err.code));
    }
}

function cerrarSesion() {
    showGenericModal({
        title: "Cerrar Sesión",
        msg: "¿Estás seguro de que deseas cerrar sesión?",
        onOk: async () => {
            if (!auth) return;
            try {
                if (comms && typeof comms.disconnect === 'function') comms.disconnect();
                await auth.signOut();
                currentUser = null;
                document.getElementById('btn-logout')?.classList.add('hidden');
                const displayEl = document.getElementById('user-display-name');
                if (displayEl) { displayEl.textContent = ''; displayEl.classList.add('hidden'); }
                // onAuthStateChanged dispara mostrarPantallaLogin automáticamente
            } catch (err) {
                console.error('[AUTH] Error al cerrar sesión:', err);
            }
        }
    });
}

function traducirErrorAuth(code) {
    const errores = {
        'auth/user-not-found':         'No existe una cuenta con ese email.',
        'auth/wrong-password':         'Contraseña incorrecta.',
        'auth/invalid-email':          'El formato del email no es válido.',
        'auth/email-already-in-use':   'Ese email ya está registrado. Intentá iniciar sesión.',
        'auth/weak-password':          'La contraseña debe tener al menos 6 caracteres.',
        'auth/popup-closed-by-user':   'Se cerró la ventana de Google antes de completar.',
        'auth/network-request-failed': 'Sin conexión a internet.',
        'auth/too-many-requests':      'Demasiados intentos fallidos. Intentá más tarde.',
        'auth/invalid-credential':     'Email o contraseña incorrectos.',
    };
    return errores[code] || `Error de autenticación: ${code}`;
}

// ==========================================
// INTERFAZ GENERAL (TABS & HEADER)
// ==========================================
function initTabs() {
    const navBtns = document.querySelectorAll('.nav-btn');

    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const target = btn.getAttribute('data-target');
            
            // Obtener la vista actual activa
            const activeView = document.querySelector('.tab-view:not(.hidden)');
            const activeTarget = activeView ? activeView.id : null;
            
            if (activeTarget === target) return;
            
            if (state.hasUnsavedChanges) {
                e.preventDefault();
                e.stopPropagation();
                
                showGenericModal({
                    title: "Cambios sin guardar",
                    msg: "Tienes cambios sin guardar. ¿Deseas descartarlos?",
                    onOk: () => {
                        state.hasUnsavedChanges = false;
                        cambiarSeccionApp(btn, target);
                    }
                });
            } else {
                cambiarSeccionApp(btn, target);
            }
        });
    });
}

function cambiarSeccionApp(btn, target) {
    const navBtns = document.querySelectorAll('.nav-btn');
    const views = document.querySelectorAll('.tab-view');

    // Actualizar botones nav
    navBtns.forEach(b => {
        b.classList.remove('text-teal-600', 'dark:text-teal-400');
        b.classList.add('text-slate-400', 'dark:text-slate-500');
    });
    btn.classList.add('text-teal-600', 'dark:text-teal-400');
    btn.classList.remove('text-slate-400', 'dark:text-slate-500');

    // Mostrar vista
    views.forEach(v => {
        if (v.id === target) {
            v.classList.remove('hidden');
        } else {
            v.classList.add('hidden');
        }
    });

    if (target === 'view-dashboard') {
        if (typeof weatherService !== 'undefined') {
            weatherService.evaluarSugerenciaRetrasoLluvia();
        }
    }

    if (target === 'view-scheduler') {
        if (typeof weeklyCalendarService !== 'undefined') {
            weeklyCalendarService.render();
        }
    }

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

function startHeaderTimers() {
    const timeEl = document.getElementById('header-time');
    const infoContainer = document.getElementById('header-info');
    
    const updateTime = () => {
        const now = new Date();
        const hh = now.getHours().toString().padStart(2, '0');
        const mm = now.getMinutes().toString().padStart(2, '0');
        if (timeEl) timeEl.textContent = `${hh}:${mm}`;
        if (infoContainer) infoContainer.classList.remove('hidden');
    };
    
    updateTime();
    // Actualizar hora cada minuto
    setInterval(updateTime, 60000);
    
    // Pedir temperatura cada 15 min si hay conexión
    setInterval(() => {
        if (state.token && (comms.mode === 'MQTT' || comms.mode === 'BLE')) {
            comms.sendCommand({comando: "GET_TEMP"}, state.token);
        }
    }, 15 * 60 * 1000);
}

function updateConnectionUI(status) {
    const iconDiv = document.getElementById('conn-icon');
    const statusText = document.getElementById('conn-status');
    const btnReconnect = document.getElementById('btn-reconnect');
    
    // Si la conexión es MQTT o BLE y tenemos la config, mostrar el SSID
    let netName = "Nube";
    if (state.deviceConfig && state.deviceConfig.ssid && state.deviceConfig.ssid !== "Desconocido") {
        netName = state.deviceConfig.ssid;
    }

    if (status === 'MQTT') {
        statusText.textContent = `(${netName})`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-green-900/40 text-green-400 rounded-full truncate max-w-[130px]";
        iconDiv.innerHTML = '<i data-lucide="wifi" class="w-5 h-5 text-green-400"></i>';
        btnReconnect.classList.add('hidden');
    } else if (status === 'BLE') {
        statusText.textContent = `(Bluetooth)`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-blue-900/40 text-blue-400 rounded-full truncate max-w-[130px]";
        iconDiv.innerHTML = '<i data-lucide="bluetooth" class="w-5 h-5 text-blue-400"></i>';
        btnReconnect.classList.add('hidden');
    } else if (status === 'CONNECTING_MQTT' || status === 'CONNECTING_BLE') {
        iconDiv.innerHTML = '<i data-lucide="loader-2" class="w-5 h-5 text-yellow-400 animate-spin"></i>';
        statusText.textContent = "Conectando...";
        statusText.className = "text-[10px] font-medium px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 truncate max-w-[130px]";
        btnReconnect.classList.add('hidden');
    } else {
        statusText.textContent = `Desconectado`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-red-900/40 text-red-400 rounded-full truncate max-w-[130px]";
        iconDiv.innerHTML = '<i data-lucide="wifi-off" class="w-5 h-5 text-red-400"></i>';
        btnReconnect.classList.remove('hidden');
    }
    lucide.createIcons();
}

document.getElementById('btn-reconnect').addEventListener('click', () => {
    if (state.chipId && state.token) {
        comms.initConnection(state.chipId, state.token);
        escucharConfiguracionFirestore(state.chipId);
    } else {
        document.querySelector('.nav-btn[data-target="view-settings"]').click();
    }
});

// ==========================================
// RECEPCIÓN DE MENSAJES Y TELEMETRIA
// ==========================================
function handleIncomingMessage(msg) {
    // Limpiar flag de comando pendiente. El feedback al usuario viene de los
    // handlers específicos (ACK_CFG, cambio de estado en TELEMETRIA, etc.)
    // No mostramos un toast genérico aquí porque podría corresponder a
    // una respuesta de un comando anterior, no del último enviado.
    if (pendingCommand) {
        pendingCommand = false;
    }

    const dbg = document.getElementById('debug-banner');
    if (dbg) {
        dbg.textContent = "DEBUG: Mensaje recibido tipo " + (msg ? msg.tipo : "undefined") + " a las " + new Date().toLocaleTimeString();
        dbg.classList.remove("bg-orange-600");
        dbg.classList.add("bg-green-600");
    }

    if (!msg || !msg.tipo) return;

    if (msg.tipo === "CONFIG") {
        const nombresZonasLocales = state.deviceConfig.nombres_zonas;
        // Guardar datos del estado local (Firestore) antes de que el merge los sobreescriba
        const versionLocal = state.deviceConfig.config_version || 0;
        const programasLocales = state.deviceConfig.programas;
        const ajustesLocales = state.deviceConfig.ajustes_estacionales;

        state.deviceConfig = { ...state.deviceConfig, ...msg.data };
        state.deviceConfig.programas = normalizeProgramasData(state.deviceConfig.programas);
        
        // Preservar siempre los nombres de zonas locales/Firestore (SSOT) ya que el ESP32 no los almacena
        if (nombresZonasLocales && Object.keys(nombresZonasLocales).length > 0) {
            state.deviceConfig.nombres_zonas = nombresZonasLocales;
        }

        // Auto-sync: si la app/Firestore tienen config más nueva que el ESP32,
        // empujar automáticamente. Ocurre tras INIT_TOKEN o factory reset donde
        // el ESP32 arranca con config_version=1 pero Firestore tiene la versión del usuario.
        const versionESP32 = msg.data.config_version || 0;
        if (versionLocal > versionESP32 && state.token && (comms.mode === 'BLE' || comms.mode === 'MQTT')) {
            console.log(`[SYNC] Auto-sync: App/Firestore v${versionLocal} > ESP32 v${versionESP32}. Empujando config...`);
            showToast('☁️ Sincronizando config con la placa...');
            // Restaurar datos de la nube (que son los del usuario) en el state
            if (programasLocales && Object.keys(programasLocales).length > 0) {
                state.deviceConfig.programas = normalizeProgramasData(programasLocales);
            }
            if (ajustesLocales && Array.isArray(ajustesLocales) && ajustesLocales.length > 0) {
                state.deviceConfig.ajustes_estacionales = ajustesLocales;
            }
            // Delay para no solaparse con GET_STATE de la secuencia de arranque
            setTimeout(() => {
                sendCmd({
                    comando: "UPDATE_CONFIG",
                    config: {
                        max_zonas: state.deviceConfig.max_zonas,
                        modo_bomba: state.deviceConfig.modo_bomba,
                        sensor_lluvia_delay_horas: state.deviceConfig.sensor_lluvia_delay_horas || 0,
                        programas: state.deviceConfig.programas,
                        ajustes_estacionales: state.deviceConfig.ajustes_estacionales
                    }
                });
            }, 1500);
        }

        refreshUIFromConfig();
        if (window._startupSeq === 1) {
            window._startupSeq = 2;
            setTimeout(() => sendCmd({comando: "GET_STATE"}), 200);
        }
    } else if (msg.tipo === "TELEMETRIA") {
        const estadoAnterior = state.telemetria ? state.telemetria.estado : "";
        state.telemetria = msg.data;
        if (msg.data) {
            if (msg.data.timestamp_rain_delay !== undefined) {
                state.deviceConfig.timestamp_rain_delay = msg.data.timestamp_rain_delay;
            }
            if (msg.data.timestamp_sensor_lluvia_clear !== undefined) {
                state.deviceConfig.timestamp_sensor_lluvia_clear = msg.data.timestamp_sensor_lluvia_clear;
            }
            if (msg.data.temp !== undefined) {
                const tempEl = document.getElementById('header-temp');
                if (tempEl) tempEl.textContent = (msg.data.temp !== "N/A") ? `${msg.data.temp}°C` : "N/A";
            }
            
            // Auto-solicitud de configuración en la transición a secado para asegurar timestamps
            if (msg.data.estado === "PAUSA: SECADO" && estadoAnterior !== "PAUSA: SECADO") {
                setTimeout(() => sendCmd({comando: "GET_CONFIG"}), 150);
            }
        }
        updateActiveWidget(msg.data);
    } else if (msg.tipo === "CONFIG_PROG") {
        // Fix #1: Recibir programas fragmentados desde BLE (un mensaje por programa).
        // El ESP32 envía CONFIG_PROG con prog_id + data cuando el origen es BLE,
        // para no superar el límite de 500 bytes por mensaje del canal BLE.
        if (!state.deviceConfig.programas) state.deviceConfig.programas = {};
        if (msg.prog_id !== undefined && msg.data) {
            state.deviceConfig.programas[msg.prog_id] = msg.data;
        } else if (msg.data && typeof msg.data === 'object') {
            Object.assign(state.deviceConfig.programas, msg.data);
        }
        state.deviceConfig.programas = normalizeProgramasData(state.deviceConfig.programas);
        refreshUIFromConfig();

    } else if (msg.tipo === "LOGS") {
        // Formato legado (BLE compacto o CLEAR_HISTORY): renderizar directo
        renderLogs(msg.data || []);
    } else if (msg.tipo === "LOG_ENTRY") {
        // Streaming MQTT: acumular entradas hasta recibir LOGS_END
        if (msg.data) _pendingLogEntries.push(msg.data);
    } else if (msg.tipo === "LOGS_END") {
        // Fin del streaming: renderizar todo lo acumulado
        if (msg.data && Array.isArray(msg.data)) {
            // BLE: viene con data directamente en LOGS_END
            renderLogs(msg.data);
        } else {
            // MQTT: renderizar buffer acumulado y limpiarlo
            renderLogs(_pendingLogEntries);
            _pendingLogEntries = [];
        }
    } else if (msg.tipo === "TEMP") {
        const tempEl = document.getElementById('header-temp');
        if (tempEl) {
            tempEl.textContent = (msg.data !== "N/A" && msg.data !== undefined) ? `${msg.data}°C` : "N/A";
        }
    } else if (msg.tipo === "ACK_CFG") {
        // El ESP32 aceptó la configuración empujada por el auto-sync.
        console.log(`[SYNC] ESP32 confirmó config v${msg.v}`);
        showToast(`✓ Placa sincronizada con la nube (v${msg.v})`);
        sendCmd({ comando: "GET_TEMP" });

    } else if (msg.tipo === "NEED_INIT") {
        // El dispositivo no tiene token configurado (nuevo o post factory-reset).
        // Ocurre cuando la app tiene token en localStorage pero la placa fue reseteada.
        // Se muestra el modal de auth para que el usuario establezca la contraseña.
        console.warn("[APP] Dispositivo requiere inicialización (NEED_INIT).");
        showToast("Dispositivo sin configurar — establecé una contraseña.");
        // Limpiar el token local obsoleto para forzar la inicialización limpia
        state.token = null;
        localStorage.removeItem('TOKEN');
        showModalAuth();

    } else if (msg.tipo === "AUTH_ERROR") {
        comms.disconnect();
        showGenericModal({
            title: "Error de Seguridad",
            msg: "El token de la app no coincide con el de la placa. Se ha borrado el token de la app.",
            hideCancel: true,
            onOk: () => {
                localStorage.clear();
                location.reload();
            }
        });
    }
}

// Variables globales para el temporizador local
let countdownTimer = null;
let currentRemainingSecs = 0;
let currentTotalSecs = 1;

function updateRainDelayBanner() {
    const txtRain = document.getElementById('rain-delay-status');
    if (!txtRain) return;
    
    let delayActive = false;
    let delayUnix = 0;
    let labelPrefix = "";
    const nowSecs = Math.floor(Date.now() / 1000);
    
    // Obtener timestamps de config o de la telemetría más reciente
    const tsRainDelay = state.deviceConfig.timestamp_rain_delay || (state.telemetria && state.telemetria.timestamp_rain_delay) || 0;
    const tsSensorDelay = state.deviceConfig.timestamp_sensor_lluvia_clear || (state.telemetria && state.telemetria.timestamp_sensor_lluvia_clear) || 0;
    
    // Priorizar el retraso manual
    if (tsRainDelay > 0) {
        const manualDelayUnix = tsRainDelay + 946684800; // Y2K a Unix
        if (manualDelayUnix > nowSecs) {
            delayActive = true;
            delayUnix = manualDelayUnix;
            labelPrefix = "Retraso manual activo hasta: ";
        }
    }
    
    // Si no hay manual, verificar el del sensor de lluvia
    if (!delayActive && tsSensorDelay > 0) {
        const sensorDelayUnix = tsSensorDelay + 946684800; // Y2K a Unix
        if (sensorDelayUnix > nowSecs) {
            delayActive = true;
            delayUnix = sensorDelayUnix;
            labelPrefix = "Secado de sensor activo hasta: ";
        }
    }
    
    if (delayActive && delayUnix > 0) {
        const futureDate = new Date(delayUnix * 1000);
        const mm = String(futureDate.getMonth()+1).padStart(2,'0');
        const dd = String(futureDate.getDate()).padStart(2,'0');
        const hh = String(futureDate.getHours()).padStart(2,'0');
        const mn = String(futureDate.getMinutes()).padStart(2,'0');
        txtRain.querySelector('span').innerHTML = `${labelPrefix}<strong>${dd}/${mm} a las ${hh}:${mn}h</strong>`;
        txtRain.classList.remove('hidden');
    } else {
        txtRain.classList.add('hidden');
    }

    // Actualizar badge de estado del sensor de lluvia en la tarjeta del Panel
    const rainBadge = document.getElementById('rain-sensor-status-badge');
    if (rainBadge) {
        const isSensorActive = state.deviceConfig.sensor_lluvia_activo !== false;
        const sensorType = (state.deviceConfig.sensor_lluvia_tipo || "NA").toUpperCase();
        const est = state.telemetria ? state.telemetria.estado : "";

        if (!isSensorActive) {
            rainBadge.textContent = "Sensor: Desactivado";
            rainBadge.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-600";
        } else if (est === "PAUSA: SENSOR") {
            rainBadge.textContent = `🌧️ Lluvia (${sensorType})`;
            rainBadge.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-sky-100 dark:bg-sky-900/60 text-sky-700 dark:text-sky-300 border border-sky-300 dark:border-sky-700 animate-pulse";
        } else if (est === "PAUSA: SECADO") {
            rainBadge.textContent = "⏳ Secando";
            rainBadge.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-700";
        } else {
            rainBadge.textContent = `Sensor: Activo (${sensorType})`;
            rainBadge.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800";
        }
    }
}

function updateActiveWidget(data) {
    const badge = document.getElementById('status-badge');
    const infoPanel = document.getElementById('active-watering-info');
    const timeRemaining = document.getElementById('time-remaining');
    const zoneName = document.getElementById('active-zone-name');
    const circle = document.getElementById('progress-circle');
    
    // Limpiar temporizador previo
    if (countdownTimer) {
        clearInterval(countdownTimer);
        countdownTimer = null;
    }

    if (!data.estado || data.estado === "IDLE" || data.estado.startsWith("PAUSA")) {
        const isPausa = data.estado && data.estado.startsWith("PAUSA");
        badge.textContent = isPausa ? data.estado : "EN REPOSO";
        badge.className = isPausa ? "px-2.5 py-1 rounded-full text-xs font-bold bg-yellow-900 text-yellow-300" : "px-2.5 py-1 rounded-full text-xs font-bold bg-slate-700 text-slate-300";
        infoPanel.classList.add('hidden');
        document.getElementById('next-watering-info').classList.remove('hidden');
        calculateNextWatering();
        document.getElementById('manual-active-stop-container')?.classList.add('hidden');
        
        // Actualizar el icono de estado de reposo / lluvia
        const iconContainer = document.getElementById('status-icon-container');
        if (iconContainer) {
            if (isPausa) {
                iconContainer.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-8 h-8 text-sky-500 dark:text-sky-400 mb-2">
                        <!-- Nube estática -->
                        <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"></path>
                        <!-- Gotas de lluvia animadas -->
                        <line class="rain-drop drop-1" x1="16" y1="14" x2="14" y2="20"></line>
                        <line class="rain-drop drop-2" x1="12" y1="16" x2="10" y2="22"></line>
                        <line class="rain-drop drop-3" x1="8" y1="14" x2="6" y2="20"></line>
                    </svg>
                `;
            } else {
                iconContainer.innerHTML = '<i data-lucide="calendar-clock" class="w-8 h-8 text-slate-400 dark:text-slate-600 mb-2"></i>';
            }
            if (window.lucide) window.lucide.createIcons();
        }
        updateRainDelayBanner();
    } else if (data.estado === "FALLO_CORRIENTE") {
        badge.textContent = "FALLO HARDWARE";
        badge.className = "px-2.5 py-1 rounded-full text-xs font-bold bg-red-900 text-red-300 animate-pulse";
        infoPanel.classList.add('hidden');
        document.getElementById('next-watering-info').classList.add('hidden');
        document.getElementById('manual-active-stop-container')?.classList.add('hidden');
    } else {
        let label = data.estado;
        if (data.estado === "REGANDO" && data.ciclo_actual && data.ciclos_totales) {
            label = `REGANDO (Ciclo ${data.ciclo_actual}/${data.ciclos_totales})`;
        } else if (data.estado === "REMOJANDO" && data.remojo_actual && data.remojos_totales) {
            label = `REMOJANDO (Remojo ${data.remojo_actual}/${data.remojos_totales})`;
        }
        badge.textContent = label;
        badge.className = "px-2.5 py-1 rounded-full text-xs font-bold bg-teal-500/20 text-teal-400 border border-teal-500/30";
        infoPanel.classList.remove('hidden');
        document.getElementById('next-watering-info').classList.add('hidden');
        
        const stopContainer = document.getElementById('manual-active-stop-container');
        if (stopContainer) {
            stopContainer.classList.remove('hidden');
            if (window.lucide) window.lucide.createIcons();
        }
        
        let zname = data.zona ? obtenerNombreZona(data.zona) : "Preparando...";
        zoneName.textContent = zname;

        document.getElementById('info-tiempo-prog').textContent = (data.t_prog || 0) + " min";
        document.getElementById('info-ajuste').textContent = (data.ajuste || 100) + "%";
        document.getElementById('info-ciclo').textContent = (data.ciclo || 0) + " min";
        document.getElementById('info-remojo').textContent = (data.remojo || 0) + " min";

        // Actualizar animación de fuente de agua (izquierda) y válvula de zona (derecha)
        const sourceContainer = document.getElementById('pump-or-main-status');
        const valveContainer = document.getElementById('zone-valve-status');
        if (sourceContainer && valveContainer) {
            const modoBomba = state.deviceConfig && state.deviceConfig.modo_bomba !== false; // por defecto true
            
            // Actualizar etiqueta dinámica
            const labelSource = document.getElementById('pump-or-main-label');
            if (labelSource) {
                labelSource.textContent = modoBomba ? "Bomba" : "Master valv.";
            }

            // 1. Fuente de agua (Izquierda)
            if (modoBomba) {
                // Bomba: Hélice (fan)
                const isPumpOn = (data.estado === "REGANDO" || data.estado === "PRESURIZANDO");
                if (isPumpOn) {
                    sourceContainer.innerHTML = `<i data-lucide="fan" class="w-8 h-8 stroke-[2.5] text-teal-600 dark:text-teal-400 animate-spin-fast"></i>`;
                } else {
                    sourceContainer.innerHTML = `<i data-lucide="fan" class="w-8 h-8 stroke-[2] text-slate-400 opacity-40"></i>`;
                }
            } else {
                // Master Válvula: Válvula de Red (faucet)
                const isFlowing = (data.estado === "REGANDO" || data.estado === "PRESURIZANDO");
                if (isFlowing) {
                    sourceContainer.innerHTML = `<i data-lucide="faucet" class="w-8 h-8 stroke-[2.5] text-teal-600 dark:text-teal-400"></i>`;
                } else {
                    sourceContainer.innerHTML = `<i data-lucide="faucet" class="w-8 h-8 stroke-[2] text-slate-400 opacity-40"></i>`;
                }
            }
            
            // 2. Válvula de Zona (Derecha)
            const isZoneWatering = (data.estado === "REGANDO");
            if (isZoneWatering) {
                valveContainer.innerHTML = `
                    <div class="relative flex items-center justify-center w-full h-full">
                        <i data-lucide="droplet" class="w-7 h-7 text-teal-600 dark:text-teal-400 fill-teal-500/20 anim-dripping-droplet"></i>
                        <span class="absolute w-1.5 h-1.5 bg-teal-500 dark:bg-teal-400 rounded-full anim-dripping-drop bottom-1.5"></span>
                    </div>
                `;
            } else {
                valveContainer.innerHTML = `
                    <div class="relative flex items-center justify-center w-full h-full opacity-40">
                        <i data-lucide="droplet" class="w-7 h-7 text-slate-400"></i>
                    </div>
                `;
            }
            
            if (window.lucide) {
                window.lucide.createIcons();
            }
        }
        
        currentRemainingSecs = data.tiempo_restante || 0; 
        currentTotalSecs = data.tiempo_total || 1;
        
        if (data.estado === "REMOJANDO") {
            circle.classList.remove('text-teal-500');
            circle.classList.add('text-blue-500');
        } else {
            circle.classList.add('text-teal-500');
            circle.classList.remove('text-blue-500');
        }
        
        renderTimeTick(timeRemaining, circle);
        
        // Iniciar reloj local
        countdownTimer = setInterval(() => {
            currentRemainingSecs--;
            if (currentRemainingSecs <= 0) {
                currentRemainingSecs = 0;
                clearInterval(countdownTimer);
            }
            renderTimeTick(timeRemaining, circle);
        }, 1000);
    }
}

function renderTimeTick(timeEl, circleEl) {
    let mins = Math.floor(currentRemainingSecs / 60);
    let secs = currentRemainingSecs % 60;
    timeEl.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    
    let percent = Math.max(0, Math.min(100, (currentRemainingSecs / currentTotalSecs) * 100));
    circleEl.style.strokeDasharray = `${percent}, 100`;
}

function calculateNextWatering() {
    if (!state.deviceConfig || !state.deviceConfig.programas) return;
    const now = new Date();
    // dia de la semana (1 Lunes ... 7 Domingo)
    let currentDay = now.getDay();
    if (currentDay === 0) currentDay = 7;
    
    let minDiff = Infinity;
    let nextProgStr = "No hay riegos programados";
    let nextProgProg = "";
    let nextProgDetails = "";
    let nextProgId = null;

    for (const [pKey, pObj] of Object.entries(state.deviceConfig.programas)) {
        if (!pObj.activo) continue;
        if (!pObj.dias_semana || pObj.dias_semana.length === 0) continue;
        if (!pObj.horas_arranque || pObj.horas_arranque.length === 0) continue;
        
        // check if any zone has minutes
        let hasZones = false;
        if (pObj.zonas) {
            for (const z of Object.values(pObj.zonas)) {
                if (z.minutos > 0) hasZones = true;
            }
        }
        if (!hasZones) continue;

        for (const hora_inicio of pObj.horas_arranque) {
            const [hh, mm] = hora_inicio.split(':').map(Number);
            
            for (const d of pObj.dias_semana) {
                let daysUntil = d - currentDay;
                if (daysUntil < 0) daysUntil += 7;
                
                let dateTarget = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                dateTarget.setDate(dateTarget.getDate() + daysUntil);
                dateTarget.setHours(hh, mm, 0, 0);
                
                let diffMs = dateTarget.getTime() - now.getTime();
                if (diffMs <= 0) {
                    // If it already passed today, it will be next week
                    dateTarget.setDate(dateTarget.getDate() + 7);
                    diffMs = dateTarget.getTime() - now.getTime();
                }
                
                if (diffMs < minDiff) {
                    minDiff = diffMs;
                    nextProgId = pKey;
                    const dNames = ["", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
                    
                    let tmr = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                    tmr.setDate(tmr.getDate() + 1);
                    
                    const pName = pObj.nombre || "Programa";

                    if (dateTarget.toDateString() === now.toDateString()) {
                        nextProgStr = `Hoy a las ${hora_inicio}h`;
                    } else if (dateTarget.toDateString() === tmr.toDateString()) {
                        nextProgStr = `Mañana a las ${hora_inicio}h`;
                    } else {
                        // dateTarget.getDay() is 0 for Sun, 1 for Mon. We want our 1..7 index.
                        let dayIdx = dateTarget.getDay();
                        if (dayIdx === 0) dayIdx = 7;
                        nextProgStr = `${dNames[dayIdx]} a las ${hora_inicio}h`;
                    }
                    nextProgProg = pName;

                    // Calcular el desglose de zonas con ajuste estacional del mes actual
                    let zonesDetail = [];
                    // Misma lógica que ejecutar_riego() en riego_core.py:
                    // comparar "MM-DD" actual contra rangos {inicio, fin, porcentaje}
                    // con soporte de rangos que cruzan año (ej: 12-21 a 03-20).
                    let factorEst = 1.0;
                    let pctEst = 100;
                    const temporadasEst = state.deviceConfig.ajustes_estacionales || [];
                    if (temporadasEst.length > 0) {
                        const hoy = new Date();
                        const mm = String(hoy.getMonth() + 1).padStart(2, '0');
                        const dd = String(hoy.getDate()).padStart(2, '0');
                        const mmdd = `${mm}-${dd}`;
                        for (const temp of temporadasEst) {
                            const ini = temp.inicio || '01-01';
                            const fin = temp.fin   || '12-31';
                            let enRango = false;
                            if (ini <= fin) {
                                enRango = (mmdd >= ini && mmdd <= fin);
                            } else {
                                // Rango que cruza año (ej: 12-21 → 03-20)
                                enRango = (mmdd >= ini || mmdd <= fin);
                            }
                            if (enRango) {
                                pctEst = temp.porcentaje || 100;
                                factorEst = pctEst / 100;
                                break;
                            }
                        }
                    }

                    if (pObj.zonas) {
                        const zKeys = Object.keys(pObj.zonas).sort((a,b) => {
                            const numA = parseInt(a.replace(/\D/g, '')) || 0;
                            const numB = parseInt(b.replace(/\D/g, '')) || 0;
                            return numA - numB;
                        });
                        for (const zKey of zKeys) {
                            const z = pObj.zonas[zKey];
                            if (z.minutos > 0) {
                                const zName = obtenerNombreZona(zKey);
                                const minReales = Math.max(1, Math.round(z.minutos * factorEst));
                                let detail = `${zName} (${minReales} min`;
                                if (pctEst !== 100) detail += ` ·${pctEst}%`;
                                if (z.cycle_min && z.cycle_min > 0 && z.cycle_min < z.minutos) {
                                    const cycleReales = Math.max(1, Math.round(z.cycle_min * factorEst));
                                    detail += ` · Ciclos: ${cycleReales}/${z.soak_min || 0} min`;
                                }
                                detail += `)`;
                                zonesDetail.push(detail);
                            }
                        }
                    }
                    nextProgDetails = zonesDetail.join(" | ");

                }
            }
        }
    }
    const el = document.getElementById('next-watering-time');
    if (el) el.textContent = nextProgStr;

    const progEl = document.getElementById('next-watering-prog');
    if (progEl) progEl.textContent = nextProgProg;

    const detailsEl = document.getElementById('next-watering-details');
    if (detailsEl) detailsEl.textContent = nextProgDetails;
    
    const btnStart = document.getElementById('btn-next-start');
    if (btnStart) {
        if (nextProgId) {
            btnStart.dataset.progId = nextProgId;
            btnStart.classList.remove('hidden');
        } else {
            btnStart.classList.add('hidden');
        }
    }
}

function actualizarVisualizacionPestanasProgramas() {
    document.querySelectorAll('.prog-tab').forEach(tab => {
        const progId = tab.dataset.prog;
        const prog = (state.deviceConfig.programas || {})[progId];
        const isActive = prog && prog.activo === true;
        
        let dot = tab.querySelector('.prog-active-dot');
        if (!dot) {
            dot = document.createElement('span');
            dot.className = "prog-active-dot w-2 h-2 rounded-full inline-block ml-2 transition-all duration-300";
            tab.appendChild(dot);
        }
        
        if (isActive) {
            dot.classList.remove('bg-rose-500/30', 'border', 'border-rose-500/50', 'bg-slate-500', 'opacity-30');
            dot.classList.add('bg-teal-400', 'shadow-[0_0_8px_rgba(45,212,191,0.8)]');
        } else {
            dot.classList.remove('bg-teal-400', 'shadow-[0_0_8px_rgba(45,212,191,0.8)]');
            dot.classList.add('bg-rose-500/30', 'border', 'border-rose-500/50');
        }
    });
}

function refreshUIFromConfig() {
    // Actualizar SSID en ajustes
    const ssidDisp = document.getElementById('current-ssid-display');
    if (ssidDisp) {
        const netName = (state.deviceConfig && state.deviceConfig.ssid && state.deviceConfig.ssid !== "Desconocido") ? state.deviceConfig.ssid : "No configurada";
        ssidDisp.textContent = netName;
    }

    // 1. Dashboard: Botones Riego Manual
    const maxZ = state.deviceConfig.max_zonas || 4;
    const manualContainer = document.getElementById('manual-zone-selector');
    manualContainer.innerHTML = '';
    
    for (let i = 1; i <= maxZ; i++) {
        const zName = obtenerNombreZona(i);
        const btn = document.createElement('button');
        btn.className = `manual-z-btn py-2 px-1 text-xs font-medium rounded-lg bg-slate-900 border border-slate-700 text-slate-400 hover:text-white transition`;
        btn.textContent = zName;
        btn.dataset.zone = i;
        btn.onclick = () => {
            document.querySelectorAll('.manual-z-btn').forEach(b => {
                b.classList.remove('bg-teal-600', 'text-white', 'border-teal-500');
                b.classList.add('bg-slate-900', 'text-slate-400', 'border-slate-700');
            });
            btn.classList.add('bg-teal-600', 'text-white', 'border-teal-500');
            btn.classList.remove('bg-slate-900', 'text-slate-400', 'border-slate-700');
        };
        manualContainer.appendChild(btn);
    }
    
    // Seleccionar primero
    if (manualContainer.firstChild) manualContainer.firstChild.click();

    // 2. Dashboard: Ajustes Estacionales (Tarjetas)
    const seasonsContainer = document.getElementById('seasons-container');
    if (seasonsContainer) {
        seasonsContainer.innerHTML = '';
        const temporadas = state.deviceConfig.ajustes_estacionales || [];
        
        if (temporadas.length === 0) {
            seasonsContainer.innerHTML = `<div class="text-center text-slate-500 text-xs py-4">No hay temporadas configuradas.</div>`;
        } else {
            temporadas.forEach((temp, idx) => {
                const formattedStart = formatSeasonDate(temp.inicio);
                const formattedEnd = formatSeasonDate(temp.fin);
                const seasonName = temp.nombre || 'Temporada';
                const mapping = seasonIconMap[seasonName] || { icon: "calendar", color: "text-teal-500" };

                const card = document.createElement('div');
                card.className = "bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-3 rounded-xl transition-colors duration-200";
                card.innerHTML = `
                    <div class="flex justify-between items-center mb-2">
                        <div class="flex items-center gap-2">
                            <i data-lucide="${mapping.icon}" class="w-4 h-4 ${mapping.color}"></i>
                            <div>
                                <div class="text-sm font-bold text-slate-800 dark:text-slate-200">${seasonName}</div>
                                <div class="text-xs text-slate-550 dark:text-slate-400">${formattedStart} al ${formattedEnd}</div>
                            </div>
                        </div>
                        <button class="text-slate-400 dark:text-slate-500 hover:text-teal-605 dark:hover:text-teal-400 transition-colors btn-edit-season" data-idx="${idx}">
                            <i data-lucide="edit-3" class="w-4 h-4"></i>
                        </button>
                    </div>
                    <div class="flex justify-between items-center mb-1">
                        <span class="text-xs text-slate-500 dark:text-slate-400">Porcentaje</span>
                        <span id="pct-val-${idx}" class="text-xs font-bold ${temp.porcentaje !== 100 ? 'text-yellow-600 dark:text-yellow-500' : 'text-teal-600 dark:text-teal-400'}">${temp.porcentaje}%</span>
                    </div>
                    <input type="range" class="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500 season-slider" data-idx="${idx}" min="0" max="100" step="10" value="${temp.porcentaje}">
                `;
                seasonsContainer.appendChild(card);
            });
            
            // Listeners para los sliders
            document.querySelectorAll('.season-slider').forEach(slider => {
                slider.addEventListener('input', (e) => {
                    const idx = e.target.dataset.idx;
                    const val = e.target.value;
                    document.getElementById(`pct-val-${idx}`).textContent = val + '%';
                });
                slider.addEventListener('change', (e) => {
                    const idx = parseInt(e.target.dataset.idx);
                    const val = parseInt(e.target.value);
                    const nuevas = JSON.parse(JSON.stringify(temporadas)); // clonar
                    nuevas[idx].porcentaje = val;
                    
                    state.deviceConfig.ajustes_estacionales = nuevas;
                    refreshUIFromConfig();
                    
                    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: nuevas } });
                    pendingCommand = true;
                });
            });
            
            // Listeners para Editar
            document.querySelectorAll('.btn-edit-season').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const idx = parseInt(e.currentTarget.dataset.idx);
                    openEditSeasonModal(idx, temporadas[idx]);
                });
            });
            
            // Re-inicializar iconos porque se re-renderizaron tarjetas
            if (window.lucide) {
                window.lucide.createIcons();
            }
        }
        
        // Highlight active hemisphere
        const btnSur = document.getElementById('btn-hemi-sur');
        const btnNorte = document.getElementById('btn-hemi-norte');
        if (btnSur && btnNorte) {
            btnSur.classList.remove('border-teal-400', 'text-teal-400');
            btnSur.classList.add('border-transparent', 'text-slate-400');
            btnNorte.classList.remove('border-blue-400', 'text-blue-400');
            btnNorte.classList.add('border-transparent', 'text-slate-400');
            
            if (temporadas.length === 4 && temporadas[0] && temporadas[1]) {
                const isSur = temporadas[0].inicio === "12-21" && temporadas[1].inicio === "03-21";
                const isNorte = temporadas[0].inicio === "06-21" && temporadas[1].inicio === "09-21";
                if (isSur) {
                    btnSur.classList.add('border-teal-400', 'text-teal-400');
                    btnSur.classList.remove('border-transparent', 'text-slate-400');
                } else if (isNorte) {
                    btnNorte.classList.add('border-blue-400', 'text-blue-400');
                    btnNorte.classList.remove('border-transparent', 'text-slate-400');
                }
            }
        }
    }
    
    // 2.5 Dashboard: Rain Delay Status (Manual & Sensor Delay)
    updateRainDelayBanner();

    // Cargar horas de retraso del sensor de lluvia en Ajustes
    const rainDelayHoursInput = document.getElementById('settings-rain-delay-hours');
    const rainDelayHoursVal = document.getElementById('rain-delay-hours-val');
    if (rainDelayHoursInput && state.deviceConfig.sensor_lluvia_delay_horas !== undefined) {
        const val = state.deviceConfig.sensor_lluvia_delay_horas;
        rainDelayHoursInput.value = val;
        if (rainDelayHoursVal) {
            rainDelayHoursVal.textContent = `${val} ${val === 1 ? 'hora' : 'horas'}`;
        }
    }

    // 3. Hardware Settings
    document.querySelectorAll('.hw-zones-btn').forEach(b => {
        if(b.dataset.val == state.deviceConfig.max_zonas) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    document.querySelectorAll('.hw-pump-btn').forEach(b => {
        const boolVal = b.dataset.val === "true";
        if (boolVal === state.deviceConfig.modo_bomba) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    const isRainActive = state.deviceConfig.sensor_lluvia_activo !== false;
    document.querySelectorAll('.rain-active-btn').forEach(b => {
        const boolVal = b.dataset.val === "true";
        if (boolVal === isRainActive) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    const rainType = (state.deviceConfig.sensor_lluvia_tipo || "NA").toUpperCase();
    document.querySelectorAll('.rain-type-btn').forEach(b => {
        if (b.dataset.val === rainType) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    const rainTypeContainer = document.getElementById('container-rain-type');
    if (rainTypeContainer) {
        if (isRainActive) {
            rainTypeContainer.classList.remove('opacity-40', 'pointer-events-none');
        } else {
            rainTypeContainer.classList.add('opacity-40', 'pointer-events-none');
        }
    }

    const tempOffsetInput = document.getElementById('settings-temp-offset');
    const tempOffsetVal = document.getElementById('temp-offset-val');
    if (tempOffsetInput && state.deviceConfig.calibracion_temp !== undefined) {
        const val = parseFloat(state.deviceConfig.calibracion_temp) || 0.0;
        tempOffsetInput.value = val;
        if (tempOffsetVal) {
            const sign = val > 0 ? '+' : '';
            tempOffsetVal.textContent = `${sign}${val.toFixed(1)} °C`;
        }
    }

    // 4. Scheduler
    state.deviceConfig.programas = normalizeProgramasData(state.deviceConfig.programas);
    if (!state.hasUnsavedChanges) {
        loadProgramIntoUI(state.activeProgTab);
    }
    
    // 5. Update Connection Header
    if (comms.mode) {
        updateConnectionUI(comms.mode);
    }
    
    const badge = document.getElementById('status-badge');
    if (badge && badge.textContent === "EN REPOSO") {
        calculateNextWatering();
    }
    
    actualizarVisualizacionPestanasProgramas();
    if (typeof weeklyCalendarService !== 'undefined') {
        weeklyCalendarService.render();
    }
}

// ==========================================
// DASHBOARD ACTIONS
// ==========================================
document.getElementById('manual-time-slider').addEventListener('input', (e) => {
    document.getElementById('manual-time-val').textContent = e.target.value + " min";
});

document.getElementById('manual-cycle-check')?.addEventListener('change', (e) => {
    const opts = document.getElementById('manual-cycle-opts');
    if (opts) {
        if (e.target.checked) opts.classList.remove('hidden');
        else opts.classList.add('hidden');
    }
});

// Listener para colapsar/desplegar la tarjeta de Riego Manual
document.getElementById('btn-toggle-manual')?.addEventListener('click', () => {
    const content = document.getElementById('manual-control-content');
    const chevron = document.getElementById('manual-toggle-chevron');
    if (content && chevron) {
        const isHidden = content.classList.contains('hidden');
        if (isHidden) {
            content.classList.remove('hidden');
            chevron.classList.add('rotate-180');
        } else {
            content.classList.add('hidden');
            chevron.classList.remove('rotate-180');
        }
    }
});

document.getElementById('btn-manual-start').addEventListener('click', () => {
    const activeBtn = document.querySelector('.manual-z-btn.bg-teal-600');
    if (!activeBtn) {
        showGenericModal({
            title: "Riego Manual",
            msg: "Seleccione una zona antes de iniciar.",
            hideCancel: true
        });
        return;
    }
    const zona = activeBtn.dataset.zone;
    const mins = parseInt(document.getElementById('manual-time-slider').value);
    
    const zonaObj = { minutos: mins };
    
    const cycleCheck = document.getElementById('manual-cycle-check');
    if (cycleCheck && cycleCheck.checked) {
        const cycleMin = parseInt(document.getElementById('manual-cycle-min').value) || 5;
        const soakMin = parseInt(document.getElementById('manual-soak-min').value) || 10;
        
        if (mins < cycleMin) {
            const zName = obtenerNombreZona(zona);
            showGenericModal({
                title: "Configuración Inválida de Ciclo",
                msg: `En la zona ${zName}, la duración total del riego (${mins} min) no puede ser menor que la duración de un ciclo (${cycleMin} min). Por favor modifique los valores antes de iniciar.`,
                hideCancel: true
            });
            return;
        }
        
        zonaObj.cycle_min = cycleMin;
        zonaObj.soak_min = soakMin;
    }
    
    sendCmd({
        comando: "RIEGO_MANUAL",
        zonas: { [zona]: zonaObj }
    });
    pendingCommand = true;

    // Colapsar la tarjeta de control manual al iniciar el riego
    const content = document.getElementById('manual-control-content');
    const chevron = document.getElementById('manual-toggle-chevron');
    if (content) content.classList.add('hidden');
    if (chevron) chevron.classList.remove('rotate-180');
});

document.getElementById('btn-manual-stop').addEventListener('click', () => {
    sendCmd({ comando: "CANCELAR_RIEGO" });
    pendingCommand = true;
});

document.getElementById('btn-next-start')?.addEventListener('click', (e) => {
    const progId = e.target.dataset.progId;
    if (progId) {
        sendCmd({ comando: "RIEGO_PROGRAMA", prog_id: progId });
        pendingCommand = true;
    }
});

function openEditSeasonModal(idx, seasonData) {
    const modal = document.getElementById('modal-season-edit');
    if (!modal) return;
    
    document.getElementById('season-edit-title').textContent = `Editar ${seasonData.nombre}`;
    document.getElementById('season-input-ini').value = `2026-${seasonData.inicio}`; // Dummy year to satisfy date input
    document.getElementById('season-input-fin').value = `2026-${seasonData.fin}`;
    
    const btnCancel = document.getElementById('btn-season-cancel');
    const btnOk = document.getElementById('btn-season-ok');
    
    // Clean old listeners
    const newBtnOk = btnOk.cloneNode(true);
    btnOk.parentNode.replaceChild(newBtnOk, btnOk);
    const newBtnCancel = btnCancel.cloneNode(true);
    btnCancel.parentNode.replaceChild(newBtnCancel, btnCancel);
    
    newBtnOk.addEventListener('click', () => {
        let ini = document.getElementById('season-input-ini').value.trim();
        let fin = document.getElementById('season-input-fin').value.trim();
        
        if (!ini || !fin) {
            showGenericModal({ title: "Error", msg: "Las fechas no pueden quedar vacías.", hideCancel: true });
            return;
        }
        
        // Extraer MM-DD de YYYY-MM-DD
        if (ini.length > 5) ini = ini.substring(5);
        if (fin.length > 5) fin = fin.substring(5);
        
        const nuevas = JSON.parse(JSON.stringify(state.deviceConfig.ajustes_estacionales));
        nuevas[idx].inicio = ini;
        nuevas[idx].fin = fin;
        
        state.deviceConfig.ajustes_estacionales = nuevas;
        refreshUIFromConfig();
        
        sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: nuevas } });
        pendingCommand = true;
        
        modal.classList.add('hidden');
    });
    
    newBtnCancel.addEventListener('click', () => {
        modal.classList.add('hidden');
    });
    
    modal.classList.remove('hidden');
}

document.getElementById('btn-hemi-sur')?.addEventListener('click', () => {
    const defaultSur = [
        {"nombre": "Verano", "inicio": "12-21", "fin": "03-20", "porcentaje": 100},
        {"nombre": "Otono", "inicio": "03-21", "fin": "06-20", "porcentaje": 100},
        {"nombre": "Invierno", "inicio": "06-21", "fin": "09-20", "porcentaje": 100},
        {"nombre": "Primavera", "inicio": "09-21", "fin": "12-20", "porcentaje": 100}
    ];
    state.deviceConfig.ajustes_estacionales = defaultSur;
    refreshUIFromConfig();
    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: defaultSur } });
    pendingCommand = true;
});

document.getElementById('btn-hemi-norte')?.addEventListener('click', () => {
    const defaultNorte = [
        {"nombre": "Verano", "inicio": "06-21", "fin": "09-20", "porcentaje": 100},
        {"nombre": "Otono", "inicio": "09-21", "fin": "12-20", "porcentaje": 100},
        {"nombre": "Invierno", "inicio": "12-21", "fin": "03-20", "porcentaje": 100},
        {"nombre": "Primavera", "inicio": "03-21", "fin": "06-20", "porcentaje": 100}
    ];
    state.deviceConfig.ajustes_estacionales = defaultNorte;
    refreshUIFromConfig();
    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: defaultNorte } });
    pendingCommand = true;
});

document.querySelectorAll('.btn-rain-delay').forEach(btn => {
    btn.addEventListener('click', () => {
        const dias = parseInt(btn.dataset.days);
        sendCmd({ comando: "RAIN_DELAY", dias: dias });
        pendingCommand = true;
    });
});

document.getElementById('btn-cancel-rain')?.addEventListener('click', () => {
    sendCmd({ comando: "RAIN_DELAY", dias: 0 });
    pendingCommand = true;
});

// ==========================================
// SCHEDULER
// ==========================================
function initSchedulerUI() {
    // Subtabs del Scheduler: Editor | Calendario | Perfiles
    document.querySelectorAll('.scheduler-subtab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetPanelId = btn.getAttribute('data-target');
            document.querySelectorAll('.scheduler-subtab-btn').forEach(b => {
                b.classList.remove('bg-teal-600', 'text-white', 'shadow');
                b.classList.add('text-slate-600', 'dark:text-slate-400');
            });
            btn.classList.add('bg-teal-600', 'text-white', 'shadow');
            btn.classList.remove('text-slate-600', 'dark:text-slate-400');

            document.querySelectorAll('.scheduler-view-panel').forEach(panel => {
                if (panel.id === targetPanelId) {
                    panel.classList.remove('hidden');
                } else {
                    panel.classList.add('hidden');
                }
            });

            if (targetPanelId === 'scheduler-calendar-view') {
                if (typeof weeklyCalendarService !== 'undefined') {
                    weeklyCalendarService.render();
                }
            } else if (targetPanelId === 'scheduler-presets-view') {
                if (typeof presetsService !== 'undefined') {
                    presetsService.render();
                }
            }

            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                window.lucide.createIcons();
            }
        });
    });

    // Tabs de programas
    document.querySelectorAll('.prog-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            if (state.hasUnsavedChanges) {
                showGenericModal({
                    title: "Cambios sin guardar",
                    msg: "Tienes cambios sin guardar. ¿Deseas descartarlos?",
                    onOk: () => {
                        state.hasUnsavedChanges = false;
                        state.activeProgTab = tab.dataset.prog;
                        
                        document.querySelectorAll('.prog-tab').forEach(t => {
                            t.classList.remove('bg-slate-700', 'text-white', 'shadow');
                            t.classList.add('text-slate-400');
                        });
                        tab.classList.add('bg-slate-700', 'text-white', 'shadow');
                        tab.classList.remove('text-slate-400');
                        
                        loadProgramIntoUI(state.activeProgTab);
                    }
                });
                return;
            } else {
                state.activeProgTab = tab.dataset.prog;
                
                document.querySelectorAll('.prog-tab').forEach(t => {
                    t.classList.remove('bg-slate-700', 'text-white', 'shadow');
                    t.classList.add('text-slate-400');
                });
                tab.classList.add('bg-slate-700', 'text-white', 'shadow');
                tab.classList.remove('text-slate-400');
                
                loadProgramIntoUI(state.activeProgTab);
            }
        });
    });

    // Días
    document.querySelectorAll('.day-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.classList.toggle('active');
            updateTempProgData();
        });
    });

    // Nombre y Activo
    document.getElementById('prog-name').addEventListener('change', updateTempProgData);
    document.getElementById('prog-active').addEventListener('change', updateTempProgData);

    // Añadir Hora
    document.getElementById('add-time-btn').addEventListener('click', () => {
        const cont = document.getElementById('prog-times');
        if (cont.children.length >= 4) {
            showGenericModal({
                title: "Límite de arranques",
                msg: "Máximo 4 arranques por programa.",
                hideCancel: true
            });
            return;
        }
        
        const div = document.createElement('div');
        div.className = "flex items-center gap-2";
        div.innerHTML = `
            <input type="time" class="flex-1 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg p-2 text-sm focus:outline-none focus:border-teal-500 text-slate-800 dark:text-slate-100 time-input">
            <button class="p-2 text-red-550 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/20 rounded-lg transition" onclick="this.parentElement.remove(); updateTempProgData();"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        `;
        cont.appendChild(div);
        lucide.createIcons();
        div.querySelector('input').addEventListener('change', updateTempProgData);
        updateTempProgData();
    });

    document.getElementById('btn-sync-prog').addEventListener('click', () => {
        updateTempProgData();

        // Validar Ciclo y Remojo en todas las zonas activas del programa
        const invalidZones = [];
        const maxZ = state.deviceConfig.max_zonas || 4;
        for (let i = 1; i <= maxZ; i++) {
            const toggleEl = document.querySelector(`.z-enable-toggle[data-zidx="${i}"]`);
            if (toggleEl && toggleEl.checked) {
                const minInp = document.querySelector(`.z-min-input[data-zidx="${i}"]`);
                const cycleCheck = document.querySelector(`.z-cycle-check[data-zidx="${i}"]`);
                if (minInp && cycleCheck && cycleCheck.checked) {
                    const mins = parseInt(minInp.value) || 0;
                    const cInp = document.querySelector(`.z-c-input[data-zidx="${i}"]`);
                    const cycleMin = parseInt(cInp?.value) || 0;
                    if (mins > 0 && cycleMin > 0 && mins < cycleMin) {
                        const zName = obtenerNombreZona(i);
                        invalidZones.push(`• ${zName}: Duración (${mins} min) < Ciclo (${cycleMin} min)`);
                    }
                }
            }
        }

        if (invalidZones.length > 0) {
            showGenericModal({
                title: "Configuración Inválida de Ciclo y Remojo",
                msg: `En el programa activo, la duración total no puede ser menor que la duración de un ciclo:\n\n${invalidZones.join('\n')}\n\nPor favor modifique los valores antes de guardar.`,
                hideCancel: true
            });
            return;
        }

        const fullProgObj = { ...state.deviceConfig.programas };
        fullProgObj[state.activeProgTab] = state.tempProgData;
        state.deviceConfig.programas = fullProgObj;
        state.hasUnsavedChanges = false;
        actualizarVisualizacionPestanasProgramas();
        
        sendCmd({
            comando: "UPDATE_PROGRAMA",
            prog_id: state.activeProgTab,
            prog_data: state.tempProgData
        });
        pendingCommand = true;
        
        // Animacion del boton
        const btn = document.getElementById('btn-sync-prog');
        const ogText = btn.innerHTML;
        btn.innerHTML = '<i data-lucide="check-circle" class="w-5 h-5"></i> Guardado';
        btn.classList.replace('bg-blue-600', 'bg-green-600');
        lucide.createIcons();
        setTimeout(() => {
            btn.innerHTML = ogText;
            btn.classList.replace('bg-green-600', 'bg-blue-600');
        }, 2000);
    });
}

function loadProgramIntoUI(progId) {
    const prog = (state.deviceConfig.programas || {})[progId] || {
        nombre: `Programa ${progId}`,
        activo: false,
        dias_semana: [],
        horas_arranque: [],
        zonas: {}
    };
    
    state.tempProgData = JSON.parse(JSON.stringify(prog));
    state.hasUnsavedChanges = false;
    
    document.getElementById('prog-name').value = prog.nombre;
    document.getElementById('prog-active').checked = prog.activo;
    
    // Dias
    document.querySelectorAll('.day-btn').forEach(b => {
        if ((prog.dias_semana || []).includes(parseInt(b.dataset.day))) {
            b.classList.add('active');
        } else {
            b.classList.remove('active');
        }
    });

    // Horas
    const contTimes = document.getElementById('prog-times');
    contTimes.innerHTML = '';
    (prog.horas_arranque || []).forEach(t => {
        const div = document.createElement('div');
        div.className = "flex items-center gap-2";
        div.innerHTML = `
            <input type="time" value="${t}" class="flex-1 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg p-2 text-sm focus:outline-none focus:border-teal-500 text-slate-800 dark:text-slate-100 time-input">
            <button class="p-2 text-red-550 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/20 rounded-lg transition" onclick="this.parentElement.remove(); updateTempProgData();"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        `;
        contTimes.appendChild(div);
        div.querySelector('input').addEventListener('change', updateTempProgData);
    });
    
    // Zonas
    const maxZ = state.deviceConfig.max_zonas || 4;
    const contZones = document.getElementById('prog-zones');
    contZones.innerHTML = '';
    
    for (let i = 1; i <= maxZ; i++) {
        const zoneId = `Z${i}`;
        const zName = obtenerNombreZona(i);
        const zData = prog.zonas[zoneId] || prog.zonas[i] || { minutos: 0, cycle_min: 0, soak_min: 0 };
        const isEnabled = zData.minutos > 0;
        // Si la zona estaba deshabilitada (minutos=0), mostrar 10 min como valor por defecto
        // para cuando el usuario la vuelva a habilitar.
        const displayMins = zData.minutos > 0 ? zData.minutos : 10;
        const hasCycle = Boolean(zData.soak_min > 0 && zData.cycle_min > 0 && zData.cycle_min < (zData.minutos || Infinity));

        const div = document.createElement('div');
        div.className = "bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl p-3 transition-all duration-200";
        div.style.opacity = isEnabled ? '1' : '0.45';
        div.innerHTML = `
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center flex-1 pr-2 min-w-0">
                    <span class="text-xs font-bold text-teal-600 dark:text-teal-400 mr-2 whitespace-nowrap">Z${i}:</span>
                    <input type="text" class="bg-transparent text-sm font-medium w-full focus:outline-none focus:border-b focus:border-teal-500 z-name-input text-slate-800 dark:text-slate-200" data-zidx="${i}" value="${zName}" placeholder="Nombre...">
                </div>
                <!-- Toggle habilitado/deshabilitado -->
                <label class="flex items-center gap-1.5 cursor-pointer flex-shrink-0 ml-3" title="${isEnabled ? 'Zona activa en este programa' : 'Zona deshabilitada en este programa'}">
                    <span class="text-[11px] font-semibold w-6 text-right z-enable-label" style="color:${isEnabled ? '#14b8a6' : '#94a3b8'}">${isEnabled ? 'ON' : 'OFF'}</span>
                    <div class="relative w-10 h-5 flex-shrink-0">
                        <input type="checkbox" class="sr-only z-enable-toggle" data-zidx="${i}" ${isEnabled ? 'checked' : ''}>
                        <div class="w-10 h-5 rounded-full transition-colors duration-200 z-toggle-bg" style="background-color:${isEnabled ? '#14b8a6' : '#cbd5e1'}"></div>
                        <div class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200 z-toggle-knob" style="transform:translateX(${isEnabled ? '20px' : '0'})"></div>
                    </div>
                </label>
            </div>
            <!-- Campos visibles solo cuando la zona está habilitada -->
            <div class="z-zone-fields ${isEnabled ? '' : 'hidden'}">
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xs text-slate-500 dark:text-slate-400 flex-1">Duración</span>
                    <input type="number" min="1" max="240" class="w-16 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded p-1 text-center text-sm text-slate-800 dark:text-slate-100 z-min-input" data-zidx="${i}" value="${displayMins}">
                    <span class="text-xs text-slate-500 dark:text-slate-400">min</span>
                </div>
                <label class="flex items-center gap-2 text-xs text-slate-550 dark:text-slate-400 cursor-pointer select-none">
                    <input type="checkbox" class="rounded border-slate-300 dark:border-slate-600 text-teal-600 dark:text-teal-500 bg-white dark:bg-slate-800 z-cycle-check" data-zidx="${i}" ${hasCycle ? 'checked' : ''}>
                    Activar Ciclo y Remojo
                </label>
                <div class="z-cycle-opts mt-2 grid grid-cols-2 gap-2 ${hasCycle ? '' : 'hidden'}">
                    <div>
                        <span class="text-[10px] text-slate-500 dark:text-slate-400">Ciclo (min)</span>
                        <input type="number" min="1" class="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded p-1 text-sm text-slate-800 dark:text-slate-100 z-c-input" data-zidx="${i}" value="${hasCycle ? zData.cycle_min : 5}">
                    </div>
                    <div>
                        <span class="text-[10px] text-slate-500 dark:text-slate-400">Remojo (min)</span>
                        <input type="number" min="1" class="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded p-1 text-sm text-slate-800 dark:text-slate-100 z-s-input" data-zidx="${i}" value="${hasCycle ? zData.soak_min : 10}">
                    </div>
                </div>
            </div>
        `;
        contZones.appendChild(div);

        // ── Toggle ON/OFF de zona ──────────────────────────────────────────────
        const enableToggle = div.querySelector('.z-enable-toggle');
        const zoneFields   = div.querySelector('.z-zone-fields');
        const enableLabel  = div.querySelector('.z-enable-label');
        const toggleBg     = div.querySelector('.z-toggle-bg');
        const toggleKnob   = div.querySelector('.z-toggle-knob');
        const minInput     = div.querySelector('.z-min-input');

        enableToggle.addEventListener('change', () => {
            const on = enableToggle.checked;
            // Al deshabilitar: los campos se ocultan pero el input mantiene su valor
            // en el DOM — cuando se rehabilite, se restaura automáticamente.
            zoneFields.classList.toggle('hidden', !on);
            div.style.opacity = on ? '1' : '0.45';
            enableLabel.textContent = on ? 'ON' : 'OFF';
            enableLabel.style.color = on ? '#14b8a6' : '#94a3b8';
            toggleBg.style.backgroundColor = on ? '#14b8a6' : '#cbd5e1';
            toggleKnob.style.transform = on ? 'translateX(20px)' : 'translateX(0)';
            updateTempProgData();
        });

        // ── Nombre de zona ────────────────────────────────────────────────────
        div.querySelector('.z-name-input').addEventListener('change', (e) => {
            if (!state.deviceConfig.nombres_zonas) state.deviceConfig.nombres_zonas = {};
            const zoneId = `Z${i}`;
            state.deviceConfig.nombres_zonas[zoneId] = e.target.value;
            if (state.chipId) {
                localStorage.setItem(`NOMBRES_ZONAS_${state.chipId}`, JSON.stringify(state.deviceConfig.nombres_zonas));
            }
            if (db && state.chipId) {
                db.collection("dispositivos").doc(state.chipId).update({
                    nombres_zonas: state.deviceConfig.nombres_zonas
                }).then(() => showToast("Nombre guardado en la nube."))
                  .catch(err => console.error("Error actualizando nombre en Firestore:", err));
            }
            refreshUIFromConfig();
        });

        // ── Ciclo y Remojo ────────────────────────────────────────────────────
        const check = div.querySelector('.z-cycle-check');
        const opts  = div.querySelector('.z-cycle-opts');
        check.addEventListener('change', () => {
            opts.classList.toggle('hidden', !check.checked);
            updateTempProgData();
        });

        div.querySelectorAll('input[type="number"]').forEach(inp => {
            inp.addEventListener('change', updateTempProgData);
        });
    }

    lucide.createIcons();
}

function updateTempProgData() {
    state.tempProgData.nombre = document.getElementById('prog-name').value;
    state.tempProgData.activo = document.getElementById('prog-active').checked;
    
    const dias = [];
    document.querySelectorAll('.day-btn.active').forEach(b => dias.push(parseInt(b.dataset.day)));
    state.tempProgData.dias_semana = dias;
    
    const horas = [];
    document.querySelectorAll('.time-input').forEach(i => { if (i.value) horas.push(i.value) });
    state.tempProgData.horas_arranque = horas;
    
    const zonas = {};
    const maxZ = state.deviceConfig.max_zonas || 4;
    for(let i=1; i<=maxZ; i++) {
        // Respetar el toggle ON/OFF de zona: si está OFF, la zona no se incluye
        // (equivale a minutos:0, pero preserva los campos sin borrarlos del DOM).
        const toggleEl = document.querySelector(`.z-enable-toggle[data-zidx="${i}"]`);
        const isOn = toggleEl ? toggleEl.checked : false;
        if (!isOn) continue;  // zona deshabilitada → no incluir en el programa

        const minInp = document.querySelector(`.z-min-input[data-zidx="${i}"]`);
        if(minInp) {
            const mins = parseInt(minInp.value) || 0;
            if(mins > 0) {
                const cycleCheck = document.querySelector(`.z-cycle-check[data-zidx="${i}"]`);
                const checked = cycleCheck ? cycleCheck.checked : false;
                const cInp = document.querySelector(`.z-c-input[data-zidx="${i}"]`);
                const sInp = document.querySelector(`.z-s-input[data-zidx="${i}"]`);
                
                const cycleMin = checked ? (parseInt(cInp?.value) || 5) : 0;
                const soakMin = checked ? (parseInt(sInp?.value) || 10) : 0;

                zonas[`Z${i}`] = {
                    minutos: mins,
                    cycle_min: cycleMin,
                    soak_min: soakMin
                };
            }
        }
    }
    state.tempProgData.zonas = zonas;
    
    state.hasUnsavedChanges = true;
}

// ==========================================
// HISTORY / LOGS
// ==========================================
function requestLogs() {
    sendCmd({ comando: "GET_LOGS" }); // Requiere soporte en backend o leer via BLE
}

document.getElementById('btn-refresh-logs').addEventListener('click', requestLogs);

document.getElementById('btn-clear-logs')?.addEventListener('click', () => {
    showGenericModal({
        title: "Confirmar",
        msg: "¿Estás seguro de que deseas borrar todo el historial? Esta acción no se puede deshacer.",
        onOk: () => {
            sendCmd({ comando: "CLEAR_HISTORY" });
            pendingCommand = true;
            const cont = document.getElementById('logs-container');
            if (cont) cont.innerHTML = '<div class="text-center text-slate-500 text-sm mt-10">El historial ha sido borrado.</div>';
        }
    });
});

function renderLogs(logsArray) {
    const cont = document.getElementById('logs-container');
    cont.innerHTML = '';
    
    if(!logsArray || logsArray.length === 0) {
        cont.innerHTML = '<div class="text-center text-slate-500 text-sm mt-10">No hay eventos registrados.</div>';
        return;
    }
    
    // Filtrar los eventos requeridos:
    // 1. Reinicio del equipo (log.msg === "Sistema iniciado")
    // 2. Ejecución de programa de riego (log.tipo === "inicio_zona")
    // 3. Estado del sensor de lluvia (log.tipo === "sensor_lluvia")
    const filteredLogs = logsArray.filter(log => {
        return (log.msg === 'Sistema iniciado') || (log.tipo === 'inicio_zona') || (log.tipo === 'sensor_lluvia');
    });
    
    if(filteredLogs.length === 0) {
        cont.innerHTML = '<div class="text-center text-slate-500 text-sm mt-10">No hay eventos registrados.</div>';
        return;
    }
    
    filteredLogs.reverse().forEach(log => {
        const div = document.createElement('div');
        div.className = "flex gap-3 text-sm p-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 transition-colors duration-200";
        
        let icon = '<i data-lucide="info" class="w-4 h-4 text-slate-500 dark:text-slate-400"></i>';
        let desc = "";
        
        if (log.msg === 'Sistema iniciado') {
            icon = '<i data-lucide="refresh-cw" class="w-4 h-4 text-amber-500 dark:text-amber-400"></i>';
            desc = "Reinicio del equipo";
        } else if (log.tipo === 'inicio_zona') {
            icon = '<i data-lucide="droplets" class="w-4 h-4 text-teal-600 dark:text-teal-400"></i>';
            const zoneName = obtenerNombreZona(log.zona);
            const duracion = log.duracion || 0;
            const ajuste = log.ajuste !== undefined ? log.ajuste : 100;
            const cicloAct = log.ciclo_actual || 1;
            const ciclosTot = log.ciclos_totales || 1;
            desc = `${log.prog} - ${zoneName} (${duracion} min · ${ajuste}% · Ciclos: ${cicloAct}/${ciclosTot} min)`;
        } else if (log.tipo === 'sensor_lluvia') {
            icon = '<i data-lucide="cloud-rain" class="w-4 h-4 text-sky-500 dark:text-sky-400"></i>';
            if (log.estado === 'detectada') {
                desc = "Riego pausado por el sensor de lluvia";
            } else if (log.estado === 'secado') {
                desc = `Pausa prolongada por secado (${log.horas} h)`;
            } else if (log.estado === 'fin_secado') {
                desc = "Fin de secado por el sensor de lluvia";
            } else {
                desc = "Pausa liberada por el sensor de lluvia";
            }
        }

        let timeStr = "";
        if (log.ts) {
            // Convertir ts (MicroPython Y2K epoch) a JS Date (1970 epoch)
            const date = new Date((log.ts + 946684800) * 1000);
            const dd = String(date.getDate()).padStart(2, '0');
            const mm = String(date.getMonth() + 1).padStart(2, '0');
            const yyyy = date.getFullYear();
            const hh = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            timeStr = `${dd}/${mm}/${yyyy} - ${hh}:${min}`;
        }

        div.innerHTML = `
            <div class="mt-0.5">${icon}</div>
            <div>
                <span class="font-medium text-slate-800 dark:text-slate-300 block mb-0.5">${desc}</span>
                <span class="text-slate-500 dark:text-slate-500 text-xs">${timeStr}</span>
            </div>
        `;
        cont.appendChild(div);
    });
    
    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// ==========================================
// THEME MANAGER (LIGHT/DARK/SYSTEM)
// ==========================================
function initTheme() {
    const savedMode = localStorage.getItem('theme-mode') || 'system';
    applyTheme(savedMode);
}

function setTheme(mode) {
    localStorage.setItem('theme-mode', mode);
    applyTheme(mode);
}

function applyTheme(mode) {
    const htmlEl = document.documentElement;
    
    // Clean up existing system listener if any
    if (window._systemThemeListener) {
        try {
            window.matchMedia('(prefers-color-scheme: dark)').removeEventListener('change', window._systemThemeListener);
        } catch (e) {
            // Support older devices
            window.matchMedia('(prefers-color-scheme: dark)').removeListener(window._systemThemeListener);
        }
        window._systemThemeListener = null;
    }
    
    if (mode === 'system') {
        const mq = window.matchMedia('(prefers-color-scheme: dark)');
        const updateSystemTheme = (e) => {
            if (e.matches) {
                htmlEl.classList.add('dark');
            } else {
                htmlEl.classList.remove('dark');
            }
        };
        updateSystemTheme(mq);
        window._systemThemeListener = updateSystemTheme;
        try {
            mq.addEventListener('change', window._systemThemeListener);
        } catch (e) {
            mq.addListener(window._systemThemeListener);
        }
    } else if (mode === 'dark') {
        htmlEl.classList.add('dark');
    } else {
        htmlEl.classList.remove('dark');
    }
    
    // Update theme selector buttons active state in Settings UI
    document.querySelectorAll('.theme-btn').forEach(btn => {
        const themeVal = btn.getAttribute('data-theme');
        if (themeVal === mode) {
            btn.classList.remove('border-slate-200', 'dark:border-slate-700', 'text-slate-500', 'dark:text-slate-400', 'bg-slate-50', 'dark:bg-slate-900', 'bg-slate-200/70');
            btn.classList.add('border-teal-500', 'text-white', 'bg-teal-600', 'shadow-md');
        } else {
            btn.classList.add('border-slate-200', 'dark:border-slate-700', 'text-slate-600', 'dark:text-slate-400', 'bg-slate-200/70', 'dark:bg-slate-900');
            btn.classList.remove('border-teal-500', 'text-white', 'bg-teal-600', 'shadow-md');
        }
    });

    // Update header theme button icon & title
    const headerBtn = document.getElementById('header-theme-toggle');
    const headerIcon = document.getElementById('header-theme-icon');
    if (headerIcon) {
        let iconName = 'sun';
        let iconClass = 'w-4 h-4 text-amber-500';
        let iconTitle = 'Modo Claro (clic para cambiar)';
        if (mode === 'dark') {
            iconName = 'moon';
            iconClass = 'w-4 h-4 text-indigo-400';
            iconTitle = 'Modo Oscuro (clic para cambiar)';
        } else if (mode === 'system') {
            iconName = 'monitor';
            iconClass = 'w-4 h-4 text-teal-500 dark:text-teal-400';
            iconTitle = 'Modo Sistema (clic para cambiar)';
        }
        headerIcon.setAttribute('data-lucide', iconName);
        headerIcon.className = iconClass;
        if (headerBtn) headerBtn.setAttribute('title', iconTitle);
        if (window.lucide) window.lucide.createIcons();
    }
}

// ==========================================
// SETTINGS & AUTH
// ==========================================
function initSettingsUI() {
    // Header quick theme toggle listener
    const headerThemeBtn = document.getElementById('header-theme-toggle');
    if (headerThemeBtn) {
        headerThemeBtn.addEventListener('click', () => {
            const currentMode = localStorage.getItem('theme-mode') || 'system';
            const modes = ['light', 'dark', 'system'];
            const nextIdx = (modes.indexOf(currentMode) + 1) % modes.length;
            const nextMode = modes[nextIdx];
            setTheme(nextMode);
            const modeNames = { light: 'Modo Claro ☀️', dark: 'Modo Oscuro 🌙', system: 'Modo Sistema 🖥️' };
            showToast(`Tema: ${modeNames[nextMode]}`);
        });
    }

    // Theme selector listeners
    document.querySelectorAll('.theme-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const mode = btn.getAttribute('data-theme');
            setTheme(mode);
        });
    });
    document.querySelectorAll('.hw-zones-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const val = parseInt(btn.dataset.val);
            sendCmd({ comando: "UPDATE_CONFIG", config: { max_zonas: val } });
            pendingCommand = true;
            state.deviceConfig.max_zonas = val;
            refreshUIFromConfig();
        });
    });
    
    document.querySelectorAll('.hw-pump-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const val = btn.dataset.val === 'true';
            sendCmd({ comando: "UPDATE_CONFIG", config: { modo_bomba: val } });
            pendingCommand = true;
            state.deviceConfig.modo_bomba = val;
            refreshUIFromConfig();
        });
    });

    document.getElementById('btn-save-hw').addEventListener('click', () => {
        const tempOffset = parseFloat(document.getElementById('settings-temp-offset')?.value) || 0.0;
        state.deviceConfig.calibracion_temp = tempOffset;

        sendCmd({ 
            comando: "UPDATE_CONFIG", 
            config: { 
                max_zonas: state.deviceConfig.max_zonas, 
                modo_bomba: state.deviceConfig.modo_bomba,
                calibracion_temp: tempOffset,
                sensor_lluvia_delay_horas: state.deviceConfig.sensor_lluvia_delay_horas || 0
            } 
        });
        pendingCommand = true;
        showGenericModal({
            title: "Configuración Guardada",
            msg: "Configuración de hardware y calibración guardada en la placa.",
            hideCancel: true
        });
    });

    const tempOffsetSld = document.getElementById('settings-temp-offset');
    const tempOffsetLbl = document.getElementById('temp-offset-val');
    if (tempOffsetSld && tempOffsetLbl) {
        tempOffsetSld.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value) || 0.0;
            const sign = val > 0 ? '+' : '';
            tempOffsetLbl.textContent = `${sign}${val.toFixed(1)} °C`;
        });
    }

    document.querySelectorAll('.rain-active-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const val = e.currentTarget.dataset.val === "true";
            state.deviceConfig.sensor_lluvia_activo = val;
            document.querySelectorAll('.rain-active-btn').forEach(b => {
                if ((b.dataset.val === "true") === val) {
                    b.classList.add('bg-slate-700', 'text-white');
                    b.classList.remove('text-slate-400');
                } else {
                    b.classList.remove('bg-slate-700', 'text-white');
                    b.classList.add('text-slate-400');
                }
            });
            const rainTypeContainer = document.getElementById('container-rain-type');
            if (rainTypeContainer) {
                if (val) rainTypeContainer.classList.remove('opacity-40', 'pointer-events-none');
                else rainTypeContainer.classList.add('opacity-40', 'pointer-events-none');
            }
        });
    });

    document.querySelectorAll('.rain-type-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const val = e.currentTarget.dataset.val;
            state.deviceConfig.sensor_lluvia_tipo = val;
            document.querySelectorAll('.rain-type-btn').forEach(b => {
                if (b.dataset.val === val) {
                    b.classList.add('bg-slate-700', 'text-white');
                    b.classList.remove('text-slate-400');
                } else {
                    b.classList.remove('bg-slate-700', 'text-white');
                    b.classList.add('text-slate-400');
                }
            });
        });
    });

    document.getElementById('btn-save-rain-delay').addEventListener('click', () => {
        const inputVal = parseInt(document.getElementById('settings-rain-delay-hours').value) || 0;
        const isActive = state.deviceConfig.sensor_lluvia_activo !== false;
        const sensorType = state.deviceConfig.sensor_lluvia_tipo || "NA";
        sendCmd({
            comando: "UPDATE_CONFIG",
            config: {
                sensor_lluvia_activo: isActive,
                sensor_lluvia_tipo: sensorType,
                sensor_lluvia_delay_horas: inputVal
            }
        });
        pendingCommand = true;
        state.deviceConfig.sensor_lluvia_delay_horas = inputVal;
        showGenericModal({
            title: "Configuración Guardada",
            msg: `Configuración del sensor de lluvia guardada (${isActive ? 'Habilitado - ' + sensorType : 'Desactivado'}, retraso ${inputVal}h).`,
            hideCancel: true
        });
    });

    const rainDelayHoursSld = document.getElementById('settings-rain-delay-hours');
    const rainDelayHoursLbl = document.getElementById('rain-delay-hours-val');
    if (rainDelayHoursSld && rainDelayHoursLbl) {
        rainDelayHoursSld.addEventListener('input', (e) => {
            const val = parseInt(e.target.value) || 0;
            rainDelayHoursLbl.textContent = `${val} ${val === 1 ? 'hora' : 'horas'}`;
        });
    }

    document.getElementById('btn-ble-pair').addEventListener('click', async () => {
        const res = await comms.connectBLE();
        if (res.success) {
            document.getElementById('ble-info').classList.remove('hidden');
            document.getElementById('ble-device-name').textContent = comms.bleDevice.name;
            document.getElementById('ble-chip-id').textContent = res.chipId;
            
            state.chipId = res.chipId;
            localStorage.setItem('CHIP_ID', res.chipId);
            
            // Cargar nombres de zonas guardados localmente para este nuevo chipId
            const localNames = localStorage.getItem(`NOMBRES_ZONAS_${res.chipId}`);
            if (localNames) {
                try {
                    state.deviceConfig.nombres_zonas = JSON.parse(localNames);
                } catch (e) {
                    console.error("[INIT] Error parseando nombres de zonas locales:", e);
                }
            } else {
                // Si no hay nombres guardados, usar los nombres por defecto
                state.deviceConfig.nombres_zonas = {
                    "Z1": "Zona 1", "Z2": "Zona 2", "Z3": "Zona 3", "Z4": "Zona 4",
                    "Z5": "Zona 5", "Z6": "Zona 6", "Z7": "Zona 7", "Z8": "Zona 8"
                };
            }
            
            // Ver si falta auth
            if (!state.token) showModalAuth();
            else {
                // Si ya teníamos token, lo mandamos para validar e intentar update
                window._startupSeq = 1;
                sendCmd({comando: "GET_CONFIG"});
            }
        } else {
            showGenericModal({
                title: "Error de Bluetooth",
                msg: "Fallo la vinculación Bluetooth.",
                hideCancel: true
            });
        }
    });

    // Ubicación y Clima
    document.getElementById('btn-detect-gps')?.addEventListener('click', () => {
        if (typeof weatherService !== 'undefined') {
            weatherService.detectarGPS();
        }
    });

    document.getElementById('btn-search-city')?.addEventListener('click', async () => {
        const query = document.getElementById('input-search-city')?.value;
        const resultsCont = document.getElementById('city-search-results');
        if (!resultsCont) return;

        resultsCont.innerHTML = '<div class="text-[11px] text-slate-400 p-2 text-center">Buscando localidades...</div>';
        resultsCont.classList.remove('hidden');

        if (typeof weatherService !== 'undefined') {
            const results = await weatherService.buscarCiudad(query);
            if (results.length === 0) {
                resultsCont.innerHTML = '<div class="text-[11px] text-slate-400 p-2 text-center">No se encontraron resultados.</div>';
                return;
            }

            resultsCont.innerHTML = results.map(res => {
                const admin = res.admin1 ? `, ${res.admin1}` : '';
                const pais = res.country ? ` (${res.country})` : '';
                const nombreCompleto = `${res.name}${admin}${pais}`;
                const sanitizedName = res.name.replace(/'/g, "\\'");
                return `
                    <button type="button" class="w-full text-left p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-xs transition flex items-center justify-between border-b border-slate-100 dark:border-slate-800/60 last:border-0" onclick="window.seleccionarCiudadBuscada(${res.latitude}, ${res.longitude}, '${sanitizedName}')">
                        <span class="font-medium text-slate-700 dark:text-slate-200 truncate">${nombreCompleto}</span>
                        <span class="text-[10px] text-slate-400 font-mono flex-shrink-0 ml-2">${res.latitude.toFixed(2)}°, ${res.longitude.toFixed(2)}°</span>
                    </button>
                `;
            }).join('');
        }
    });

    document.getElementById('settings-smart-delay-toggle')?.addEventListener('change', (e) => {
        if (typeof weatherService !== 'undefined') {
            weatherService.smartDelayEnabled = e.target.checked;
            localStorage.setItem('WEATHER_SMART_DELAY', e.target.checked);
            if (!e.target.checked) {
                document.getElementById('smart-rain-delay-card')?.classList.add('hidden');
            } else {
                weatherService.evaluarSugerenciaRetrasoLluvia();
            }
        }
    });

    document.getElementById('btn-wifi-connect')?.addEventListener('click', async () => {
        if (!state.chipId || !state.token) {
            showGenericModal({
                title: "No configurado",
                msg: "Aún no tienes credenciales guardadas. Debes conectarte primero por Bluetooth al menos una vez para vincular el equipo.",
                hideCancel: true
            });
            return;
        }
        
        // Si hay una conexion BLE activa, la cerramos forzosamente para evitar conflictos
        if (comms.bleDevice && comms.bleDevice.gatt.connected) {
            comms.bleDevice.gatt.disconnect();
        }
        
        await comms.initConnection(state.chipId, state.token);
        escucharConfiguracionFirestore(state.chipId);
        // Si logro conectarse por MQTT, enviar comandos iniciales
        if (comms.mode === 'MQTT') {
            setTimeout(() => {
                window._startupSeq = 1;
                sendCmd({comando: "GET_CONFIG"});
            }, 1000);
        }
    });

    document.getElementById('btn-auth-submit').addEventListener('click', () => {
        const pwd = document.getElementById('auth-password').value.trim();
        if(pwd.length < 4) {
            showGenericModal({
                title: "Contraseña",
                msg: "La contraseña es muy corta.",
                hideCancel: true
            });
            return;
        }
        
        // Enviar INIT_TOKEN via BLE (porque aún no hay token local para usar MQTT)
        // Se asume que el backend acepta esto si su config["token_acceso"] es null.
        // Si no es null, el backend responderá AUTH_ERROR en handleIncomingMessage.
        
        state.token = pwd;
        localStorage.setItem('TOKEN', pwd);
        
        sendCmd({ comando: "INIT_TOKEN", token: pwd });
        pendingCommand = true;
        setTimeout(() => {
            window._startupSeq = 1;
            sendCmd({comando: "GET_CONFIG"});
        }, 500);
        
        document.getElementById('modal-auth').classList.add('hidden');
    });
    
    document.getElementById('btn-clear-local').addEventListener('click', () => {
        showGenericModal({
            title: "Desvincular Equipo",
            msg: "¿Seguro que quieres borrar tus credenciales del celular? Deberás emparejar por BLE nuevamente.",
            onOk: () => {
                localStorage.clear();
                location.reload();
            }
        });
    });
    
    document.getElementById('btn-factory-reset').addEventListener('click', () => {
        showGenericModal({
            title: "Factory Reset",
            msg: "Para confirmar el borrado físico de la placa, ingresa tu contraseña:",
            inputType: "password",
            onOk: (pwd) => {
                if (pwd === state.token) {
                    sendCmd({ comando: "FACTORY_RESET" });
                    pendingCommand = true;
                    showGenericModal({
                        title: "Reseteo de Fábrica",
                        msg: "Comando enviado. El sistema se reiniciará de fábrica.",
                        hideCancel: true,
                        onOk: () => {
                            localStorage.clear();
                            location.reload();
                        }
                    });
                } else {
                    showGenericModal({
                        title: "Error",
                        msg: "Contraseña incorrecta.",
                        hideCancel: true
                    });
                }
            }
        });
    });
    
    document.getElementById('btn-sync-rtc').addEventListener('click', () => {
        // Enviar unix timestamp en segundos ajustado a la zona horaria local
        // (porque MicroPython asume UTC si no se le configuran reglas complejas)
        const date = new Date();
        const offsetSecs = date.getTimezoneOffset() * 60;
        const unixSecs = Math.floor(date.getTime() / 1000) - offsetSecs;
        sendCmd({ comando: "SYNC_RTC", timestamp: unixSecs });
        pendingCommand = true;
        showGenericModal({
            title: "Sincronización de Reloj",
            msg: "Hora sincronizada con el celular.",
            hideCancel: true
        });
    });
    
    document.getElementById('btn-send-wifi').addEventListener('click', () => {
        const s = document.getElementById('wifi-ssid').value.trim();
        const p = document.getElementById('wifi-pass').value.trim();
        if(s) {
            sendCmd({ comando: "config_wifi", ssid: s, pass: p });
            pendingCommand = true;
            showGenericModal({
                title: "Credenciales",
                msg: "Credenciales enviadas al equipo.",
                hideCancel: true
            });
        }
    });
}

function showModalAuth() {
    document.getElementById('modal-auth').classList.remove('hidden');
}

// Wrapper envio comandos con interceptor de sincronización a Firestore
function sendCmd(obj) {
    if (obj && (obj.comando === "UPDATE_CONFIG" || obj.comando === "UPDATE_PROGRAMA" || obj.comando === "RAIN_DELAY")) {
        const payload = {};
        
        if (obj.comando === "UPDATE_CONFIG" && obj.config) {
            Object.assign(payload, obj.config);
            // Evitar enviar nombres_zonas al ESP32 por BLE/MQTT para no congestionar
            if (payload.nombres_zonas) {
                delete payload.nombres_zonas;
            }
        } else if (obj.comando === "UPDATE_PROGRAMA") {
            // Mapear zonas del programa a claves "Z1", "Z2", etc.
            const progData = { ...obj.prog_data };
            if (progData.zonas) {
                const zonasNuevas = {};
                for (let zKey in progData.zonas) {
                    const zNewKey = String(zKey).toUpperCase().startsWith('Z') ? String(zKey).toUpperCase() : `Z${zKey}`;
                    zonasNuevas[zNewKey] = progData.zonas[zKey];
                }
                progData.zonas = zonasNuevas;
            }
            obj.prog_data = progData;
            
            if (!state.deviceConfig.programas) state.deviceConfig.programas = {};
            state.deviceConfig.programas[obj.prog_id] = obj.prog_data;
            state.deviceConfig.programas = normalizeProgramasData(state.deviceConfig.programas);
            
            payload.programas = state.deviceConfig.programas;
        } else if (obj.comando === "RAIN_DELAY") {
            payload.timestamp_rain_delay = obj.dias > 0 ? (Math.floor(Date.now() / 1000) + obj.dias * 86400) : 0;
            if (obj.dias <= 0) {
                payload.timestamp_sensor_lluvia_clear = 0;
                state.deviceConfig.timestamp_sensor_lluvia_clear = 0;
                state.deviceConfig.timestamp_rain_delay = 0;
                if (state.telemetria) {
                    state.telemetria.timestamp_sensor_lluvia_clear = 0;
                    state.telemetria.timestamp_rain_delay = 0;
                }
            }
        }
        
        // Sincronizar en Firestore
        sincronizarConfigTecnicaAFirestore(payload);
        
        // Inyectar versión en comando de salida
        if (obj.comando === "UPDATE_CONFIG") {
            obj.config.config_version = state.deviceConfig.config_version;
        } else if (obj.comando === "UPDATE_PROGRAMA" || obj.comando === "RAIN_DELAY") {
            obj.config_version = state.deviceConfig.config_version;
        }
    }
    
    comms.sendCommand(obj, state.token);
}

function initHelpModals() {
    const helpData = {
        'seasonal': {
            title: 'Ajustes por Temporada',
            body: 'Configura rangos de fechas (Mes-Día) y asígnales un porcentaje. Por ejemplo, Verano (12-21 a 03-20) al 150%, e Invierno (06-21 a 09-20) al 50%. El sistema leerá la fecha actual y aplicará automáticamente el ajuste.'
        },
        'rain': {
            title: 'Retraso por Lluvia',
            body: 'Pausa todos los programas automáticos durante los días especificados. Si tienes un sensor de lluvia conectado al Hardware, también se detendrán los riegos automáticamente mientras esté lloviendo.'
        },
        'hardware': {
            title: 'Configuración de Hardware',
            body: '<strong>Zonas de Riego</strong>: Para expandir la capacidad del equipo de 4 a 8 zonas, es indispensable contar con el módulo de expansión de hardware (relevadores y bornes físicos) acoplado a tu ESP32.<br><br><strong>Modos Hidráulicos (Transición)</strong>:<br>• <strong>Bomba (Overlap)</strong>: Diseñado para instalaciones con bomba de agua. Al cambiar de zona, inicia la apertura de la siguiente electroválvula unos segundos antes de cerrar la actual. Esto evita golpes de ariete e impide que la bomba funcione a tubería tapada.<br>• <strong>Red (Pausa)</strong>: Diseñado para agua directa de red de suministro. Aplica una breve pausa de apagado total entre zonas para que la presión de agua de la línea se estabilice antes de abrir la siguiente zona.'
        }
    };

    const modal = document.getElementById('help-modal');
    const titleEl = document.getElementById('help-title');
    const bodyEl = document.getElementById('help-body');
    const closeBtn = document.getElementById('btn-close-help');

    if (!modal) return; // Prevent error if element doesn't exist

    document.querySelectorAll('.btn-help').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const type = btn.getAttribute('data-help');
            if (helpData[type]) {
                titleEl.textContent = helpData[type].title;
                bodyEl.innerHTML = helpData[type].body;
                modal.classList.remove('hidden');
            }
        });
    });

    closeBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });
}

function startLEDSimulator() {
    const led = document.getElementById('simulated-led');
    if (!led) return;

    let patronActual = [[true, 100], [false, 100]]; // Default
    let step = 0;
    let timeInStep = 0;
    const TICK_MS = 100;
    
    setInterval(() => {
        const wifiConectado = (comms.mode === 'MQTT');
        const bleConectado = (comms.mode === 'BLE');
        
        let retraso = false;
        if (state.deviceConfig && state.deviceConfig.timestamp_rain_delay) {
            const nowUnix = Math.floor(Date.now() / 1000);
            const rainDelayUnix = state.deviceConfig.timestamp_rain_delay + 946684800; // Offset epoch 2000
            if (nowUnix < rainDelayUnix) {
                retraso = true;
            }
        }
        
        const estado = (state.telemetria && state.telemetria.estado) ? state.telemetria.estado : "IDLE";
        
        let nuevoPatron = []; 
        if (retraso) {
            nuevoPatron = [[true, 2000], [false, 200]];
        } else if (estado !== "IDLE") {
            if (wifiConectado) nuevoPatron = [[true, 500], [false, 500]];
            else nuevoPatron = [[true, 1000], [false, 200]];
        } else {
            if (wifiConectado) nuevoPatron = [[true, 200], [false, 4000]];
            else if (bleConectado) nuevoPatron = [[true, 200], [false, 200], [true, 200], [false, 4000]];
            else nuevoPatron = [[true, 100], [false, 100]];
        }
        
        // Si el patron cambia, reseteamos el ciclo
        if (JSON.stringify(nuevoPatron) !== JSON.stringify(patronActual)) {
            patronActual = nuevoPatron;
            step = 0;
            timeInStep = 0;
        }
        
        const currentPhase = patronActual[step];
        if (!currentPhase) return;
        
        const [isHigh, duration] = currentPhase;
        
        if (isHigh) {
            led.className = "w-2 h-2 rounded-full bg-blue-400 shadow-[0_0_8px_rgba(96,165,250,0.8)] transition-all duration-100";
        } else {
            led.className = "w-2 h-2 rounded-full bg-slate-700 transition-all duration-100";
        }
        
        timeInStep += TICK_MS;
        if (timeInStep >= duration) {
            timeInStep = 0;
            step++;
            if (step >= patronActual.length) step = 0;
        }
        
    }, TICK_MS);
}

// ==========================================
// CUSTOM MODAL HELPERS
// ==========================================

function showGenericModal(options) {
    const modal = document.getElementById('modal-generic');
    if (!modal) return;
    
    const title = document.getElementById('generic-title');
    const msg = document.getElementById('generic-msg');
    const input = document.getElementById('generic-input');
    const btnCancel = document.getElementById('btn-generic-cancel');
    const btnOk = document.getElementById('btn-generic-ok');
    
    title.textContent = options.title || "Alerta";
    msg.textContent = options.msg || "";
    
    if (options.inputType) {
        input.type = options.inputType;
        input.placeholder = options.inputPlaceholder || "";
        input.value = "";
        input.classList.remove('hidden');
    } else {
        input.classList.add('hidden');
    }
    
    if (options.hideCancel) {
        btnCancel.classList.add('hidden');
    } else {
        btnCancel.classList.remove('hidden');
    }
    
    // Clean old listeners
    const newBtnOk = btnOk.cloneNode(true);
    btnOk.parentNode.replaceChild(newBtnOk, btnOk);
    const newBtnCancel = btnCancel.cloneNode(true);
    btnCancel.parentNode.replaceChild(newBtnCancel, btnCancel);
    
    newBtnOk.addEventListener('click', () => {
        modal.classList.add('hidden');
        if (options.onOk) {
            if (options.inputType) options.onOk(input.value);
            else options.onOk();
        }
    });
    
    newBtnCancel.addEventListener('click', () => {
        modal.classList.add('hidden');
        if (options.onCancel) options.onCancel();
    });
    
    modal.classList.remove('hidden');
}

// Toggle Seasons Logic
document.getElementById('btn-toggle-seasons')?.addEventListener('click', () => {
    const content = document.getElementById('seasons-collapsible-content');
    const icon = document.getElementById('icon-seasons-collapse');
    if (content && icon) {
        if (content.classList.contains('hidden')) {
            content.classList.remove('hidden');
            icon.classList.add('rotate-180');
        } else {
            content.classList.add('hidden');
            icon.classList.remove('rotate-180');
        }
    }
});

// PWA Installation Logic
let deferredPrompt;

const isIos = () => /iphone|ipad|ipod/.test(window.navigator.userAgent.toLowerCase());
const isInStandaloneMode = () => ('standalone' in window.navigator) && (window.navigator.standalone) || window.matchMedia('(display-mode: standalone)').matches;

window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
});

if (!isInStandaloneMode() && !sessionStorage.getItem('installPromptDismissed')) {
    setTimeout(() => {
        const modal = document.getElementById('modal-install');
        if (modal) {
            modal.classList.remove('hidden');
            if (isIos()) {
                document.getElementById('install-instructions-ios')?.classList.remove('hidden');
                document.getElementById('btn-install-app')?.classList.add('hidden');
            } else if (!deferredPrompt) {
                // PC o navegador sin soporte automático de prompt
                const inst = document.getElementById('install-instructions-ios');
                if (inst) {
                    inst.innerHTML = 'Para instalar en PC/Mac:<br>Haz clic en el ícono de instalar en la barra de direcciones del navegador <i data-lucide="monitor" class="w-3 h-3 inline"></i>';
                    inst.classList.remove('hidden');
                }
                document.getElementById('btn-install-app')?.classList.add('hidden');
            }
            lucide.createIcons();
        }
    }, 3000);
}

document.getElementById('btn-install-app')?.addEventListener('click', async () => {
    if (deferredPrompt) {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
            document.getElementById('modal-install')?.classList.add('hidden');
        }
        deferredPrompt = null;
    }
});

document.querySelector('#modal-install button[onclick]')?.addEventListener('click', () => {
    sessionStorage.setItem('installPromptDismissed', 'true');
});

// ==========================================
// 🌦️ MÓDULO DE CLIMA Y SMART RAIN DELAY
// ==========================================
const weatherService = {
    data: null,
    location: {
        name: localStorage.getItem('WEATHER_LOC_NAME') || 'Buenos Aires',
        lat: parseFloat(localStorage.getItem('WEATHER_LOC_LAT')) || -34.6037,
        lon: parseFloat(localStorage.getItem('WEATHER_LOC_LON')) || -58.3816
    },
    smartDelayEnabled: localStorage.getItem('WEATHER_SMART_DELAY') !== 'false',
    lastFetch: 0,

    wmoCodeMap: {
        0: { desc: "Despejado", icon: "sun" },
        1: { desc: "Mayormente despejado", icon: "sun-dim" },
        2: { desc: "Parcialmente nublado", icon: "cloud-sun" },
        3: { desc: "Nublado", icon: "cloud" },
        45: { desc: "Niebla", icon: "cloud-fog" },
        48: { desc: "Niebla con escarcha", icon: "cloud-fog" },
        51: { desc: "Llovizna ligera", icon: "cloud-drizzle" },
        53: { desc: "Llovizna moderada", icon: "cloud-drizzle" },
        55: { desc: "Llovizna densa", icon: "cloud-drizzle" },
        61: { desc: "Lluvia ligera", icon: "cloud-rain" },
        63: { desc: "Lluvia moderada", icon: "cloud-rain" },
        65: { desc: "Lluvia fuerte", icon: "cloud-rain" },
        71: { desc: "Nieve ligera", icon: "snowflake" },
        73: { desc: "Nieve moderada", icon: "snowflake" },
        75: { desc: "Nieve intensa", icon: "snowflake" },
        80: { desc: "Chubascos ligeros", icon: "cloud-rain" },
        81: { desc: "Chubascos moderados", icon: "cloud-rain" },
        82: { desc: "Chubascos violentos", icon: "cloud-lightning" },
        95: { desc: "Tormenta eléctrica", icon: "cloud-lightning" },
        96: { desc: "Tormenta con granizo leve", icon: "cloud-lightning" },
        99: { desc: "Tormenta con granizo fuerte", icon: "cloud-lightning" }
    },

    getWmoInfo(code) {
        return this.wmoCodeMap[code] || { desc: "Variable", icon: "cloud" };
    },

    async fetchWeather(force = false) {
        const now = Date.now();
        if (!force && this.data && (now - this.lastFetch < 30 * 60 * 1000)) {
            this.render();
            return;
        }

        try {
            const url = `https://api.open-meteo.com/v1/forecast?latitude=${this.location.lat}&longitude=${this.location.lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max&timezone=auto&forecast_days=3`;
            const resp = await fetch(url);
            if (!resp.ok) throw new Error("Error HTTP " + resp.status);
            this.data = await resp.json();
            this.lastFetch = now;
            this.render();
            this.evaluarSugerenciaRetrasoLluvia();
        } catch (e) {
            console.warn("[WEATHER] Error obteniendo clima:", e);
            const descEl = document.getElementById('weather-condition-desc');
            if (descEl) descEl.textContent = "Clima no disponible";
        }
    },

    render() {
        if (!this.data || !this.data.current) return;
        const current = this.data.current;
        const daily = this.data.daily;

        // Badge y ubicación
        const badge = document.getElementById('weather-location-badge');
        if (badge) badge.textContent = this.location.name;

        // Settings info
        const setLocName = document.getElementById('settings-location-name');
        const setLocCoords = document.getElementById('settings-location-coords');
        if (setLocName) setLocName.textContent = this.location.name;
        if (setLocCoords) setLocCoords.textContent = `${this.location.lat.toFixed(4)}°, ${this.location.lon.toFixed(4)}°`;

        // Datos actuales
        const tempEl = document.getElementById('weather-current-temp');
        if (tempEl) tempEl.textContent = `${Math.round(current.temperature_2m)}°C`;

        const humEl = document.getElementById('weather-current-humidity');
        if (humEl) humEl.textContent = `${current.relative_humidity_2m}%`;

        const wmoInfo = this.getWmoInfo(current.weather_code);
        const descEl = document.getElementById('weather-condition-desc');
        if (descEl) descEl.textContent = wmoInfo.desc;

        const iconEl = document.getElementById('weather-current-icon');
        if (iconEl) iconEl.setAttribute('data-lucide', wmoInfo.icon);

        // Renderizar mini-pronóstico 3 días
        const forecastCont = document.getElementById('weather-forecast-container');
        if (forecastCont && daily && daily.time) {
            forecastCont.innerHTML = '';
            const diasSemana = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];

            for (let i = 0; i < Math.min(3, daily.time.length); i++) {
                const dateObj = new Date(daily.time[i] + 'T00:00:00');
                const diaNombre = i === 0 ? 'Hoy' : diasSemana[dateObj.getDay()];
                const dayWmo = this.getWmoInfo(daily.weather_code[i]);
                const maxT = Math.round(daily.temperature_2m_max[i]);
                const minT = Math.round(daily.temperature_2m_min[i]);
                const rainProb = daily.precipitation_probability_max ? daily.precipitation_probability_max[i] : 0;
                const rainMm = daily.precipitation_sum ? daily.precipitation_sum[i] : 0;

                const dayCard = document.createElement('div');
                dayCard.className = `p-2 rounded-xl transition ${i === 0 ? 'bg-slate-100/90 dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700/60' : 'bg-slate-50/50 dark:bg-slate-900/40'}`;
                dayCard.innerHTML = `
                    <p class="text-[10px] font-bold uppercase text-slate-500 dark:text-slate-400 mb-0.5">${diaNombre}</p>
                    <div class="flex justify-center my-1 text-slate-700 dark:text-slate-300">
                        <i data-lucide="${dayWmo.icon}" class="w-4 h-4"></i>
                    </div>
                    <p class="text-[11px] font-bold text-slate-800 dark:text-slate-100 font-mono">${maxT}° / <span class="text-slate-400 font-normal">${minT}°</span></p>
                    <div class="mt-0.5 flex items-center justify-center gap-0.5 text-[9px] ${rainProb >= 50 || rainMm >= 3 ? 'text-blue-600 dark:text-blue-400 font-bold' : 'text-slate-400 dark:text-slate-500'}">
                        <i data-lucide="droplet" class="w-2.5 h-2.5"></i>
                        <span>${rainProb}%</span>
                        ${rainMm > 0 ? `<span class="text-[8px] opacity-80">(${rainMm}mm)</span>` : ''}
                    </div>
                `;
                forecastCont.appendChild(dayCard);
            }
        }

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    },

    evaluarSugerenciaRetrasoLluvia() {
        if (!this.smartDelayEnabled) return;
        const banner = document.getElementById('smart-rain-delay-card');
        if (!banner) return;

        // Si ya hay un retraso por lluvia activo en el dispositivo, no mostrar sugerencia
        if (state.telemetria && (state.telemetria.retraso_lluvia || state.telemetria.sensor_lluvia_pausa)) {
            banner.classList.add('hidden');
            return;
        }

        if (!this.data || !this.data.daily) return;
        const daily = this.data.daily;

        let maxRainMm = 0;
        let maxProb = 0;
        let diaLluvia = "próximamente";

        for (let i = 0; i < Math.min(2, daily.time.length); i++) {
            const mm = daily.precipitation_sum ? daily.precipitation_sum[i] : 0;
            const prob = daily.precipitation_probability_max ? daily.precipitation_probability_max[i] : 0;
            if (mm > maxRainMm) {
                maxRainMm = mm;
                diaLluvia = i === 0 ? "hoy" : "mañana";
            }
            if (prob > maxProb) maxProb = prob;
        }

        if (maxRainMm >= 4 || maxProb >= 70) {
            const titleEl = document.getElementById('smart-rain-delay-title');
            const descEl = document.getElementById('smart-rain-delay-desc');

            if (titleEl) titleEl.textContent = `🌧️ Lluvia prevista para ${diaLluvia} (${maxRainMm > 0 ? maxRainMm + ' mm' : maxProb + '% prob.'})`;
            if (descEl) descEl.textContent = `Se pronostican precipitaciones. ¿Deseas pausar el riego por 24 horas para ahorrar agua?`;

            banner.classList.remove('hidden');
            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                window.lucide.createIcons();
            }
        } else {
            banner.classList.add('hidden');
        }
    },

    async detectarGPS() {
        if (!('geolocation' in navigator)) {
            showToast("Tu navegador no soporta geolocalización.");
            return;
        }

        showToast("Detectando ubicación GPS...");
        navigator.geolocation.getCurrentPosition(async (pos) => {
            const lat = pos.coords.latitude;
            const lon = pos.coords.longitude;
            
            let nombreCiudad = `${lat.toFixed(2)}°, ${lon.toFixed(2)}°`;
            try {
                const geoResp = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&zoom=10`);
                if (geoResp.ok) {
                    const geoData = await geoResp.json();
                    nombreCiudad = geoData.address.city || geoData.address.town || geoData.address.village || geoData.address.county || nombreCiudad;
                }
            } catch (e) {
                console.log("[WEATHER] No se pudo obtener nombre de ciudad:", e);
            }

            this.guardarUbicacion(lat, lon, nombreCiudad);
            showToast(`Ubicación establecida: ${nombreCiudad}`);
            await this.fetchWeather(true);
        }, (err) => {
            console.warn("[GPS] Error:", err);
            showToast("No se pudo obtener ubicación GPS (permiso denegado).");
        }, { timeout: 10000 });
    },

    async buscarCiudad(query) {
        if (!query || query.trim().length < 2) return [];
        try {
            const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query.trim())}&count=5&language=es&format=json`;
            const resp = await fetch(url);
            if (!resp.ok) return [];
            const data = await resp.json();
            return data.results || [];
        } catch (e) {
            console.error("[GEOCODING] Error:", e);
            return [];
        }
    },

    guardarUbicacion(lat, lon, name) {
        this.location = { lat: parseFloat(lat), lon: parseFloat(lon), name: name };
        localStorage.setItem('WEATHER_LOC_LAT', lat);
        localStorage.setItem('WEATHER_LOC_LON', lon);
        localStorage.setItem('WEATHER_LOC_NAME', name);

        if (currentUser && db) {
            db.collection('usuarios').doc(currentUser.uid).set({
                ubicacion_clima: { lat, lon, name, updatedAt: firebase.firestore.FieldValue.serverTimestamp() }
            }, { merge: true }).catch(err => console.warn("[WEATHER] Error guardando en Firestore:", err));
        }
    }
};

window.seleccionarCiudadBuscada = (lat, lon, name) => {
    weatherService.guardarUbicacion(lat, lon, name);
    document.getElementById('city-search-results')?.classList.add('hidden');
    const inputCity = document.getElementById('input-search-city');
    if (inputCity) inputCity.value = '';
    showToast(`Ubicación seleccionada: ${name}`);
    weatherService.fetchWeather(true);
};

// ==========================================
// 📅 MÓDULO DE CALENDARIO SEMANAL
// ==========================================
const weeklyCalendarService = {
    render() {
        const grid = document.getElementById('weekly-calendar-grid');
        const summaryTotal = document.getElementById('calendar-summary-total');
        if (!grid) return;

        grid.innerHTML = '';
        const diasNombres = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
        const diasIndices = [1, 2, 3, 4, 5, 6, 7];

        const hoy = new Date();
        const diaHoyJs = hoy.getDay();
        const diaHoyIndex = diaHoyJs === 0 ? 7 : diaHoyJs;

        const programas = state.deviceConfig.programas || {};

        // Calcular factor estacional según fecha actual o global
        let factorEstacional = 1.0;
        const temporadasEst = state.deviceConfig.ajustes_estacionales || [];
        if (temporadasEst.length > 0) {
            const mm = String(hoy.getMonth() + 1).padStart(2, '0');
            const dd = String(hoy.getDate()).padStart(2, '0');
            const mmdd = `${mm}-${dd}`;
            for (const temp of temporadasEst) {
                const ini = temp.inicio || '01-01';
                const fin = temp.fin || '12-31';
                let enRango = false;
                if (ini <= fin) {
                    enRango = (mmdd >= ini && mmdd <= fin);
                } else {
                    enRango = (mmdd >= ini || mmdd <= fin);
                }
                if (enRango) {
                    factorEstacional = (temp.porcentaje || 100) / 100;
                    break;
                }
            }
        } else if (state.deviceConfig.ajuste_estacional) {
            factorEstacional = state.deviceConfig.ajuste_estacional / 100;
        }

        let totalMinutosSemana = 0;

        diasIndices.forEach((diaNum, idx) => {
            const esHoy = (diaNum === diaHoyIndex);
            const nombreDia = diasNombres[idx];

            const runsDelDia = [];

            for (const [progKey, prog] of Object.entries(programas)) {
                if (!prog) continue;

                // Verificar si el programa está activo
                const estaActivo = (prog.activo === true || prog.activo === 1 || prog.activo === "true");
                if (!estaActivo) continue;

                // Obtener array de días
                const rawDias = prog.dias_semana || prog.dias || [];
                const diasProg = Array.isArray(rawDias) ? rawDias.map(Number) : [];
                const correEsteDia = diasProg.includes(diaNum);

                // Obtener array de horas de arranque
                const horasArranque = prog.horas_arranque || prog.horarios || prog.horas || [];

                if (correEsteDia && Array.isArray(horasArranque) && horasArranque.length > 0) {
                    let duracionTotalMinProg = 0;
                    const zonasInfo = [];

                    if (prog.zonas && typeof prog.zonas === 'object') {
                        for (const [zKey, zVal] of Object.entries(prog.zonas)) {
                            let mins = 0;
                            if (typeof zVal === 'number') {
                                mins = zVal;
                            } else if (zVal && typeof zVal === 'object') {
                                mins = parseInt(zVal.minutos) || 0;
                            }
                            if (mins > 0) {
                                const minutosAjustados = Math.max(1, Math.round(mins * factorEstacional));
                                duracionTotalMinProg += minutosAjustados;
                                zonasInfo.push({
                                    zona: zKey,
                                    nombre: obtenerNombreZona(zKey),
                                    duracion: minutosAjustados
                                });
                            }
                        }
                    }

                    if (duracionTotalMinProg > 0) {
                        horasArranque.forEach(hora => {
                            if (hora) {
                                runsDelDia.push({
                                    progKey: progKey,
                                    progNombre: prog.nombre || `Prog ${progKey}`,
                                    horaInicio: hora,
                                    duracionTotal: duracionTotalMinProg,
                                    zonas: zonasInfo
                                });
                                totalMinutosSemana += duracionTotalMinProg;
                            }
                        });
                    }
                }
            }

            runsDelDia.sort((a, b) => a.horaInicio.localeCompare(b.horaInicio));

            const dayCard = document.createElement('div');
            dayCard.className = `p-3.5 rounded-2xl border transition-all ${
                esHoy 
                    ? 'bg-teal-500/10 border-teal-500/40 dark:border-teal-400/40 shadow-sm' 
                    : 'bg-slate-50 dark:bg-slate-900/50 border-slate-200/80 dark:border-slate-800'
            }`;

            let contenidoRuns = '';
            if (runsDelDia.length === 0) {
                contenidoRuns = `<p class="text-xs text-slate-400 italic py-1">Sin riegos programados</p>`;
            } else {
                contenidoRuns = runsDelDia.map(run => {
                    const zonesBadges = run.zonas.map(z => 
                        `<span class="px-1.5 py-0.5 rounded bg-slate-200/80 dark:bg-slate-700 text-[10px] text-slate-700 dark:text-slate-300 font-medium">${z.nombre}: ${z.duracion}m</span>`
                    ).join(' ');

                    return `
                        <div class="p-2.5 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700/80 flex flex-col gap-1.5 mb-2 last:mb-0 shadow-sm">
                            <div class="flex items-center justify-between">
                                <div class="flex items-center gap-1.5">
                                    <span class="w-5 h-5 rounded-full bg-teal-100 dark:bg-teal-900/60 text-teal-700 dark:text-teal-300 text-[10px] font-bold flex items-center justify-center">${run.progKey}</span>
                                    <span class="text-xs font-bold text-slate-800 dark:text-slate-200">${run.progNombre}</span>
                                </div>
                                <div class="flex items-center gap-1 text-xs font-mono font-bold text-teal-600 dark:text-teal-400 bg-teal-50 dark:bg-teal-950/60 px-2 py-0.5 rounded-md border border-teal-500/20">
                                    <i data-lucide="clock" class="w-3 h-3"></i>
                                    <span>${run.horaInicio}</span>
                                    <span class="text-[10px] font-normal text-slate-400">(${run.duracionTotal} min)</span>
                                </div>
                            </div>
                            <div class="flex flex-wrap gap-1 mt-0.5">
                                ${zonesBadges}
                            </div>
                        </div>
                    `;
                }).join('');
            }

            dayCard.innerHTML = `
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="text-xs font-bold uppercase tracking-wider ${esHoy ? 'text-teal-600 dark:text-teal-400' : 'text-slate-700 dark:text-slate-300'}">${nombreDia}</span>
                        ${esHoy ? '<span class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-teal-600 text-white shadow-sm">HOY</span>' : ''}
                    </div>
                    <span class="text-[11px] font-semibold text-slate-500 dark:text-slate-400">
                        ${runsDelDia.length > 0 ? `${runsDelDia.reduce((acc, r) => acc + r.duracionTotal, 0)} min total` : '0 min'}
                    </span>
                </div>
                <div class="space-y-1.5">
                    ${contenidoRuns}
                </div>
            `;

            grid.appendChild(dayCard);
        });

        if (summaryTotal) {
            summaryTotal.textContent = `${totalMinutosSemana} min / sem`;
        }

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }
};

// ==========================================
// 💾 MÓDULO DE PRESETS Y RESPALDO JSON
// ==========================================
const presetsService = {
    presets: [],

    init() {
        this.cargarPresetsLocales();
        if (currentUser && db) {
            this.cargarPresetsFirestore();
        }
        this.bindEvents();
    },

    cargarPresetsLocales() {
        const data = localStorage.getItem('PRESETS_RIEGO');
        if (data) {
            try {
                this.presets = JSON.parse(data);
            } catch (e) {
                this.presets = [];
            }
        }
        this.render();
    },

    async cargarPresetsFirestore() {
        if (!currentUser || !db) return;
        try {
            const snap = await db.collection('usuarios').doc(currentUser.uid).collection('presets').orderBy('creadoEn', 'desc').get();
            const firestorePresets = [];
            snap.forEach(doc => {
                firestorePresets.push({ id: doc.id, ...doc.data() });
            });
            if (firestorePresets.length > 0) {
                this.presets = firestorePresets;
                localStorage.setItem('PRESETS_RIEGO', JSON.stringify(this.presets));
            }
            this.render();
        } catch (e) {
            console.warn("[PRESETS] Error cargando de Firestore:", e);
        }
    },

    async guardarPresetActual(nombre) {
        if (!nombre || !nombre.trim()) {
            showToast("Ingresá un nombre para el perfil.");
            return;
        }

        const nuevoPreset = {
            id: 'preset_' + Date.now(),
            nombre: nombre.trim(),
            creadoEn: Date.now(),
            programas: JSON.parse(JSON.stringify(state.deviceConfig.programas || {})),
            ajuste_estacional: state.deviceConfig.ajuste_estacional || 100,
            ajustes_estacionales: JSON.parse(JSON.stringify(state.deviceConfig.ajustes_estacionales || []))
        };

        this.presets.unshift(nuevoPreset);
        localStorage.setItem('PRESETS_RIEGO', JSON.stringify(this.presets));
        this.render();

        if (currentUser && db) {
            try {
                await db.collection('usuarios').doc(currentUser.uid).collection('presets').doc(nuevoPreset.id).set(nuevoPreset);
            } catch (e) {
                console.warn("[PRESETS] Error guardando en Firestore:", e);
            }
        }

        showToast(`✓ Perfil "${nuevoPreset.nombre}" guardado.`);
    },

    async aplicarPreset(presetId) {
        const preset = this.presets.find(p => p.id === presetId);
        if (!preset) return;

        showGenericModal({
            title: `Cargar Perfil "${preset.nombre}"`,
            msg: `¿Deseas aplicar los programas de este perfil al regador? Se actualizarán en el equipo.`,
            onOk: async () => {
                state.deviceConfig.programas = JSON.parse(JSON.stringify(preset.programas));
                if (preset.ajuste_estacional !== undefined) {
                    state.deviceConfig.ajuste_estacional = preset.ajuste_estacional;
                }
                if (preset.ajustes_estacionales) {
                    state.deviceConfig.ajustes_estacionales = JSON.parse(JSON.stringify(preset.ajustes_estacionales));
                }

                refreshUIFromConfig();
                weeklyCalendarService.render();

                await this.sincronizarProgramasAlEquipo();
                showToast(`✓ Perfil "${preset.nombre}" aplicado.`);
            }
        });
    },

    async eliminarPreset(presetId) {
        const preset = this.presets.find(p => p.id === presetId);
        const nombre = preset ? preset.nombre : '';

        showGenericModal({
            title: `Eliminar Perfil`,
            msg: `¿Estás seguro de eliminar el perfil "${nombre}"?`,
            onOk: async () => {
                this.presets = this.presets.filter(p => p.id !== presetId);
                localStorage.setItem('PRESETS_RIEGO', JSON.stringify(this.presets));
                this.render();

                if (currentUser && db) {
                    try {
                        await db.collection('usuarios').doc(currentUser.uid).collection('presets').doc(presetId).delete();
                    } catch (e) {
                        console.warn("[PRESETS] Error eliminando en Firestore:", e);
                    }
                }
                showToast(`Perfil eliminado.`);
            }
        });
    },

    async sincronizarProgramasAlEquipo() {
        if (state.deviceConfig.programas) {
            for (const [pId, pData] of Object.entries(state.deviceConfig.programas)) {
                sendCmd({
                    comando: "UPDATE_PROGRAMA",
                    prog_id: pId,
                    prog_data: pData
                });
                await new Promise(r => setTimeout(r, 150));
            }
        }
        sendCmd({
            comando: "UPDATE_CONFIG",
            config: {
                ajuste_estacional: state.deviceConfig.ajuste_estacional || 100,
                ajustes_estacionales: state.deviceConfig.ajustes_estacionales || []
            }
        });
    },

    exportarJSON() {
        const exportData = {
            version_backup: "1.0",
            fecha: new Date().toISOString(),
            chipId: state.chipId,
            deviceConfig: state.deviceConfig,
            presets: this.presets
        };

        const jsonStr = JSON.stringify(exportData, null, 2);
        const blob = new Blob([jsonStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const fechaStr = new Date().toISOString().split('T')[0];
        a.href = url;
        a.download = `respaldo_riego_smart_${fechaStr}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast("✓ Archivo de respaldo exportado.");
    },

    importarJSON(file) {
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async (e) => {
            try {
                const data = JSON.parse(e.target.result);
                if (!data.deviceConfig && !data.programas) {
                    showToast("Formato de archivo inválido.");
                    return;
                }

                showGenericModal({
                    title: "Restaurar Copia de Seguridad",
                    msg: "¿Deseas cargar y sincronizar esta copia de seguridad en el regador?",
                    onOk: async () => {
                        if (data.deviceConfig) {
                            state.deviceConfig = Object.assign(state.deviceConfig, data.deviceConfig);
                        } else if (data.programas) {
                            state.deviceConfig.programas = data.programas;
                        }

                        if (Array.isArray(data.presets)) {
                            this.presets = data.presets;
                            localStorage.setItem('PRESETS_RIEGO', JSON.stringify(this.presets));
                        }

                        refreshUIFromConfig();
                        weeklyCalendarService.render();
                        this.render();

                        await this.sincronizarProgramasAlEquipo();
                        showToast("✓ Copia de seguridad restaurada correctamente.");
                    }
                });
            } catch (err) {
                console.error("[IMPORT] Error:", err);
                showToast("Error al leer el archivo JSON.");
            }
        };
        reader.readAsText(file);
    },

    render() {
        const container = document.getElementById('presets-list-container');
        if (!container) return;

        if (this.presets.length === 0) {
            container.innerHTML = `<div class="text-xs text-slate-400 text-center py-4">No hay perfiles guardados aún.</div>`;
            return;
        }

        container.innerHTML = this.presets.map(preset => {
            const fecha = new Date(preset.creadoEn).toLocaleDateString();
            const progsCount = preset.programas ? Object.keys(preset.programas).length : 0;

            return `
                <div class="p-3 rounded-xl bg-white dark:bg-slate-800/90 border border-slate-200 dark:border-slate-700/80 flex items-center justify-between gap-2 shadow-sm">
                    <div>
                        <p class="text-xs font-bold text-slate-800 dark:text-slate-100">${preset.nombre}</p>
                        <p class="text-[10px] text-slate-400">${fecha} • ${progsCount} programas • Ajuste: ${preset.ajuste_estacional || 100}%</p>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <button onclick="presetsService.aplicarPreset('${preset.id}')" class="px-2.5 py-1 bg-teal-600 hover:bg-teal-500 text-white rounded-lg text-xs font-semibold transition shadow-sm flex items-center gap-1">
                            <i data-lucide="play" class="w-3 h-3"></i> Cargar
                        </button>
                        <button onclick="presetsService.eliminarPreset('${preset.id}')" class="p-1 text-slate-400 hover:text-red-500 dark:hover:text-red-400 transition" title="Eliminar">
                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    },

    bindEvents() {
        document.getElementById('btn-save-preset')?.addEventListener('click', () => {
            const input = document.getElementById('input-preset-name');
            if (input) {
                this.guardarPresetActual(input.value);
                input.value = '';
            }
        });

        document.getElementById('btn-export-json')?.addEventListener('click', () => {
            this.exportarJSON();
        });

        document.getElementById('input-import-json')?.addEventListener('change', (e) => {
            if (e.target.files && e.target.files[0]) {
                this.importarJSON(e.target.files[0]);
                e.target.value = '';
            }
        });
    }
};
