const functions = require("firebase-functions");
const admin = require("firebase-admin");

admin.initializeApp();
const db = admin.firestore();

/**
 * Despacha notificaciones push a todos los usuarios vinculados al chipId
 * respetando sus preferencias individuales de notificación.
 */
async function sendPushToDeviceOwners(chipId, notification, eventType) {
  try {
    if (!chipId) return;
    console.log(`[FCM] Preparando notificación para equipo: ${chipId} | Evento: ${eventType}`);

    // 1. Identificar UIDs de usuarios propietarios del chipId
    const targetUids = new Set();

    // A. Buscar en el documento del dispositivo (owner_uid)
    const devDoc = await db.collection("dispositivos").doc(chipId).get();
    if (devDoc.exists && devDoc.data().owner_uid) {
      targetUids.add(devDoc.data().owner_uid);
    }

    // B. Buscar en la colección de usuarios
    const usersSnap = await db.collection("usuarios").where("chipId", "==", chipId).get();
    usersSnap.forEach(uDoc => targetUids.add(uDoc.id));

    if (targetUids.size === 0) {
      console.log(`[FCM] No se encontraron usuarios para el equipo ${chipId}`);
      return;
    }

    // 2. Filtrar por preferencias de usuario y recolectar tokens FCM
    const tokensToSend = [];
    const tokenDocRefs = [];

    for (const uid of targetUids) {
      // Leer preferencias del usuario
      const prefDoc = await db.doc(`usuarios/${uid}/config_notificaciones/actual`).get();
      const prefs = prefDoc.exists ? prefDoc.data() : { notif_activas: true };

      if (prefs.notif_activas === false) {
        console.log(`[FCM] Usuario ${uid} tiene notificaciones desactivadas globalmente.`);
        continue;
      }

      // Validar tipo de evento específico si está configurado
      if (eventType && prefs[eventType] === false) {
        console.log(`[FCM] Usuario ${uid} tiene desactivado el evento: ${eventType}`);
        continue;
      }

      // Recolectar tokens del usuario
      const fcmTokensSnap = await db.collection(`usuarios/${uid}/fcm_tokens`).get();
      fcmTokensSnap.forEach(tDoc => {
        const tokenVal = tDoc.data().token || tDoc.id;
        if (tokenVal && !tokensToSend.includes(tokenVal)) {
          tokensToSend.push(tokenVal);
          tokenDocRefs.push({ ref: tDoc.ref, token: tokenVal });
        }
      });
    }

    if (tokensToSend.length === 0) {
      console.log(`[FCM] No hay tokens FCM registrados para el equipo ${chipId}`);
      return;
    }

    console.log(`[FCM] Enviando push a ${tokensToSend.length} dispositivo(s)...`);

    // 3. Crear payload Multicast
    const messagePayload = {
      tokens: tokensToSend,
      notification: {
        title: notification.title || "Smart Riego",
        body: notification.body || ""
      },
      data: {
        chipId: String(chipId),
        eventType: String(eventType || "general"),
        url: "/"
      },
      webpush: {
        notification: {
          icon: "/icon-512.png",
          badge: "/favicon.ico",
          vibrate: [200, 100, 200],
          tag: String(eventType || "riego-alert")
        },
        fcmOptions: {
          link: "/"
        }
      }
    };

    const response = await admin.messaging().sendEachForMulticast(messagePayload);
    console.log(`[FCM] Resultado despacho: ${response.successCount} exitosos, ${response.failureCount} fallidos.`);

    // 4. Limpieza automática de tokens obsoletos/desinstalados
    if (response.failureCount > 0) {
      response.responses.forEach((resp, idx) => {
        if (!resp.success) {
          const errCode = resp.error ? resp.error.code : "";
          console.warn(`[FCM] Falló token [${idx}]:`, errCode);
          if (
            errCode === "messaging/registration-token-not-registered" ||
            errCode === "messaging/invalid-registration-token"
          ) {
            if (tokenDocRefs[idx] && tokenDocRefs[idx].ref) {
              console.log("[FCM] Eliminando token obsoleto de Firestore...");
              tokenDocRefs[idx].ref.delete().catch(() => {});
            }
          }
        }
      });
    }
  } catch (error) {
    console.error("[FCM] Error general al enviar push:", error);
  }
}

