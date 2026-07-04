// app.js - UI Controller

// Estado global de la App
const state = {
    chipId: localStorage.getItem('CHIP_ID') || null,
    token: localStorage.getItem('TOKEN') || null,
    deviceConfig: {
        max_zonas: 4,
        modo_bomba: true,
        ajuste_estacional: 100,
        nombres_zonas: { "1":"Zona 1", "2":"Zona 2", "3":"Zona 3", "4":"Zona 4" },
        programas: {}
    },
    activeProgTab: 'A',
    tempProgData: {}, // Datos del programa que se está editando
    hasUnsavedChanges: false
};

// ==========================================
// INICIALIZACIÓN
// ==========================================
document.addEventListener("DOMContentLoaded", async () => {
    initTabs();
    initSettingsUI();
    initSchedulerUI();
    initHelpModals();
    
    // Cargar UI inicial con config por defecto para evitar vacío
    refreshUIFromConfig();
    
    // Bindings de comunicación
    comms.onConnectionChange = updateConnectionUI;
    comms.onMessage = handleIncomingMessage;

    // Intentar inicio dual
    if (state.chipId && state.token) {
        await comms.initConnection(state.chipId, state.token);
            setTimeout(() => {
                sendCmd({comando: "GET_CONFIG"});
                sendCmd({comando: "GET_STATE"});
                sendCmd({comando: "GET_TEMP"});
            }, 1000);
    } else {
        // Obliga a configurar por BLE primero llevandolo a ajustes
        document.querySelector('.nav-btn[data-target="view-settings"]').click();
    }
    
    startHeaderTimers();
    startLEDSimulator();
});

// ==========================================
// INTERFAZ GENERAL (TABS & HEADER)
// ==========================================
function initTabs() {
    const navBtns = document.querySelectorAll('.nav-btn');
    const views = document.querySelectorAll('.tab-view');

    navBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.getAttribute('data-target');
            
            // Actualizar botones nav
            navBtns.forEach(b => {
                b.classList.remove('text-teal-400');
                b.classList.add('text-slate-500');
            });
            btn.classList.add('text-teal-400');
            btn.classList.remove('text-slate-500');

            // Mostrar vista
            views.forEach(v => {
                if (v.id === target) {
                    v.classList.remove('hidden');
                } else {
                    v.classList.add('hidden');
                }
            });

            // Si es historial, pedimos logs
            if (target === 'view-history') {
                requestLogs();
            }
        });
    });
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
        statusText.textContent = `WiFi (${netName})`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-green-900/40 text-green-400 rounded-full truncate max-w-[100px]";
        iconDiv.innerHTML = '<i data-lucide="wifi" class="w-5 h-5 text-green-400"></i>';
        btnReconnect.classList.add('hidden');
    } else if (status === 'BLE') {
        statusText.textContent = `Bluetooth`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-blue-900/40 text-blue-400 rounded-full truncate max-w-[100px]";
        iconDiv.innerHTML = '<i data-lucide="bluetooth" class="w-5 h-5 text-blue-400"></i>';
        btnReconnect.classList.add('hidden');
    } else if (status === 'CONNECTING_MQTT' || status === 'CONNECTING_BLE') {
        iconDiv.innerHTML = '<i data-lucide="loader-2" class="w-5 h-5 text-yellow-400 animate-spin"></i>';
        statusText.textContent = "Conectando...";
        statusText.className = "text-[10px] font-medium px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 truncate max-w-[100px]";
        btnReconnect.classList.add('hidden');
    } else {
        statusText.textContent = `Desconectado`;
        statusText.className = "text-[10px] font-medium px-2 py-1 bg-red-900/40 text-red-400 rounded-full truncate max-w-[100px]";
        iconDiv.innerHTML = '<i data-lucide="wifi-off" class="w-5 h-5 text-red-400"></i>';
        btnReconnect.classList.remove('hidden');
    }
    lucide.createIcons();
}

document.getElementById('btn-reconnect').addEventListener('click', () => {
    if (state.chipId && state.token) {
        comms.initConnection(state.chipId, state.token);
    } else {
        document.querySelector('.nav-btn[data-target="view-settings"]').click();
    }
});

