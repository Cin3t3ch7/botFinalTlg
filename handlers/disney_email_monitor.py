import re
import logging
import asyncio
import concurrent.futures
import os
import time
import email as email_lib
import threading
from datetime import datetime, timedelta
from handlers.email_search_handlers import email_service
from database.connection import execute_query
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Executor dedicado para operaciones IMAP síncronas del monitor de Disney.
# Configurable vía env var IMAP_MONITOR_WORKERS (default=2).
# Se usa un ThreadPoolExecutor pequeño porque es un monitor de fondo;
# no necesita alta concurrencia y no se quiere saturar el servidor IMAP.
# ---------------------------------------------------------------------------
_IMAP_MONITOR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.environ.get("IMAP_MONITOR_WORKERS", "2")),
    thread_name_prefix="imap-monitor"
)

# Regex patterns for Disney email change detection
EMAIL_CHANGE_PATTERNS = [
    r'Se cambi(?:=C3=B3|ó) el correo electr(?:=C3=B3|ó)nico(?:=)?',
    r'Correo electr(?:=C3=B3|ó)nico de MyDisney actua(?:=)?',
    r'\* ;">\s*MyDisney unique email address updated\s*</td>',
    r'MyDisney email address has been updated',
    r'email address.*updated.*MyDisney',
    r'Disney account email.*changed',
    r'Account email.*has been.*updated'
]