/**
 * Disparador Firestore: Escucha cambios en el estado/telemetría del dispositivo
 */
exports.onDispositivoUpdated = functions.firestore
  .document("dispositivos/{chipId}")
  .onUpdate(async (change, context) => {
    const chipId = context.params.chipId;
    const beforeData = change.before.data() || {};
    const afterData = change.after.data() || {};

    const beforeTele = beforeData.telemetria || {};
    const afterTele = afterData.telemetria || {};

    const estadoAnterior = String(beforeTele.estado || "");
    const estadoNuevo = String(afterTele.estado || "");

    // Si el estado no cambió, no procesar
    if (!estadoNuevo || estadoNuevo === estadoAnterior) return;

    console.log(`[TELEMETRIA] ${chipId} cambió estado: "${estadoAnterior}" -> "${estadoNuevo}"`);

    // 1. Pausa por Sensor de Lluvia
    if (estadoNuevo === "PAUSA: SENSOR" && estadoAnterior !== "PAUSA: SENSOR") {
      await sendPushToDeviceOwners(chipId, {
        title: "🌧️ Sensor de Lluvia Activado",
        body: "El sensor detectó lluvia. El riego se ha pausado automáticamente."
      }, "sensor_lluvia");
    }

    // 2. Pausa por Secado de Sensor
    else if (estadoNuevo === "PAUSA: SECADO" && estadoAnterior !== "PAUSA: SECADO") {
      await sendPushToDeviceOwners(chipId, {
        title: "⏳ Secado de Sensor en Curso",
        body: "La lluvia se detuvo. El regador esperará a que el sensor se seque antes de reanudar."
      }, "sensor_lluvia");
    }

    // 3. Fin de Secado / Reanudación
    else if (
      (estadoAnterior === "PAUSA: SECADO" || estadoAnterior === "PAUSA: SENSOR") &&
      estadoNuevo === "IDLE"
    ) {
      await sendPushToDeviceOwners(chipId, {
        title: "☀️ Sensor de Lluvia Seco",
        body: "El sensor se ha secado por completo. El riego automático vuelve a estar activo."
      }, "fin_secado");
    }

    // 4. Pausa Manual / Retraso
    else if (estadoNuevo === "PAUSA: MANUAL" && estadoAnterior !== "PAUSA: MANUAL") {
      await sendPushToDeviceOwners(chipId, {
        title: "⏸️ Retraso por Lluvia Activado",
        body: "El riego ha sido pausado manualmente."
      }, "sensor_lluvia");
    }

    // 5. Alerta de Fallo de Corriente / Cortocircuito
    else if (estadoNuevo === "FALLO_CORRIENTE" && estadoAnterior !== "FALLO_CORRIENTE") {
      await sendPushToDeviceOwners(chipId, {
        title: "⚠️ Alerta Eléctrica",
        body: "Se detectó sobrecorriente o cortocircuito en las electroválvulas. Riego detenido por seguridad."
      }, "fallo_corriente");
    }
  });

/**
 * Disparador Firestore: Escucha creación de logs de historial (ej: fin de programa)
 */
exports.onLogCreated = functions.firestore
  .document("dispositivos/{chipId}/logs/{logId}")
  .onCreate(async (snap, context) => {
    const chipId = context.params.chipId;
    const logData = snap.data() || {};

    const tipo = String(logData.tipo || "");
    const msg = String(logData.msg || "");
    const prog = String(logData.prog || "Programa");

    console.log(`[LOG_TRIGGER] ${chipId} nuevo log tipo "${tipo}":`, logData);

    // 1. Fin de programa de riego
    if (tipo === "fin_prog" || msg.toLowerCase().includes("riego completado") || msg.toLowerCase().includes("fin de programa")) {
      await sendPushToDeviceOwners(chipId, {
        title: "✅ Riego Completado",
        body: `El ${prog} finalizó su ciclo de riego con éxito.`
      }, "fin_riego");
    }

    // 2. Alerta de error o fallo
    else if (tipo === "error" || tipo === "warning" || msg.toLowerCase().includes("fallo")) {
      await sendPushToDeviceOwners(chipId, {
        title: "⚠️ Alerta en Regador",
        body: msg || "Se ha registrado un evento de advertencia en el sistema."
      }, "fallo_corriente");
    }
  });