// ==========================================
// RECEPCIÓN DE MENSAJES Y TELEMETRIA
// ==========================================
function handleIncomingMessage(msg) {
    const dbg = document.getElementById('debug-banner');
    if (dbg) {
        dbg.textContent = "DEBUG: Mensaje recibido tipo " + (msg ? msg.tipo : "undefined") + " a las " + new Date().toLocaleTimeString();
        dbg.classList.remove("bg-orange-600");
        dbg.classList.add("bg-green-600");
    }

    if (!msg || !msg.tipo) return;

    if (msg.tipo === "CONFIG") {
        state.deviceConfig = { ...state.deviceConfig, ...msg.data };
        refreshUIFromConfig();
    } else if (msg.tipo === "TELEMETRIA") {
        updateActiveWidget(msg.data);
    } else if (msg.tipo === "LOGS") {
        renderLogs(msg.data);
    } else if (msg.tipo === "TEMP") {
        const tempEl = document.getElementById('header-temp');
        if (tempEl && msg.data !== "N/A") {
            tempEl.textContent = `${msg.data}°C`;
        }
    } else if (msg.tipo === "AUTH_ERROR") {
        showGenericModal({
            title: "Error de Conexión",
            msg: "El equipo rechazó la conexión. Token inválido.",
            hideCancel: true
        });
        localStorage.clear();
        location.reload();
    }
}

