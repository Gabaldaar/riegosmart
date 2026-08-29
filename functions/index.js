const functions = require("firebase-functions");
const admin = require("firebase-admin");

admin.initializeApp();
const db = admin.firestore();

/**
 * Despacha notificaciones Push Multicast a los propietarios de un regador (chipId),
 * respetando las preferencias individuales de cada usuario y limpiando tokens inválidos.
 *
 * @param {string} chipId - ID único del hardware ESP32
 * @param {object} notification - { title: string, body: string }
 * @param {string} eventType - "riego_completado" | "pausa_lluvia" | "fin_secado" | "general"
 * @param {object} extraData - Datos opcionales para la PWA
 */
async function sendPushToDeviceOwners(chipId, notification, eventType, extraData = {}) {
  try {
    console.log(`[FCM] Iniciando despacho para equipo: ${chipId} | Evento: ${eventType}`);

    const targetUids = new Set();

    // 1. Obtener el propietario principal desde el documento del dispositivo
    const devDocSnap = await db.doc(`dispositivos/${chipId}`).get();
    if (devDocSnap.exists) {
      const devData = devDocSnap.data() || {};
      if (devData.owner_uid) {
        targetUids.add(devData.owner_uid);
      }
    }

    // 2. Buscar usuarios que tengan vinculado este equipo en su documento de usuario
    const userDocsSnap = await db.collection("usuarios").where("chipId", "==", chipId).get();
    userDocsSnap.forEach((doc) => {
      targetUids.add(doc.id);
    });

    if (targetUids.size === 0) {
      console.log(`[FCM] No se encontraron usuarios propietarios vinculados al equipo ${chipId}.`);
      return;
    }

    // 3. Filtrar según preferencias y recolectar tokens FCM
    const tokensToSend = [];
    const tokenDocRefs = [];

    for (const uid of targetUids) {
      // Consultar preferencias del usuario
      const prefSnap = await db.doc(`usuarios/${uid}/config_notificaciones/actual`).get();
      const prefs = prefSnap.exists ? prefSnap.data() : {};

      // Si el usuario apagó todas las notificaciones, saltar
      if (prefs.notificaciones_activas === false) {
        console.log(`[FCM] Usuario ${uid} tiene las notificaciones globales desactivadas.`);
        continue;
      }

      // Si el usuario apagó este tipo específico de notificación, saltar
      if (eventType && prefs[eventType] === false) {
        console.log(`[FCM] Usuario ${uid} tiene desactivada la preferencia '${eventType}'.`);
        continue;
      }

      // Obtener todos los tokens FCM registrados para los dispositivos de este usuario
      const fcmSnap = await db.collection(`usuarios/${uid}/fcm_tokens`).get();
      fcmSnap.forEach((tokenDoc) => {
        const tData = tokenDoc.data();
        const tokenVal = tData.token || tokenDoc.id;
        if (tokenVal && !tokensToSend.includes(tokenVal)) {
          tokensToSend.push(tokenVal);
          tokenDocRefs.push({ ref: tokenDoc.ref, token: tokenVal, uid: uid });
        }
      });
    }

    if (tokensToSend.length === 0) {
      console.log(`[FCM] No hay tokens FCM válidos para notificar al equipo ${chipId}.`);
      return;
    }

    console.log(`[FCM] Enviando notificación a ${tokensToSend.length} dispositivo(s)...`);

    // 4. Construir carga de mensaje Multicast
    const messagePayload = {
      tokens: tokensToSend,
      notification: {
        title: notification.title || "Control de Riego Smart",
        body: notification.body || ""
      },
      data: Object.assign(
        {
          chipId: String(chipId),
          eventType: String(eventType || "general"),
          timestamp: String(Date.now()),
          url: "/"
        },
        extraData
      )
    };

    // 5. Despacho Multicast vía FCM
    const response = await admin.messaging().sendEachForMulticast(messagePayload);
    console.log(`[FCM] Resultado despacho - Éxitos: ${response.successCount}, Fallos: ${response.failureCount}`);

    // 6. Limpieza automática de tokens obsoletos / apps desinstaladas
    if (response.failureCount > 0) {
      const deletionPromises = [];
      response.responses.forEach((resp, idx) => {
        if (!resp.success) {
          const errCode = resp.error ? resp.error.code : "";
          console.warn(`[FCM] Error en token ${tokenDocRefs[idx].token.substring(0, 15)}...: ${errCode}`);
          if (
            errCode === "messaging/registration-token-not-registered" ||
            errCode === "messaging/invalid-registration-token"
          ) {
            console.log(`[FCM] Eliminando token obsoleto de Firestore para usuario ${tokenDocRefs[idx].uid}`);
            deletionPromises.push(tokenDocRefs[idx].ref.delete().catch(() => {}));
          }
        }
      });
      await Promise.all(deletionPromises);
    }
  } catch (error) {
    console.error("[FCM] Error general en sendPushToDeviceOwners:", error);
  }
}

/**
 * Helper para formatear nombre legible de zona
 */
function formatearZona(zona) {
  if (zona === undefined || zona === null) return "Zona";
  if (typeof zona === "number") return `Zona ${zona + 1}`;
  return String(zona);
}

/**
 * 1. Trigger Firestore: Disparo ante eventos de alerta en dispositivos/{chipId}/eventos/{eventId}
 */
