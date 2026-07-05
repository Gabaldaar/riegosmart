// comms.js - Comunicación Dual (MQTT y BLE)

const BLE_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E".toLowerCase();
const BLE_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E".toLowerCase();
const BLE_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E".toLowerCase();

class CommsManager {
    constructor() {
        this.mode = 'DISCONNECTED'; // 'MQTT', 'BLE', 'DISCONNECTED'
        
        // BLE state
        this.bleDevice = null;
        this.bleServer = null;
        this.bleRxChar = null;
        this.bleTxChar = null;
        this.bleBuffer = "";
        
        // MQTT state
        this.mqttClient = null;
        this.mqttTopic = null;
        
        // Callbacks
        this.onMessage = null;
        
        this.txQueue = [];
        this.isTransmitting = false;

        this.onConnectionChange = null;
    }

    async hashSHA256(message) {
        const msgBuffer = new TextEncoder().encode(message);
        const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
        return hashHex;
    }

    async initConnection(chipId, token) {
        if (!chipId || !token) {
            console.warn("No hay credenciales locales. Debe usar BLE primero.");
            return false;
        }

        // 1. Intentar MQTT
        this.notifyStatus('CONNECTING_MQTT');
        try {
            const connected = await this.connectMQTT(chipId, token);
            if (connected) {
                this.mode = 'MQTT';
                this.notifyStatus('MQTT');
                return true;
            }
        } catch (e) {
            console.warn("Fallo MQTT, cambiando a BLE...", e);
        }

        // Si falla MQTT, notificar que debe usar BLE
        this.mode = 'DISCONNECTED';
        this.notifyStatus('DISCONNECTED');
        return false;
    }

    // ==========================================
    // MQTT LOGIC
    // ==========================================
    connectMQTT(chipId, token) {
        return new Promise(async (resolve, reject) => {
            const hash = await this.hashSHA256(chipId + token);
            this.mqttTopic = `riego/${hash}/cmd`;
            const clientId = 'pwa_' + Math.random().toString(16).substr(2, 8);
            
            // Timeout ampliado a 15 segundos para MQTT (HiveMQ por WSS suele tardar en redes móviles)
            const timeout = setTimeout(() => {
                if (this.mqttClient) this.mqttClient.end();
                reject(new Error("MQTT Timeout"));
            }, 15000);

            // Conectar via WebSockets
            this.mqttClient = mqtt.connect('wss://broker.hivemq.com:8884/mqtt', {
                clientId: clientId,
                clean: true
            });

            this.mqttClient.on('connect', () => {
                clearTimeout(timeout);
                const dbg = document.getElementById('debug-banner');
                if (dbg) dbg.textContent = "DEBUG: Conectado a broker. Suscribiendo...";
                
                this.mqttClient.subscribe(`riego/${hash}/telemetry`, (err) => {
                    if (dbg) {
                        if (err) dbg.textContent = "DEBUG: Error suscribiendo " + err.message;
                        else dbg.textContent = "DEBUG: Suscrito OK a " + `riego/${hash.substring(0,6)}...`;
                    }
                });
                resolve(true);
            });

            this.mqttClient.on('message', (topic, message) => {
                const msgStr = message.toString();
                const dbg = document.getElementById('debug-banner');
                if (dbg) {
                    dbg.textContent = "COMMS.JS RX: " + msgStr.substring(0, 30);
                    dbg.classList.replace("bg-orange-600", "bg-purple-600");
                }
                
                try {
                    const jsonObj = JSON.parse(msgStr);
                    if (this.onMessage) {
                        this.onMessage(jsonObj);
                    }
                } catch (e) {
                    console.error("MQTT Parse error:", e);
                }
            });

            this.mqttClient.on('error', (err) => {
                clearTimeout(timeout);
                reject(err);
            });
            
            this.mqttClient.on('close', () => {
                if (this.mode === 'MQTT') {
                    this.mode = 'DISCONNECTED';
                    this.notifyStatus('DISCONNECTED');
                }
            });
        });
    }