// Variables globales para el temporizador local
let countdownTimer = null;
let currentRemainingSecs = 0;
let currentTotalSecs = 1;

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

    if (!data.estado || data.estado === "IDLE") {
        badge.textContent = "EN REPOSO";
        badge.className = "px-2.5 py-1 rounded-full text-xs font-bold bg-slate-700 text-slate-300";
        infoPanel.classList.add('hidden');
        document.getElementById('btn-manual-stop').classList.add('hidden');
    } else if (data.estado === "FALLO_CORRIENTE") {
        badge.textContent = "FALLO HARDWARE";
        badge.className = "px-2.5 py-1 rounded-full text-xs font-bold bg-red-900 text-red-300 animate-pulse";
        infoPanel.classList.add('hidden');
        document.getElementById('btn-manual-stop').classList.add('hidden');
    } else {
        badge.textContent = data.estado;
        badge.className = "px-2.5 py-1 rounded-full text-xs font-bold bg-teal-500/20 text-teal-400 border border-teal-500/30";
        infoPanel.classList.remove('hidden');
        document.getElementById('btn-manual-stop').classList.remove('hidden');
        
        let zname = (state.deviceConfig.nombres_zonas && state.deviceConfig.nombres_zonas[data.zona]) ? state.deviceConfig.nombres_zonas[data.zona] : `Zona ${data.zona}`;
        zoneName.textContent = zname;
        
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
        const zName = (state.deviceConfig.nombres_zonas && state.deviceConfig.nombres_zonas[i]) ? state.deviceConfig.nombres_zonas[i] : `Zona ${i}`;
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
                const card = document.createElement('div');
                card.className = "bg-slate-900 border border-slate-700 p-3 rounded-xl";
                card.innerHTML = `
                    <div class="flex justify-between items-center mb-2">
                        <div>
                            <div class="text-sm font-bold text-slate-200">${temp.nombre || 'Temporada'}</div>
                            <div class="text-xs text-slate-400">${temp.inicio} al ${temp.fin}</div>
                        </div>
                        <button class="text-slate-500 hover:text-teal-400 transition-colors btn-edit-season" data-idx="${idx}">
                            <i data-lucide="edit-3" class="w-4 h-4"></i>
                        </button>
                    </div>
                    <div class="flex justify-between items-center mb-1">
                        <span class="text-xs text-slate-400">Porcentaje</span>
                        <span id="pct-val-${idx}" class="text-xs font-bold ${temp.porcentaje !== 100 ? 'text-yellow-500' : 'text-teal-400'}">${temp.porcentaje}%</span>
                    </div>
                    <input type="range" class="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500 season-slider" data-idx="${idx}" min="10" max="250" step="10" value="${temp.porcentaje}">
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
                    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: nuevas } });
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
            
            if (temporadas.length === 4) {
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
    
    // 2.5 Dashboard: Rain Delay Status
    const txtRain = document.getElementById('rain-delay-status');
    if (txtRain && state.deviceConfig.timestamp_rain_delay) {
        const nowSecs = Math.floor(Date.now() / 1000);
        const rainDelayUnix = state.deviceConfig.timestamp_rain_delay + 946684800; // Y2K a Unix
        
        if (rainDelayUnix > nowSecs) {
            const futureDate = new Date(rainDelayUnix * 1000);
            const mm = String(futureDate.getMonth()+1).padStart(2,'0');
            const dd = String(futureDate.getDate()).padStart(2,'0');
            const hh = String(futureDate.getHours()).padStart(2,'0');
            const mn = String(futureDate.getMinutes()).padStart(2,'0');
            txtRain.querySelector('strong').textContent = `${dd}/${mm} a las ${hh}:${mn}`;
            txtRain.classList.remove('hidden');
        } else {
            txtRain.classList.add('hidden');
        }
    }

    // 3. Hardware Settings
    document.querySelectorAll('.hw-zones-btn').forEach(b => {
        if(b.dataset.val == maxZ) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    const modoBomba = state.deviceConfig.modo_bomba;
    document.querySelectorAll('.hw-pump-btn').forEach(b => {
        if(b.dataset.val === String(modoBomba)) {
            b.classList.add('bg-slate-700', 'text-white');
            b.classList.remove('text-slate-400');
        } else {
            b.classList.remove('bg-slate-700', 'text-white');
            b.classList.add('text-slate-400');
        }
    });

    // 4. Scheduler
    loadProgramIntoUI(state.activeProgTab);
    
    // 5. Update Connection Header (to show SSID if now available)
    if (comms.mode) {
        updateConnectionUI(comms.mode);
    }
}

// ==========================================
// DASHBOARD ACTIONS
// ==========================================
document.getElementById('manual-time-slider').addEventListener('input', (e) => {
    document.getElementById('manual-time-val').textContent = e.target.value + " min";
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
    
    sendCmd({
        comando: "RIEGO_MANUAL",
        zonas: { [zona]: { minutos: mins } }
    });
});

document.getElementById('btn-manual-stop').addEventListener('click', () => {
    sendCmd({ comando: "CANCELAR_RIEGO" });
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
        sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: nuevas } });
        
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
    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: defaultSur } });
});

document.getElementById('btn-hemi-norte')?.addEventListener('click', () => {
    const defaultNorte = [
        {"nombre": "Verano", "inicio": "06-21", "fin": "09-20", "porcentaje": 100},
        {"nombre": "Otono", "inicio": "09-21", "fin": "12-20", "porcentaje": 100},
        {"nombre": "Invierno", "inicio": "12-21", "fin": "03-20", "porcentaje": 100},
        {"nombre": "Primavera", "inicio": "03-21", "fin": "06-20", "porcentaje": 100}
    ];
    sendCmd({ comando: "UPDATE_CONFIG", config: { ajustes_estacionales: defaultNorte } });
});

document.querySelectorAll('.btn-rain-delay').forEach(btn => {
    btn.addEventListener('click', () => {
        const dias = parseInt(btn.dataset.days);
        sendCmd({ comando: "RAIN_DELAY", dias: dias });
        setTimeout(() => sendCmd({ comando: "GET_CONFIG" }), 500);
    });
});

document.getElementById('btn-cancel-rain')?.addEventListener('click', () => {
    sendCmd({ comando: "RAIN_DELAY", dias: 0 });
    setTimeout(() => sendCmd({ comando: "GET_CONFIG" }), 500);
});

// ==========================================
// SCHEDULER
// ==========================================
function initSchedulerUI() {
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
            <input type="time" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg p-2 text-sm focus:outline-none focus:border-teal-500 time-input">
            <button class="p-2 text-red-400 hover:bg-red-500/20 rounded-lg transition" onclick="this.parentElement.remove(); updateTempProgData();"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        `;
        cont.appendChild(div);
        lucide.createIcons();
        div.querySelector('input').addEventListener('change', updateTempProgData);
        updateTempProgData();
    });

    // Guardar
    document.getElementById('btn-sync-prog').addEventListener('click', () => {
        updateTempProgData();
        const fullProgObj = { ...state.deviceConfig.programas };
        fullProgObj[state.activeProgTab] = state.tempProgData;
        state.hasUnsavedChanges = false;
        
        sendCmd({
            comando: "UPDATE_CONFIG",
            config: { programas: fullProgObj }
        });
        
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
            <input type="time" value="${t}" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg p-2 text-sm focus:outline-none focus:border-teal-500 time-input">
            <button class="p-2 text-red-400 hover:bg-red-500/20 rounded-lg transition" onclick="this.parentElement.remove(); updateTempProgData();"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        `;
        contTimes.appendChild(div);
        div.querySelector('input').addEventListener('change', updateTempProgData);
    });
    
    // Zonas
    const maxZ = state.deviceConfig.max_zonas || 4;
    const contZones = document.getElementById('prog-zones');
    contZones.innerHTML = '';
    
    for (let i = 1; i <= maxZ; i++) {
        const zName = (state.deviceConfig.nombres_zonas && state.deviceConfig.nombres_zonas[i]) ? state.deviceConfig.nombres_zonas[i] : `Zona ${i}`;
        const zData = prog.zonas[i] || { minutos: 0, cycle_min: 0, soak_min: 0 };
        
        const div = document.createElement('div');
        div.className = "bg-slate-900 border border-slate-700 rounded-xl p-3";
        div.innerHTML = `
            <div class="flex justify-between items-center mb-2">
                <input type="text" class="bg-transparent text-sm font-medium w-1/2 focus:outline-none focus:border-b focus:border-teal-500 z-name-input" data-zidx="${i}" value="${zName}">
                <div class="flex items-center gap-2">
                    <input type="number" min="0" max="240" class="w-16 bg-slate-800 border border-slate-600 rounded p-1 text-center text-sm z-min-input" data-zidx="${i}" value="${zData.minutos}">
                    <span class="text-xs text-slate-400">min</span>
                </div>
            </div>
            <label class="flex items-center gap-2 text-xs text-slate-400 cursor-pointer select-none">
                <input type="checkbox" class="rounded border-slate-600 text-teal-500 bg-slate-800 z-cycle-check" data-zidx="${i}" ${zData.cycle_min > 0 && zData.cycle_min < zData.minutos ? 'checked' : ''}>
                Activar Ciclo y Remojo
            </label>
            <div class="z-cycle-opts mt-2 grid grid-cols-2 gap-2 ${zData.cycle_min > 0 && zData.cycle_min < zData.minutos ? '' : 'hidden'}">
                <div>
                    <span class="text-[10px] text-slate-500">Ciclo (min)</span>
                    <input type="number" min="1" class="w-full bg-slate-800 border border-slate-600 rounded p-1 text-sm z-c-input" data-zidx="${i}" value="${zData.cycle_min || 5}">
                </div>
                <div>
                    <span class="text-[10px] text-slate-500">Remojo (min)</span>
                    <input type="number" min="1" class="w-full bg-slate-800 border border-slate-600 rounded p-1 text-sm z-s-input" data-zidx="${i}" value="${zData.soak_min || 10}">
                </div>
            </div>
        `;
        contZones.appendChild(div);
        
        // Bindings de UI zona
        div.querySelector('.z-name-input').addEventListener('change', (e) => {
            // Actualizar nombre global de zona
            if (!state.deviceConfig.nombres_zonas) state.deviceConfig.nombres_zonas = {};
            state.deviceConfig.nombres_zonas[i] = e.target.value;
            sendCmd({ comando: "UPDATE_CONFIG", config: { nombres_zonas: state.deviceConfig.nombres_zonas } });
            refreshUIFromConfig();
        });
        
        const check = div.querySelector('.z-cycle-check');
        const opts = div.querySelector('.z-cycle-opts');
        check.addEventListener('change', () => {
            if (check.checked) opts.classList.remove('hidden');
            else opts.classList.add('hidden');
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
        const minInp = document.querySelector(`.z-min-input[data-zidx="${i}"]`);
        if(minInp) {
            const mins = parseInt(minInp.value) || 0;
            if(mins > 0) {
                const checked = document.querySelector(`.z-cycle-check[data-zidx="${i}"]`).checked;
                const cInp = document.querySelector(`.z-c-input[data-zidx="${i}"]`);
                const sInp = document.querySelector(`.z-s-input[data-zidx="${i}"]`);
                zonas[i] = {
                    minutos: mins,
                    cycle_min: checked ? (parseInt(cInp.value)||mins) : mins,
                    soak_min: checked ? (parseInt(sInp.value)||0) : 0
                };
            }
        }
        state.tempProgData.zonas[i] = zonas[i];
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
    
    const mesNombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
    
    logsArray.reverse().forEach(log => {
        const div = document.createElement('div');
        div.className = "flex gap-3 text-sm p-3 rounded-xl bg-slate-900 border border-slate-800";
        
        let icon = '<i data-lucide="info" class="w-4 h-4 text-slate-400"></i>';
        if (log.tipo === 'error' || log.tipo === 'alerta') icon = '<i data-lucide="alert-triangle" class="w-4 h-4 text-red-400"></i>';
        else if (log.tipo === 'inicio_prog') icon = '<i data-lucide="play-circle" class="w-4 h-4 text-teal-400"></i>';
        else if (log.tipo === 'fin_prog') icon = '<i data-lucide="check-circle" class="w-4 h-4 text-green-400"></i>';
        else if (log.tipo === 'inicio_zona') icon = '<i data-lucide="droplets" class="w-4 h-4 text-blue-400"></i>';
        
        let desc = log.msg || "";
        if (log.tipo === 'inicio_prog') desc = `Inicio de Programa: ${log.prog}`;
        else if (log.tipo === 'fin_prog') desc = `Fin de Programa: ${log.prog}`;
        else if (log.tipo === 'inicio_zona') desc = `Regando Zona ${log.zona} durante ${log.duracion} min (Prog: ${log.prog})`;
        else if (log.tipo === 'fin_zona') desc = `Fin de riego en Zona ${log.zona}`;
        else if (!desc) desc = JSON.stringify(log);

        let timeStr = "";
        if (log.ts) {
            // Convertir ts (MicroPython Y2K epoch) a JS Date (1970 epoch)
            const date = new Date((log.ts + 946684800) * 1000);
            const d = String(date.getDate()).padStart(2, '0');
            const m = mesNombres[date.getMonth()];
            const y = date.getFullYear();
            const hh = String(date.getHours()).padStart(2, '0');
            const mm = String(date.getMinutes()).padStart(2, '0');
            timeStr = `<span class="text-xs text-slate-500 mr-2">[${d} ${m} ${y} - ${hh}:${mm}]</span>`;
        }

        div.innerHTML = `
            <div class="mt-0.5">${icon}</div>
            <div>
                <span class="font-medium text-slate-300 block mb-0.5">${timeStr}${log.tipo.toUpperCase()}</span>
                <span class="text-slate-400 text-xs">${desc}</span>
            </div>
        `;
        cont.appendChild(div);
    });
    lucide.createIcons();
}

// ==========================================
// SETTINGS & AUTH
// ==========================================
function initSettingsUI() {
    document.querySelectorAll('.hw-zones-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const val = parseInt(btn.dataset.val);
            sendCmd({ comando: "UPDATE_CONFIG", config: { max_zonas: val } });
            state.deviceConfig.max_zonas = val;
            refreshUIFromConfig();
        });
    });
    
    document.querySelectorAll('.hw-pump-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const val = btn.dataset.val === 'true';
            sendCmd({ comando: "UPDATE_CONFIG", config: { modo_bomba: val } });
            state.deviceConfig.modo_bomba = val;
            refreshUIFromConfig();
        });
    });

    document.getElementById('btn-save-hw').addEventListener('click', () => {
        // Los botones individuales ya actualizan en vivo, pero el usuario 
        // necesita confirmación visual o forzar el envío
        sendCmd({ 
            comando: "UPDATE_CONFIG", 
            config: { 
                max_zonas: state.deviceConfig.max_zonas, 
                modo_bomba: state.deviceConfig.modo_bomba 
            } 
        });
        showGenericModal({
            title: "Configuración Guardada",
            msg: "Configuración de hardware guardada en la placa.",
            hideCancel: true
        });
    });

    document.getElementById('btn-ble-pair').addEventListener('click', async () => {
        const res = await comms.connectBLE();
        if (res.success) {
            document.getElementById('ble-info').classList.remove('hidden');
            document.getElementById('ble-device-name').textContent = comms.bleDevice.name;
            document.getElementById('ble-chip-id').textContent = res.chipId;
            
            state.chipId = res.chipId;
            localStorage.setItem('CHIP_ID', res.chipId);
            
            // Ver si falta auth
            if (!state.token) showModalAuth();
            else {
                // Si ya teníamos token, lo mandamos para validar e intentar update
                sendCmd({comando: "GET_CONFIG"});
                sendCmd({comando: "GET_STATE"});
                sendCmd({comando: "GET_TEMP"});
            }
        } else {
            showGenericModal({
                title: "Error de Bluetooth",
                msg: "Fallo la vinculación Bluetooth.",
                hideCancel: true
            });
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
        setTimeout(() => sendCmd({comando: "GET_CONFIG"}), 500);
        
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
        showGenericModal({
            title: "Sincronización de Reloj",
            msg: "Hora sincronizada con el celular.",
            hideCancel: true
        });
    });
    
    document.getElementById('btn-send-wifi').addEventListener('click', () => {
        const s = document.getElementById('wifi-ssid').value;
        const p = document.getElementById('wifi-pass').value;
        if(s) {
            sendCmd({ comando: "config_wifi", ssid: s, pass: p });
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

// Wrapper envio comandos
function sendCmd(obj) {
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
                bodyEl.textContent = helpData[type].body;
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