exports.onDeviceEventCreated = functions.firestore
  .document("dispositivos/{chipId}/eventos/{eventId}")
  .onCreate(async (snap, context) => {
    const chipId = context.params.chipId;
    const data = snap.data();
    if (!data) return;

    const tipo = String(data.tipo || "").toLowerCase();
    const estado = String(data.estado || "").toLowerCase();
    const msg = String(data.msg || "");
    const prog = data.prog || data.programa || "Manual";
    const zona = data.zona !== undefined ? data.zona : data.zona_idx;

    // A) AVISO DE RIEGO COMPLETADO (Programa y Zona)
    if (tipo === "riego_completado" || tipo === "fin_prog" || tipo === "fin_zona") {
      let bodyText = "";
      if (tipo === "fin_zona") {
        bodyText = `Completó el riego en ${formatearZona(zona)} (Programa: ${prog}).`;
      } else {
        bodyText = `El programa '${prog}' ha finalizado todas sus zonas con éxito.`;
      }

      await sendPushToDeviceOwners(
        chipId,
        {
          title: "💧 Riego Finalizado",
          body: bodyText
        },
        "riego_completado",
        { prog: String(prog), zona: String(zona || "") }
      );
    }

    // B) AVISO DE PAUSA POR SENSOR DE LLUVIA / SECADO / RETRASO MANUAL
    else if (tipo === "sensor_lluvia" || tipo === "pausa_lluvia" || tipo === "pausa_manual") {
      if (estado === "detectada" || msg.includes("Lluvia") || tipo === "pausa_lluvia") {
        await sendPushToDeviceOwners(
          chipId,
          {
            title: "🌧️ Riego en Pausa: Lluvia",
            body: "El sensor de lluvia físico detectó agua. Se suspenden los riegos programados."
          },
          "pausa_lluvia"
        );
      } else if (estado === "secado" || tipo === "pausa_secado") {
        const horas = data.horas || data.delay_horas || 24;
        await sendPushToDeviceOwners(
          chipId,
          {
            title: "⏳ Riego en Pausa: Secado",
            body: `El sensor ya no detecta agua. Iniciando periodo de secado (${horas}h de espera).`
          },
          "pausa_lluvia",
          { horas: String(horas) }
        );
      } else if (tipo === "pausa_manual" || msg.includes("Retraso manual")) {
        await sendPushToDeviceOwners(
          chipId,
          {
            title: "⏸️ Riego en Pausa: Retraso Manual",
            body: "Se ha activado un retraso manual por lluvia desde la aplicación."
          },
          "pausa_lluvia"
        );
      }
    }

    // C) AVISO DE TERMINACIÓN DEL SECADO DEL SENSOR
    else if (estado === "fin_secado" || tipo === "fin_secado") {
      await sendPushToDeviceOwners(
        chipId,
        {
          title: "☀️ Sensor Despejado",
          body: "El periodo de secado ha concluido. El regador vuelve a estar listo y operativo."
        },
        "fin_secado"
      );
    }

    // D) FALLO O CORTE DETECTADO
    else if (tipo === "error" || tipo === "alerta") {
      await sendPushToDeviceOwners(
        chipId,
        {
          title: "⚠️ Alerta en Regador",
          body: msg || "Se ha producido un evento de advertencia en el equipo."
        },
        "general"
      );
    }
  });

/**
 * 2. Trigger Firestore: Disparo ante logs en dispositivos/{chipId}/logs/{logId} (compatibilidad directa)
 */
exports.onDeviceLogCreated = functions.firestore
  .document("dispositivos/{chipId}/logs/{logId}")
  .onCreate(async (snap, context) => {
    const chipId = context.params.chipId;
    const logData = snap.data();
    if (!logData) return;

    const tipo = String(logData.tipo || "").toLowerCase();
    const estado = String(logData.estado || "").toLowerCase();
    const msg = String(logData.msg || "");
    const prog = logData.prog || "Manual";
    const zona = logData.zona;

    if (tipo === "fin_prog" || tipo === "fin_zona") {
      const bodyText = tipo === "fin_zona"
        ? `Completó el riego en ${formatearZona(zona)} (Programa: ${prog}).`
        : `El programa '${prog}' ha finalizado su ciclo completo.`;

      await sendPushToDeviceOwners(
        chipId,
        { title: "💧 Riego Finalizado", body: bodyText },
        "riego_completado"
      );
    } else if (tipo === "sensor_lluvia") {
      if (estado === "detectada") {
        await sendPushToDeviceOwners(
          chipId,
          { title: "🌧️ Riego en Pausa: Lluvia", body: "Sensor de lluvia activado. Riegos suspendidos." },
          "pausa_lluvia"
        );
      } else if (estado === "secado") {
        const horas = logData.horas || 24;
        await sendPushToDeviceOwners(
          chipId,
          { title: "⏳ Riego en Pausa: Secado", body: `Iniciando tiempo de secado (${horas}h restantes).` },
          "pausa_lluvia"
        );
      } else if (estado === "fin_secado") {
        await sendPushToDeviceOwners(
          chipId,
          { title: "☀️ Sensor Despejado", body: "Fin del secado. Sistema de riego listo y operativo." },
          "fin_secado"
        );
      }
    } else if (msg.includes("Aborto por lluvia física")) {
      await sendPushToDeviceOwners(
        chipId,
        { title: "🌧️ Riego Interrumpido por Lluvia", body: "El riego en curso se detuvo debido a lluvia física." },
        "pausa_lluvia"
      );
    }
  });

/**
 * 3. Webhook HTTP para envío directo de alertas o telemetría
 */
exports.enviarAlertaDirecta = functions.https.onRequest(async (req, res) => {
  if (req.method !== "POST") {
    return res.status(405).send("Method Not Allowed");
  }

  const { chipId, title, body, eventType, extraData } = req.body || {};
  if (!chipId || !title) {
    return res.status(400).json({ error: "Faltan parámetros obligatorios: chipId y title." });
  }

  await sendPushToDeviceOwners(
    chipId,
    { title, body: body || "" },
    eventType || "general",
    extraData || {}
  );

  return res.status(200).json({ success: true, message: "Push procesado correctamente." });
});