/**
 * HTTPS Callable: Enviar notificación de prueba para que el usuario valide su móvil
 */
exports.enviarNotificacionPrueba = functions.https.onCall(async (data, context) => {
  if (!context.auth) {
    throw new functions.https.HttpsError("unauthenticated", "Debe iniciar sesión.");
  }
  const uid = context.auth.uid;
  const chipId = data.chipId;

  console.log(`[PRUEBA] Enviando push de prueba para usuario: ${uid}`);

  const fcmSnap = await db.collection(`usuarios/${uid}/fcm_tokens`).get();
  const tokens = [];
  fcmSnap.forEach(doc => {
    const t = doc.data().token || doc.id;
    if (t) tokens.push(t);
  });

  if (tokens.length === 0) {
    throw new functions.https.HttpsError(
      "failed-precondition",
      "No hay ningún teléfono registrado. Activa los permisos de notificación en la app primero."
    );
  }

  const messagePayload = {
    tokens: tokens,
    notification: {
      title: "🔔 Prueba de Smart Riego",
      body: "¡Felicitaciones! Las notificaciones push están funcionando correctamente en tu teléfono."
    },
    webpush: {
      notification: {
        icon: "/icon-512.png",
        badge: "/favicon.ico",
        vibrate: [200, 100, 200]
      }
    }
  };

  const response = await admin.messaging().sendEachForMulticast(messagePayload);
  return {
    success: response.successCount > 0,
    successCount: response.successCount,
    failureCount: response.failureCount
  };
});

/**
 * HTTPS Webhook: Recibe eventos directos desde el ESP32 por WiFi
 */
exports.reportarEvento = functions.https.onRequest(async (req, res) => {
  try {
    const data = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || req.query || {});
    const chipId = data.chipId;
    const evento = data.evento;
    const prog = data.prog || "Programa";
    const msg = data.msg || "";

    if (!chipId || !evento) {
      return res.status(400).json({ error: "Faltan parámetros chipId o evento" });
    }

    console.log(`[HTTP_WEBHOOK] Recibido evento "${evento}" para chipId: ${chipId}`);

    if (evento === "fin_prog") {
      await sendPushToDeviceOwners(chipId, {
        title: "✅ Riego Completado",
        body: `El ${prog} finalizó su ciclo de riego con éxito.`
      }, "fin_riego");
    } else if (evento === "sensor_lluvia_mojado") {
      await sendPushToDeviceOwners(chipId, {
        title: "🌧️ Sensor de Lluvia Activado",
        body: "El sensor detectó lluvia. El riego se ha pausado automáticamente."
      }, "sensor_lluvia");
    } else if (evento === "sensor_lluvia_seco") {
      await sendPushToDeviceOwners(chipId, {
        title: "☀️ Sensor de Lluvia Seco",
        body: "El sensor se ha secado por completo. El riego automático vuelve a estar activo."
      }, "fin_secado");
    } else if (evento === "fallo_corriente") {
      await sendPushToDeviceOwners(chipId, {
        title: "⚠️ Alerta Eléctrica",
        body: msg || "Se detectó sobrecorriente o cortocircuito en las electroválvulas."
      }, "fallo_corriente");
    }

    return res.status(200).json({ ok: true });
  } catch (error) {
    console.error("[HTTP_WEBHOOK] Error procesando evento:", error);
    return res.status(500).json({ error: error.message });
  }
});