    // ==========================================
    // BLE LOGIC
    // ==========================================
    async connectBLE() {
        try {
            this.notifyStatus('CONNECTING_BLE');
            this.bleDevice = await navigator.bluetooth.requestDevice({
                filters: [{ namePrefix: 'Riego' }, { namePrefix: 'Dosimat' }],
                optionalServices: [BLE_SERVICE_UUID]
            });

            this.bleDevice.addEventListener('gattserverdisconnected', () => {
                this.mode = 'DISCONNECTED';
                this.notifyStatus('DISCONNECTED');
            });

            this.bleServer = await this.bleDevice.gatt.connect();
            const service = await this.bleServer.getPrimaryService(BLE_SERVICE_UUID);
            
            this.bleRxChar = await service.getCharacteristic(BLE_RX_UUID);
            this.bleTxChar = await service.getCharacteristic(BLE_TX_UUID);

            await this.bleTxChar.startNotifications();
            this.bleTxChar.addEventListener('characteristicvaluechanged', this.handleBLEReceive.bind(this));

            this.mode = 'BLE';
            this.notifyStatus('BLE');
            
            // Extraer chip ID del nombre (Ej: Riego_A1B2)
            const nameParts = this.bleDevice.name.split('_');
            const chipId = nameParts.length > 1 ? nameParts[1] : this.bleDevice.id;

            return { success: true, chipId: chipId };
        } catch (error) {
            console.error("Error BLE:", error);
            this.mode = 'DISCONNECTED';
            this.notifyStatus('DISCONNECTED');
            return { success: false, error };
        }
    }

    handleBLEReceive(event) {
        const value = event.target.value;
        const decoder = new TextDecoder('utf-8');
        this.bleBuffer += decoder.decode(value);

        let newlineIndex;
        while ((newlineIndex = this.bleBuffer.indexOf('\n')) !== -1) {
            const line = this.bleBuffer.slice(0, newlineIndex).trim();
            this.bleBuffer = this.bleBuffer.slice(newlineIndex + 1);
            if (line) {
                try {
                    const data = JSON.parse(line);
                    if (this.onMessage) this.onMessage(data);
                } catch (e) {
                    console.error("BLE Parse error:", e);
                }
            }
        }
    }

    // ==========================================
    // SEND LOGIC
    // ==========================================
    async sendCommand(cmdObject, token) {
        return new Promise((resolve) => {
            this.txQueue.push({ cmdObject, token, resolve });
            this.processTxQueue();
        });
    }

    async processTxQueue() {
        if (this.isTransmitting || this.txQueue.length === 0) return;
        this.isTransmitting = true;

        while (this.txQueue.length > 0) {
            const { cmdObject, token, resolve } = this.txQueue.shift();
            cmdObject.token = token;
            const jsonStr = JSON.stringify(cmdObject) + "\n";

            try {
                if (this.mode === 'MQTT' && this.mqttClient) {
                    this.mqttClient.publish(this.mqttTopic, jsonStr, { qos: 1 });
                    resolve(true);
                } else if (this.mode === 'BLE' && this.bleRxChar) {
                    const encoder = new TextEncoder();
                    const data = encoder.encode(jsonStr);
                    const CHUNK_SIZE = 20;
                    for (let i = 0; i < data.length; i += CHUNK_SIZE) {
                        const chunk = data.slice(i, i + CHUNK_SIZE);
                        await this.bleRxChar.writeValueWithResponse(chunk);
                    }
                    resolve(true);
                } else {
                    console.warn("No hay conexión activa para enviar comando.");
                    resolve(false);
                }
            } catch (e) {
                console.error("TX Error:", e);
                resolve(false);
            }
        }
        
        this.isTransmitting = false;
    }

    disconnect() {
        if (this.mqttClient) {
            this.mqttClient.end();
            this.mqttClient = null;
        }
        if (this.bleDevice && this.bleDevice.gatt.connected) {
            this.bleDevice.gatt.disconnect();
        }
        this.mode = 'DISCONNECTED';
        this.notifyStatus('DISCONNECTED');
    }

    notifyStatus(status) {
        if (this.onConnectionChange) {
            this.onConnectionChange(status);
        }
    }
}

const comms = new CommsManager();