class DisneyEmailMonitor:
    def __init__(self):
        self.compiled_patterns = [
            re.compile(pattern, re.IGNORECASE | re.DOTALL)
            for pattern in EMAIL_CHANGE_PATTERNS
        ]
        self.verification_threads = {}

    # ------------------------------------------------------------------
    # Punto de entrada async: mantiene el sleep en el event loop,
    # pero delega TODO el trabajo IMAP a un hilo del executor dedicado.
    # ------------------------------------------------------------------
    async def check_disney_email_changes(self, email_addr, bot_token, user_id, context):
        """
        Check for Disney email change notifications after a delay.
        El sleep de 6 minutos ocurre en el event loop (correcto, es async).
        La lógica IMAP síncrona ocurre en _IMAP_MONITOR_EXECUTOR (correcto,
        nunca bloquea el event loop).
        """
        try:
            # Espera async — el event loop sigue libre durante este tiempo
            await asyncio.sleep(360)

            logger.info(
                f"[disney-monitor] Verificando cambio de email para {email_addr} "
                f"(user_id={user_id})"
            )

            loop = asyncio.get_running_loop()

            # Toda la lógica IMAP se ejecuta en el executor – fuera del event loop
            email_changed = await loop.run_in_executor(
                _IMAP_MONITOR_EXECUTOR,
                lambda: self._check_disney_imap_sync(email_addr, bot_token, user_id)
            )

            if email_changed:
                await self._handle_email_change_detected(
                    email_addr, user_id, bot_token, context
                )
                return True
            else:
                logger.info(
                    f"[disney-monitor] ✅ Sin cambios de email detectados "
                    f"para {email_addr}"
                )
                return False

        except Exception as e:
            logger.error(
                f"[disney-monitor] Error en verificación de cambio de email "
                f"para {email_addr}: {e}",
                exc_info=True
            )
            return False

    # ------------------------------------------------------------------
    # Función síncrona: se ejecuta en el executor, time.sleep() válido aquí.
    # ------------------------------------------------------------------
    def _check_disney_imap_sync(self, email_addr, bot_token, user_id):
        """
        Lógica IMAP completamente síncrona ejecutada en un thread del executor.
        Nunca llama a código async ni retorna corutinas.
        Devuelve True si se detectó cambio de email.
        """
        # Verificar si el usuario ya está bloqueado/expirado
        try:
            user_status = execute_query("""
            SELECT access_until FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))

            if user_status and user_status[0][0] < datetime.now():
                logger.info(
                    f"[disney-monitor] Usuario {user_id} ya expirado/bloqueado, "
                    "saltando verificación"
                )
                return False
        except Exception as e:
            logger.warning(
                f"[disney-monitor] No se pudo verificar estado del usuario {user_id}: {e}"
            )

        # Obtener configuración y conexión IMAP
        try:
            config = email_service.get_imap_config(email_addr, bot_token)
            conn = email_service.get_connection(config)
        except Exception as e:
            logger.error(
                f"[disney-monitor] Error obteniendo conexión IMAP para {email_addr}: {e}"
            )
            return False

        date_since = (datetime.now() - timedelta(minutes=2)).strftime("%d-%b-%Y")
        disney_senders = [
            'disneyplus@trx.mail2.disneyplus.com',
            'member.services@disneyaccount.com'
        ]

        for sender in disney_senders:
            try:
                # Seleccionar INBOX antes de buscar
                status, _ = conn.select("INBOX", readonly=True)
                if status != 'OK':
                    logger.warning(
                        f"[disney-monitor] Error al seleccionar INBOX para {sender}"
                    )
                    continue

                search_criteria = (
                    f'FROM "{sender}" TO "{email_addr}" SINCE {date_since}'
                )
                # Desempaquetamos live_conn para actualizar conn si hubo reconexión interna
                status, messages, conn = email_service.search_with_retry(
                    conn, search_criteria, config=config, cid="disney-mon"
                )

                if not messages[0]:
                    continue

                # Procesar los últimos 5 mensajes
                message_ids = messages[0].split()[-5:]

                for msg_id in message_ids:
                    try:
                        # Desempaquetamos live_conn para actualizar conn si hubo reconexión
                        status, msg_data, conn = email_service.fetch_with_retry(
                            conn, msg_id, '(RFC822)', config=config, cid="disney-mon"
                        )
                        if status != 'OK':
                            continue

                        email_message = email_lib.message_from_bytes(msg_data[0][1])
                        email_content = self._get_email_content(email_message)
                        subject = email_message.get('Subject', '')

                        for pattern in self.compiled_patterns:
                            if pattern.search(email_content) or pattern.search(subject):
                                logger.warning(
                                    f"[disney-monitor] 🚨 Cambio de email detectado "
                                    f"para user={user_id} email={email_addr}"
                                )
                                return True

                    except Exception as e:
                        logger.error(
                            f"[disney-monitor] Error procesando mensaje de {sender}: {e}"
                        )
                        continue

            except Exception as e:
                logger.error(
                    f"[disney-monitor] Error buscando emails de {sender}: {e}"
                )
                continue

        return False

    def _get_email_content(self, email_message):
        """Extract content from email message."""
        content = ""
        try:
            if email_message.is_multipart():
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    if content_type in ["text/plain", "text/html"]:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                for encoding in ['utf-8', 'iso-8859-1', 'windows-1252']:
                                    try:
                                        body = payload.decode(encoding, 'ignore')
                                        content += body + "\n"
                                        break
                                    except (UnicodeDecodeError, LookupError):
                                        continue
                        except Exception as e:
                            logger.error(f"Error extrayendo parte del email: {e}")
            else:
                try:
                    payload = email_message.get_payload(decode=True)
                    if payload:
                        for encoding in ['utf-8', 'iso-8859-1', 'windows-1252']:
                            try:
                                content = payload.decode(encoding, 'ignore')
                                break
                            except (UnicodeDecodeError, LookupError):
                                continue
                except Exception as e:
                    logger.error(f"Error extrayendo contenido del email: {e}")
        except Exception as e:
            logger.error(f"Error general extrayendo contenido: {e}")
        return content

    async def _handle_email_change_detected(self, email_addr, user_id, bot_token, context):
        """Handle when Disney email change is detected."""
        try:
            await self._block_user(user_id, bot_token, email_addr)

            user_info = execute_query("""
            SELECT created_by FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))

            created_by = user_info[0][0] if user_info else None

            alert_message = (
                "🚨 ALERTA DE SEGURIDAD\n\n"
                "⚠️ Usuario bloqueado por cambio de email\n\n"
                f"👤 Usuario: ID_{user_id}\n"
                f"🆔 ID: {user_id}\n"
                f"📧 Email: {email_addr}\n"
                f"🕐 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "✅ El usuario ha sido bloqueado automáticamente por seguridad\n"
                "🔓 Usa /unblock para desbloquearlo si es necesario"
            )

            # Notificar al super admin
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=alert_message)
                logger.info(
                    f"[disney-monitor] ✅ Notificación enviada al super admin "
                    f"sobre bloqueo de usuario {user_id}"
                )
            except Exception as e:
                logger.error(f"[disney-monitor] Error enviando alerta al super admin: {e}")

            # Notificar a otros admins del bot
            try:
                admin_results = execute_query("""
                SELECT u.id FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE r.name IN ('admin', 'super_admin') AND u.bot_token = %s AND u.id != %s
                """, (bot_token, ADMIN_ID))

                if admin_results:
                    for admin_row in admin_results:
                        admin_id = admin_row[0]
                        try:
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=alert_message
                            )
                        except Exception as e:
                            logger.error(
                                f"[disney-monitor] Error enviando alerta al admin {admin_id}: {e}"
                            )
            except Exception as e:
                logger.error(f"[disney-monitor] Error obteniendo lista de admins: {e}")

            # Notificar al usuario bloqueado
            try:
                user_message = (
                    "🚨 CUENTA BLOQUEADA POR SEGURIDAD\n\n"
                    "⚠️ Tu cuenta ha sido bloqueada automáticamente\n\n"
                    "📋 Motivo: Cambio de email detectado en Disney\n"
                    f"📧 Email afectado: {email_addr}\n"
                    f"🕐 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "🔒 Esta es una medida de seguridad automática\n"
                    "📞 Contacta al administrador para resolver este problema\n\n"
                    "⚡ Tu acceso será restaurado una vez verificada tu identidad"
                )
                await context.bot.send_message(chat_id=user_id, text=user_message)
                logger.info(
                    f"[disney-monitor] ✅ Notificación de bloqueo enviada al usuario {user_id}"
                )
            except Exception as e:
                logger.error(
                    f"[disney-monitor] Error notificando al usuario {user_id}: {e}"
                )

            logger.info(
                f"[disney-monitor] ✅ Usuario {user_id} bloqueado y notificaciones enviadas"
            )

        except Exception as e:
            logger.error(
                f"[disney-monitor] Error manejando detección de cambio de email: {e}"
            )

    async def _block_user(self, user_id, bot_token, email_addr):
        """Block user by setting their access expiration to past date."""
        try:
            blocked_reason = (
                f"Cambio de email Disney detectado en {email_addr} "
                f"el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            try:
                execute_query("""
                UPDATE users
                SET access_until = NOW() - INTERVAL '1 day',
                    blocked_reason = %s
                WHERE id = %s AND bot_token = %s
                """, (blocked_reason, user_id, bot_token))
            except Exception as e:
                logger.warning(
                    f"[disney-monitor] Columna blocked_reason no existe, "
                    f"actualizando sin ella: {e}"
                )
                execute_query("""
                UPDATE users
                SET access_until = NOW() - INTERVAL '1 day'
                WHERE id = %s AND bot_token = %s
                """, (user_id, bot_token))

            logger.info(
                f"[disney-monitor] ✅ Usuario {user_id} bloqueado "
                "por cambio de email Disney"
            )
        except Exception as e:
            logger.error(f"[disney-monitor] Error bloqueando usuario {user_id}: {e}")
            raise


# Global instance
disney_email_monitor = DisneyEmailMonitor()
