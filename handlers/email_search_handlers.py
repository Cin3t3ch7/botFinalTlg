import imaplib
import email
import re
import logging
import time
import socket
import asyncio
import uuid
from email.header import decode_header
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, NetworkError, TimedOut

logger = logging.getLogger(__name__)

# Patrones regex para diferentes búsquedas
REGEX_PATTERNS = {
    'disney': r'<td[^>]*>\s*(\d+)\s*</td>',
    'disney_household': r'15 min[\s\S]*?updated Household[\s\S]*?<td[^>]*>\s*(\d{6})\s*</td>',
    'disney_mydisney': r'id=(?:3D)?"otp_code"[^>]*>\s*(\d+)\s*<',
    'netflix_reset': r'https:\/\/www\.netflix\.com\/password\?g=[^"\s<>]+',
    'netflix_update_home': r'https:\/\/www\.netflix\.com\/account\/update-primary-location\?nftoken=[a-zA-Z0-9%+=&\/]+',
    'netflix_home_code': r'https:\/\/www\.netflix\.com\/account\/travel\/verify\?nftoken=[a-zA-Z0-9%+=\/]+',
    'netflix_login_code': r'<td\b[^>]*>\s*([0-9]{6})\s*<\/td>',
    'crunchyroll': r'Please\s*<a[^>]+href="(https:\/\/links\.mail\.crunchyroll\.com\/ls\/click\?[^"]+)"',
    'crunchyroll_device': r'click here\s*\(\s*(https?:\/\/[^)\s]+(?:\s*\r?\n\s*[^)\s]+)*)\s*\)',
    'prime': r'"otp">\s*(\d{6})',
    'max': r'https:\/\/auth\.hbomax\.com\/set-new-password\?passwordResetToken=[a-zA-Z0-9_\-=]+',
    'max_code': r'\s*(\d{6})\s*E',
    'netflix_country': r'_(\w{2})_EVO',  # Para capturar el código de país
    'netflix_activation': r'https:\/\/www\.netflix\.com\/ilum\?code=[a-zA-Z0-9%+=&\/]+'  # Para link de activación
}

FROM_ADDRESSES = {
    'disney': [
        'disneyplus@trx.mail2.disneyplus.com',
    ],
    'disney_mydisney': [
        ''
    ],
    'netflix': [
        'info@account.netflix.com'
    ],
    'crunchyroll': [
        'hello@mail.crunchyroll.com'
    ],
    'prime': [
        'account-update@primevideo.com',
        'account-update@amazon.com'
    ],
    'max': [
        'no-reply@alerts.hbomax.com'
    ]
}

# Configuraciones IMAP de respaldo
IMAP_CONFIG = {}

import threading

# ---------------------------------------------------------------------------
# Helpers Telegram seguros (silencian errores esperados sin ocultar bugs reales)
# ---------------------------------------------------------------------------

async def safe_edit_message_text(message, text, reply_markup=None, parse_mode=None):
    """
    Edita un mensaje capturando 'Message is not modified'.
    Retorna True si se editó, False si el contenido era idéntico.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug(
                f"safe_edit_message_text: contenido idéntico, no se edita "
                f"(msg_id={getattr(message, 'message_id', '?')})"
            )
            return False
        raise


async def safe_answer_callback(query, text=None, show_alert=False):
    """
    Responde un callback_query capturando errores esperados sin ocultar bugs:
      - BadRequest 'query is too old / invalid'  → warning, no crash
      - NetworkError / TimedOut                  → warning, no crash
        (delivery failure, no indica bug en el código)
    Todos los demás BadRequest se propagan normalmente.
    """
    try:
        await query.answer(text=text, show_alert=show_alert)
    except BadRequest as e:
        errmsg = str(e).lower()
        if "query is too old" in errmsg or "query id is invalid" in errmsg:
            logger.warning(
                f"safe_answer_callback: callback expirado o inválido (ignorado): {e}"
            )
        else:
            raise
    except (NetworkError, TimedOut) as e:
        # Fallo de red transitorio al responder el ACK — degradar sin crash
        logger.warning(
            f"safe_answer_callback: error de red al hacer ACK (ignorado): {e}"
        )

class EmailSearchService:
    def __init__(self):
        """Inicializa el servicio de búsqueda de correos con conexiones persistentes"""
        self._connections = {}  # Almacena conexiones IMAP activas
        self._last_used = {}    # Registra cuando se usó por última vez una conexión
        self._connection_timeout = 40  # Tiempo de expiración de conexiones en segundos
        self._lock = threading.Lock()  # Lock para thread safety
        
    def get_imap_config(self, email_addr, bot_token=None):
        """Obtiene la configuración IMAP apropiada para un correo"""
        # Si se proporciona token, buscar primero en la base de datos
        if bot_token:
            try:
                from database.connection import execute_query
                
                # Obtener todas las configuraciones IMAP para este bot
                configs = execute_query(
                    "SELECT domain, email, password, imap_server FROM imap_config WHERE bot_token = %s",
                    (bot_token,)
                )
                
                # Si hay configuraciones para este bot
                if configs and len(configs) > 0:
                    local_part = None
                    domain = None
                    gmail_config = None
                    
                    # Almacenar la configuración de Gmail si existe (para usar como respaldo)
                    for config in configs:
                        if config[0] == 'gmail.com':
                            gmail_config = config
                            break
                    
                    # Determinar la parte local y el dominio del correo proporcionado
                    if '@' in email_addr:
                        local_part, domain = email_addr.split('@', 1)
                    else:
                        local_part = email_addr  # Si no hay @, todo es parte local
                        
                    # 1. PRIMERA PRIORIDAD: Si tiene +, buscar por la parte antes del +
                    if '+' in local_part:
                        plus_prefix = local_part.split('+', 1)[0]
                        logger.info(f"Correo con +: buscando configuración para prefijo: {plus_prefix}")
                        
                        # Buscar prefijo exacto
                        for config_domain, config_email, config_password, config_server in configs:
                            if config_domain == plus_prefix:
                                logger.info(f"Usando configuración para prefijo: {plus_prefix}")
                                return {
                                    'EMAIL_ACCOUNT': config_email,
                                    'PASSWORD': config_password,
                                    'IMAP_SERVER': config_server,
                                    'IMAP_PORT': 993
                                }
                    
                    # 2. SEGUNDA PRIORIDAD: Buscar configuración para el dominio específico
                    logger.info(f"Buscando configuración para dominio: {domain}")
                    for config_domain, config_email, config_password, config_server in configs:
                        if config_domain == domain:
                            logger.info(f"Usando configuración para dominio específico: {domain}")
                            return {
                                'EMAIL_ACCOUNT': config_email,
                                'PASSWORD': config_password,
                                'IMAP_SERVER': config_server,
                                'IMAP_PORT': 993
                            }
                    
                    # 3. TERCERA PRIORIDAD: Si el dominio es gmail.com y no tiene +
                    if domain == 'gmail.com' and '+' not in local_part and gmail_config:
                        logger.info(f"Correo de Gmail sin +, usando configuración para gmail.com")
                        _, config_email, config_password, config_server = gmail_config
                        return {
                            'EMAIL_ACCOUNT': config_email,
                            'PASSWORD': config_password,
                            'IMAP_SERVER': config_server,
                            'IMAP_PORT': 993
                        }
                    
                    # 4. ÚLTIMA PRIORIDAD: Usar la configuración de Gmail como respaldo
                    if gmail_config:
                        logger.warning(f"No se encontró configuración específica para {email_addr}, usando Gmail como respaldo")
                        _, config_email, config_password, config_server = gmail_config
                        return {
                            'EMAIL_ACCOUNT': config_email,
                            'PASSWORD': config_password,
                            'IMAP_SERVER': config_server,
                            'IMAP_PORT': 993
                        }
                    
                    # Si no hay configuración de Gmail, usar la primera disponible
                    logger.warning(f"No hay configuración de Gmail, usando la primera disponible")
                    domain, email, password, server = configs[0]
                    return {
                        'EMAIL_ACCOUNT': email,
                        'PASSWORD': password,
                        'IMAP_SERVER': server,
                        'IMAP_PORT': 993
                    }
            except Exception as e:
                logger.error(f"Error al obtener configuración IMAP de la BD: {e}")
                # Continuar con el método tradicional si hay error
        
        # Método tradicional (respaldo) si no hay token o no se encontró configuración en la BD
        if '@' in email_addr:
            local_part, domain = email_addr.split('@', 1)
            
            # 1. PRIMERA PRIORIDAD: Si tiene +, buscar por la parte antes del +
            if '+' in local_part:
                plus_prefix = local_part.split('+', 1)[0]
                if plus_prefix in IMAP_CONFIG:
                    logger.info(f"Usando configuración para prefijo: {plus_prefix}")
                    return IMAP_CONFIG[plus_prefix]
            
            # 2. SEGUNDA PRIORIDAD: Buscar configuración para el dominio específico
            if domain in IMAP_CONFIG:
                logger.info(f"Usando configuración para dominio: {domain}")
                return IMAP_CONFIG[domain]
                
            # 3. TERCERA PRIORIDAD: Si el dominio es gmail.com y no tiene +
            if domain == 'gmail.com' and '+' not in local_part and 'gmail.com' in IMAP_CONFIG:
                logger.info(f"Usando configuración específica para gmail.com")
                return IMAP_CONFIG['gmail.com']
                
            # 4. ÚLTIMA PRIORIDAD: Usar Gmail como respaldo general
            if 'gmail.com' in IMAP_CONFIG:
                logger.warning(f"Usando Gmail como configuración de respaldo para: {email_addr}")
                return IMAP_CONFIG['gmail.com']
        else:
            # Si el correo no tiene @, buscar por el valor exacto
            if email_addr in IMAP_CONFIG:
                return IMAP_CONFIG[email_addr]
            
            # Si no se encuentra, intentar usar Gmail como respaldo
            if 'gmail.com' in IMAP_CONFIG:
                logger.warning(f"Usando Gmail como respaldo para correo sin dominio: {email_addr}")
                return IMAP_CONFIG['gmail.com']
        
        # Respaldo final: devolver la primera configuración disponible solo si no hay Gmail
        if IMAP_CONFIG:
            logger.warning(f"No hay configuración de Gmail ni específica, usando la primera disponible")
            return next(iter(IMAP_CONFIG.values()))
            
        raise ValueError(f"No se encontró configuración IMAP para el correo: {email_addr}")

    # Conjunto de tipos de excepción que indican conexión IMAP muerta.
    _DEAD_EXC_TYPES = (socket.timeout, imaplib.IMAP4.abort, BrokenPipeError, ConnectionResetError)
    # Textos en el mensaje de la excepción que también indican conexión muerta.
    _DEAD_EXC_TEXTS = ("timed out", "timed out object", "broken pipe", "connection reset", "eof occurred")

    def _is_dead_connection(self, exc: Exception) -> bool:
        """Devuelve True si la excepción indica que la conexión IMAP está irrecuperable."""
        if isinstance(exc, self._DEAD_EXC_TYPES):
            return True
        msg = str(exc).lower()
        return any(t in msg for t in self._DEAD_EXC_TEXTS)

    def _discard_conn_from_pool(self, conn) -> str:
        """
        Descarta una conexión muerta del pool de forma thread-safe.
        - Obtiene la clave bajo lock, luego hace logout sin lock (puede bloquear).
        Devuelve la clave descartada o '' si no estaba en el pool.
        """
        key_to_remove = ""
        with self._lock:
            for key, c in list(self._connections.items()):
                if c is conn:
                    key_to_remove = key
                    self._connections.pop(key, None)
                    self._last_used.pop(key, None)
                    break
        # Logout fuera del lock para evitar bloquear otros hilos durante operación de red
        if key_to_remove:
            try:
                conn.logout()
            except Exception:
                pass
            logger.warning(
                f"[IMAP-POOL] Conexión muerta descartada del pool (key={key_to_remove})"
            )
        return key_to_remove

    def connect_to_imap(self, config):
        """Establece una conexión IMAP usando la configuración proporcionada."""
        try:
            conn = imaplib.IMAP4_SSL(config['IMAP_SERVER'], config['IMAP_PORT'])

            # Login con backoff lineal (código sync, puede usar time.sleep)
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    conn.login(config['EMAIL_ACCOUNT'], config['PASSWORD'])
                    break
                except imaplib.IMAP4.error as e:
                    if attempt < max_retries and (
                        "try again" in str(e).lower() or "too many connections" in str(e).lower()
                    ):
                        logger.warning(
                            f"[IMAP] Error temporal en login, reintentando "
                            f"({attempt+1}/{max_retries}): {e}"
                        )
                        time.sleep(1 + attempt)  # back-off lineal simple
                        continue
                    raise

            # Timeout de socket configurable; 30 s es el default seguro.
            import os as _os
            socket_timeout = int(_os.environ.get("IMAP_SOCKET_TIMEOUT", "30"))
            conn.socket().settimeout(socket_timeout)

            return conn
        except Exception as e:
            raise Exception(f"Error de conexión IMAP: {str(e)}")
    
    def get_connection(self, config):
        """
        Obtiene una conexión del pool o crea una nueva.

        Patrón fast-path / slow-path / commit:
          - fast-path: consulta el pool DENTRO del lock (operación rápida).
          - slow-path: conecta FUERA del lock para no serializar los hilos
                       que necesitan conexiones simultáneas.
          - commit: registra la nueva conexión DENTRO del lock; si otro hilo
                    ya conectó mientras tanto, descarta la nuestra.
        """
        config_key = f"{config['IMAP_SERVER']}_{config['EMAIL_ACCOUNT']}"
        current_time = time.time()

        # ── FAST PATH ──────────────────────────────────────────────────────
        stale_conns = []  # recolectar conns expiradas para logout FUERA del lock
        with self._lock:
            # Limpiar conexiones expiradas: pop bajo lock, logout posterior fuera del lock
            for key in list(self._connections.keys()):
                if current_time - self._last_used.get(key, 0) > self._connection_timeout:
                    expired_conn = self._connections.pop(key, None)
                    self._last_used.pop(key, None)
                    if expired_conn:
                        stale_conns.append((key, expired_conn))

            if config_key in self._connections:
                conn = self._connections[config_key]
                try:
                    conn.noop()
                    self._last_used[config_key] = current_time
                    logger.debug(f"[IMAP-POOL] Reutilizando conexión existente (key={config_key})")
                    # Logout de expiradas fuera del lock antes de retornar
                    for s_key, s_conn in stale_conns:
                        try:
                            s_conn.logout()
                        except Exception:
                            pass
                        logger.debug(f"[IMAP-POOL] Conexión expirada descartada (key={s_key})")
                    return conn
                except Exception as e:
                    logger.warning(
                        f"[IMAP-POOL] noop() falló en fast-path, descartando (key={config_key}): {e}"
                    )
                    self._connections.pop(config_key, None)
                    self._last_used.pop(config_key, None)
                    # cae al slow-path

        # Logout de conns expiradas fuera del lock (I/O de red no bloquea a otros)
        for s_key, s_conn in stale_conns:
            try:
                s_conn.logout()
            except Exception:
                pass
            logger.debug(f"[IMAP-POOL] Conexión expirada descartada (key={s_key})")

        # ── SLOW PATH (fuera del lock) ─────────────────────────────────────
        logger.info(f"[IMAP-POOL] Creando nueva conexión a {config['IMAP_SERVER']} (key={config_key})")
        new_conn = self.connect_to_imap(config)

        # ── COMMIT ────────────────────────────────────────────────────────
        with self._lock:
            if config_key in self._connections:
                # Otro hilo conectó mientras estábamos en slow-path; usar la suya.
                existing = self._connections[config_key]
                try:
                    new_conn.logout()
                except Exception:
                    pass
                logger.debug(
                    f"[IMAP-POOL] Conexión duplicada descartada (key={config_key}), "
                    "usando la existente."
                )
                return existing
            self._connections[config_key] = new_conn
            self._last_used[config_key] = time.time()
            logger.info(f"[IMAP-POOL] Nueva conexión registrada en pool (key={config_key})")
            return new_conn
    
    def search_with_retry(self, conn, criteria, config=None, max_retries=2, cid="-"):
        """
        Busca en IMAP con reintentos seguros.

        Devuelve (status, messages, live_conn).
        `live_conn` puede ser diferente de `conn` si se reconectó internamente;
        el caller DEBE usarla para operaciones posteriores (fetch, select).

        time.sleep() aquí es CORRECTO: siempre corre en un thread del executor.
        """
        current_conn = conn
        reconnect_count = 0
        for attempt in range(max_retries + 1):
            try:
                status, messages = current_conn.search(None, criteria)
                return status, messages, current_conn
            except Exception as e:
                if attempt < max_retries and self._is_dead_connection(e):
                    reconnect_count += 1
                    logger.warning(
                        f"[{cid}][IMAP-SEARCH] Conexión muerta "
                        f"(intento {attempt+1}/{max_retries}, reconexión #{reconnect_count}): {e}. "
                        "Descartando y reconectando..."
                    )
                    dead_key = self._discard_conn_from_pool(current_conn)
                    if config is None:
                        raise Exception(
                            f"[{cid}] Conexión muerta (key={dead_key}) pero config=None; "
                            f"no se puede reconectar: {e}"
                        )
                    # Jitter mínimo (sync, OK en executor)
                    time.sleep(0.5 + attempt * 0.5)
                    current_conn = self.get_connection(config)
                    logger.info(
                        f"[{cid}][IMAP-SEARCH] Reconectado (key={dead_key}), "
                        "reintentando búsqueda..."
                    )
                    continue
                # Error no recuperable o reintentos agotados
                raise Exception(
                    f"[{cid}] Error en búsqueda IMAP tras {attempt+1} intento(s): {str(e)}"
                )

    def fetch_with_retry(self, conn, msg_id, format_string, config=None, max_retries=2, cid="-"):
        """
        Recupera un mensaje IMAP con reintentos seguros.

        Devuelve (status, data, live_conn).
        `live_conn` puede ser diferente de `conn` si se reconectó internamente;
        el caller DEBE usarla para operaciones posteriores.

        time.sleep() es seguro aquí (executor).
        """
        current_conn = conn
        reconnect_count = 0
        for attempt in range(max_retries + 1):
            try:
                status, data = current_conn.fetch(msg_id, format_string)
                return status, data, current_conn
            except Exception as e:
                if attempt < max_retries and self._is_dead_connection(e):
                    reconnect_count += 1
                    logger.warning(
                        f"[{cid}][IMAP-FETCH] Conexión muerta "
                        f"(intento {attempt+1}/{max_retries}, reconexión #{reconnect_count}): {e}. "
                        "Descartando y reconectando..."
                    )
                    dead_key = self._discard_conn_from_pool(current_conn)
                    if config is None:
                        raise Exception(
                            f"[{cid}] Conexión muerta (key={dead_key}) pero config=None; "
                            f"no se puede reconectar: {e}"
                        )
                    time.sleep(0.5 + attempt * 0.5)
                    current_conn = self.get_connection(config)
                    logger.info(
                        f"[{cid}][IMAP-FETCH] Reconectado (key={dead_key}), "
                        "reintentando fetch..."
                    )
                    continue
                raise Exception(
                    f"[{cid}] Error en fetch IMAP tras {attempt+1} intento(s): {str(e)}"
                )
    
    def list_folders(self, email_addr, bot_token=None):
        """Lista las carpetas disponibles en la cuenta IMAP"""
        config = self.get_imap_config(email_addr, bot_token)
        
        # Obtener conexión del pool
        try:
            conn = self.get_connection(config)
        except Exception as e:
            logger.error(f"Error al obtener conexión IMAP del pool: {e}")
            raise Exception(f"No se pudo establecer conexión IMAP: {e}")
        
        try:
            status, folder_list = conn.list()
            
            if status != 'OK':
                raise Exception("Error al obtener la lista de carpetas")
            
            folders = []
            for folder_info in folder_list:
                if isinstance(folder_info, bytes):
                    folder_info = folder_info.decode('utf-8')
                    # Extraer el nombre de la carpeta
                    match = re.search(r'"([^"]*)"$', folder_info)
                    if match:
                        folder_name = match.group(1)
                        folders.append(folder_name)
            
            return folders
        finally:
            # No cerramos la conexión aquí para mantenerla persistente
            pass
    
    def search_emails(self, email_addr, service, regex_type=None, folder="INBOX", days_back=1, bot_token=None, user_id=None):
        """Busca correos usando una expresión regular según el servicio y devuelve el resultado."""
        cid = str(uuid.uuid4())[:8]  # correlation-id por búsqueda
        t_start = time.perf_counter()
        logger.info(f"[{cid}] Iniciando búsqueda service={service} type={regex_type or 'default'} email={email_addr}")
        
        # Verificación de acceso
        if user_id and bot_token:
            try:
                from database.connection import execute_query
                
                # Verificar si el usuario es superadmin o admin
                from config import ADMIN_ID
                if user_id == ADMIN_ID:
                    pass  # El superadmin siempre tiene acceso
                else:
                    # Verificar si es admin
                    admin_result = execute_query("""
                    SELECT r.name FROM users u
                    JOIN roles r ON u.role_id = r.id
                    WHERE u.id = %s AND u.bot_token = %s
                    """, (user_id, bot_token))
                    
                    is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
                    
                    if not is_admin:
                        # Verificar si tiene acceso libre
                        free_result = execute_query("""
                        SELECT free_access FROM users
                        WHERE id = %s AND bot_token = %s
                        """, (user_id, bot_token))
                        
                        has_free_access = free_result and free_result[0][0]
                        
                        if not has_free_access:
                            # Verificar si tiene este correo asignado
                            email_result = execute_query("""
                            SELECT id FROM user_emails
                            WHERE user_id = %s AND bot_token = %s AND email = %s
                            """, (user_id, bot_token, email_addr))
                            
                            if not email_result:
                                raise ValueError(f"No tienes acceso al correo {email_addr}")
            except ImportError:
                logger.warning("No se pudo verificar acceso a través de la base de datos")
        
        # Determinar el servicio y los remitentes
        service_lower = service.lower()
        # Calcular la clave regex primero
        regex_key = f"{service_lower}_{regex_type}" if regex_type else service_lower

        # Usar regex_key como from_key si existe en FROM_ADDRESSES
        if regex_key in FROM_ADDRESSES:
            from_key = regex_key
        else:
            service_mapping = {
                'netflix': 'netflix',
                'disney': 'disney',
                'disney_mydisney': 'disney_mydisney',
                'max': 'max', 
                'prime': 'prime',
                'crunchyroll': 'crunchyroll'
            }
            from_key = service_mapping.get(service_lower, service_lower)

        if from_key not in FROM_ADDRESSES:
            raise ValueError(f"Servicio no reconocido: {service}")

        from_addresses = FROM_ADDRESSES[from_key]
        
        # Determinar qué regex usar
        regex_key = f"{service_lower}_{regex_type}" if regex_type else service_lower
        if regex_key not in REGEX_PATTERNS:
            raise ValueError(f"No hay patrón regex para el servicio: {regex_key}")
        
        regex_pattern = REGEX_PATTERNS[regex_key]
        
        # Obtener configuración IMAP
        config = self.get_imap_config(email_addr, bot_token)
        
        # Obtener conexión del pool
        conn = None
        try:
            t_connect = time.perf_counter()
            try:
                conn = self.get_connection(config)
            except Exception as e:
                logger.error(f"[{cid}] Error al obtener conexión IMAP del pool: {e}")
                raise Exception(f"No se pudo establecer conexión IMAP: {e}")
                
            logger.debug(
                f"[{cid}] Conexión IMAP obtenida en "
                f"{time.perf_counter()-t_connect:.3f}s"
            )
            
            # Seleccionar carpeta (siempre recargar para buscar nuevos correos)
            t_select = time.perf_counter()
            try:
                status, messages = conn.select(folder, readonly=True)
                if status != 'OK':
                    raise Exception(f"Error al seleccionar la carpeta {folder}")
                logger.debug(f"[{cid}] select() en {time.perf_counter()-t_select:.3f}s")
            except Exception as e:
                # Si falla, podría ser un problema de conexión — reconectar vía pool
                logger.warning(f"[{cid}] Error al seleccionar carpeta, reconectando: {e}")
                if self._is_dead_connection(e):
                    self._discard_conn_from_pool(conn)
                conn = self.get_connection(config)
                status, messages = conn.select(folder, readonly=True)
                if status != 'OK':
                    raise Exception(f"Error al seleccionar la carpeta {folder} después de reconexión")
            
            # Construir fecha para búsqueda (reducir días para búsqueda más eficiente)
            days_back = min(days_back, 3)  # Limitar a máximo 3 días para búsquedas más rápidas
            date_since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
            
            # Optimizar búsqueda: combinar FROM y TO en una sola consulta
            search_criteria = []
            
            # Crear criterio para remitentes
            for from_addr in from_addresses:
                if '@' in email_addr:
                    # Búsqueda combinada de remitente y destinatario para mayor precisión
                    search_criteria.append(f'(FROM "{from_addr}" TO "{email_addr}" SINCE {date_since})')
                else:
                    search_criteria.append(f'(FROM "{from_addr}" SINCE {date_since})')
            
            # Combinar criterios con OR
            if len(search_criteria) > 1:
                combined_criteria = f'OR {" ".join(search_criteria)}'
            else:
                combined_criteria = search_criteria[0]
            
            # Realizar la búsqueda con reintentos (pasamos config para permitir reconexión)
            # search_with_retry devuelve (status, messages, live_conn); usamos live_conn
            # para que conn sea siempre la conexión viva devuelta tras posibles reconexiones.
            t_search = time.perf_counter()
            try:
                status, messages, conn = self.search_with_retry(
                    conn, combined_criteria, config=config, cid=cid
                )
                logger.debug(f"[{cid}] search() en {time.perf_counter()-t_search:.3f}s")
            except Exception as e:
                logger.error(f"[{cid}] Error en búsqueda IMAP: {e}")

                # Intentar una búsqueda más simple como último recurso
                fallback_criteria = f'SINCE {date_since}'
                logger.info(f"[{cid}] Intentando búsqueda simplificada: {fallback_criteria}")
                try:
                    # Reconectar frescamente para el fallback
                    conn = self.get_connection(config)
                    status, messages, conn = self.search_with_retry(
                        conn, fallback_criteria, config=config, cid=cid
                    )
                except Exception as e2:
                    logger.error(f"[{cid}] Error en búsqueda simplificada: {e2}")
                    raise Exception(f"Error en búsqueda IMAP: {str(e)}")
            
            if not messages[0]:
                t_total = time.perf_counter() - t_start
                logger.info(
                    f"[{cid}] Búsqueda completada total={t_total:.3f}s resultado=no encontrado"
                )
                return None
            
            # Compilar expresión regular para cuerpo
            try:
                body_regex = re.compile(regex_pattern, re.IGNORECASE | re.DOTALL)
            except re.error as e:
                raise Exception(f"Error en la expresión regular: {str(e)}")
            
            # Variable para almacenar el resultado más reciente
            latest_result = None
            
            # Procesar mensajes más recientes primero (limitar a 10 para mayor velocidad)
            message_ids = messages[0].split()
            message_ids.reverse()  # Ordenar de más recientes a más antiguos
            message_ids = message_ids[:10]  # Procesar solo los 10 más recientes
            
            logger.info(f"Procesando {len(message_ids)} mensajes recientes para {email_addr}")
            
            # Procesamiento optimizado: verificar directamente los mensajes más recientes
            for msg_id in message_ids:
                # Recuperar encabezados primero para validación rápida
                t_hdr = time.perf_counter()
                try:
                    # Desempaquetamos live_conn para que conn quede actualizado si hubo reconexión
                    status, msg_data, conn = self.fetch_with_retry(
                        conn, msg_id, '(BODY.PEEK[HEADER])', config=config, cid=cid
                    )
                    if status != 'OK':
                        continue
                    logger.debug(f"[{cid}] fetch_header() en {time.perf_counter()-t_hdr:.3f}s")
                except Exception as e:
                    logger.error(f"[{cid}] Error al recuperar encabezado: {e}")
                    continue
                
                # Validar remitente y destinatario
                raw_headers = msg_data[0][1]
                email_headers = email.message_from_bytes(raw_headers)
                
                # Verificar remitente
                from_value = email_headers.get('From', '')
                from_match = any(addr.lower() in from_value.lower() for addr in from_addresses)
                if not from_match:
                    continue
                
                # Verificar destinatario si se especificó un correo
                if '@' in email_addr:
                    to_value = email_headers.get('To', '')
                    email_addr_lower = email_addr.lower()
                    
                    # Verificación simplificada del destinatario
                    if email_addr_lower not in to_value.lower():
                        # Verificar si es un correo con formato user+tag@domain
                        if '+' in email_addr_lower:
                            base_email = email_addr_lower.split('@')[0].split('+')[0]
                            domain = email_addr_lower.split('@')[1]
                            pattern = f"{base_email}+[^@]*@{domain}"
                            if not re.search(pattern, to_value.lower()):
                                continue
                        else:
                            continue
                
                # Si pasa las validaciones, recuperar el mensaje completo
                t_body = time.perf_counter()
                try:
                    status, msg_data, conn = self.fetch_with_retry(
                        conn, msg_id, '(RFC822)', config=config, cid=cid
                    )
                    if status != 'OK':
                        continue
                    logger.debug(f"[{cid}] fetch_body() en {time.perf_counter()-t_body:.3f}s")
                except Exception as e:
                    logger.error(f"[{cid}] Error al recuperar mensaje completo: {e}")
                    continue
                
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)
                
                # Extraer asunto para registro
                subject = self.decode_email_subject(email_message.get('Subject', ''))
                
                # Búsqueda optimizada en el cuerpo del mensaje
                if email_message.is_multipart():
                    # Primero en HTML (más común tener los códigos/enlaces aquí)
                    for part in email_message.walk():
                        if part.get_content_type() == "text/html":
                            try:
                                body = part.get_payload(decode=True).decode('utf-8', 'ignore')
                                match = body_regex.search(body)
                                if match:
                                    result = match.group(1) if match.groups() else match.group(0)
                                    result = result.replace('amp;', '')
                                    
                                    latest_result = {
                                        'result': result,
                                        'is_link': result.startswith('http'),
                                        'subject': subject,
                                        'date': email_message.get('Date', ''),
                                        'from': email_message.get('From', '')
                                    }
                                    # Encontramos un resultado, romper ciclo externo
                                    break
                            except Exception as e:
                                logger.error(f"Error al procesar parte HTML: {e}")
                    
                    # Si no se encontró en HTML y aún no tenemos resultado, buscar en texto plano
                    if not latest_result:
                        for part in email_message.walk():
                            if part.get_content_type() == "text/plain":
                                try:
                                    body = part.get_payload(decode=True).decode('utf-8', 'ignore')
                                    if match:
                                        result = match.group(1) if match.groups() else match.group(0)
                                        result = result.replace('amp;', '')
                                        
                                        latest_result = {
                                            'result': result,
                                            'is_link': result.startswith('http'),
                                            'subject': subject,
                                            'date': email_message.get('Date', ''),
                                            'from': email_message.get('From', '')
                                        }
                                        break
                                except Exception as e:
                                    logger.error(f"Error al procesar parte texto: {e}")
                else:
                    # No es multiparte, procesar directamente
                    try:
                        body = email_message.get_payload(decode=True).decode('utf-8', 'ignore')
                        match = body_regex.search(body)
                        if match:
                            result = match.group(1) if match.groups() else match.group(0)
                            result = result.replace('amp;', '')
                            
                            latest_result = {
                                'result': result,
                                'is_link': result.startswith('http'),
                                'subject': subject,
                                'date': email_message.get('Date', ''),
                                'from': email_message.get('From', '')
                            }
                    except Exception as e:
                        logger.error(f"Error al procesar mensaje no multiparte: {e}")
                
                # Si encontramos un resultado, terminar la búsqueda
                if latest_result:
                    break
            
            t_total = time.perf_counter() - t_start
            logger.info(
                f"[{cid}] Búsqueda completada total={t_total:.3f}s "
                f"resultado={'encontrado' if latest_result else 'no encontrado'}"
            )

            # Devolver el resultado más reciente, o None si no se encontró nada
            return latest_result
                    
        finally:
            # No cerramos la conexión para mantenerla persistente
            # Las conexiones inactivas se limpiarán en la próxima búsqueda
            pass

    def decode_email_subject(self, subject):
        """Decodifica el asunto del correo"""
        if not subject:
            return ""
            
        decoded_list = decode_header(subject)
        result = ''
        for decoded_string, charset in decoded_list:
            if isinstance(decoded_string, bytes):
                if charset:
                    result += decoded_string.decode(charset, 'ignore')
                else:
                    result += decoded_string.decode('utf-8', 'ignore')
            else:
                result += decoded_string
        return result
    
    def cleanup(self):
        """Cierra todas las conexiones IMAP abiertas sin bloquear otros hilos."""
        conns_to_close = []
        with self._lock:
            for key, conn in list(self._connections.items()):
                conns_to_close.append((key, conn))
            
            self._connections.clear()
            self._last_used.clear()

        # Logout fuera del lock
        for key, conn in conns_to_close:
            try:
                conn.logout()
                # Ofuscar el key para no filtrar correos en logs de nivel debug
                import hashlib
                safe_key = hashlib.md5(key.encode()).hexdigest()[:8]
                logger.debug(f"[IMAP-POOL] Conexión IMAP cerrada para key={safe_key} en cleanup")
            except:
                pass

# Instancia global del servicio
email_service = EmailSearchService()

# Funciones handler para Telegram

async def handle_netflix_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'netflix_reset_link':
        context.user_data['search_state'] = 'netflix_reset'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de restablecimiento de contraseña de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'netflix_update_home':
        context.user_data['search_state'] = 'netflix_home'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de actualización de hogar de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'netflix_home_code':
        context.user_data['search_state'] = 'netflix_home_code'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código de hogar de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'netflix_login_code':
        context.user_data['search_state'] = 'netflix_login'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código de inicio de sesión de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'netflix_country':
        context.user_data['search_state'] = 'netflix_country'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el país de la cuenta de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == 'netflix_activation':
        context.user_data['search_state'] = 'netflix_activation'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Netflix", callback_data='netflix_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de activación de Netflix:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_disney_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'disney_code':
        context.user_data['search_state'] = 'disney_code'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Disney", callback_data='disney_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código de verificación de Disney:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'disney_home':
        context.user_data['search_state'] = 'disney_household'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Disney", callback_data='disney_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código de actualización de hogar de Disney:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'disney_mydisney':  # Nuevo manejador
        context.user_data['search_state'] = 'disney_mydisney'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver al Disney", callback_data='disney_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código OTP de My Disney:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_crunchyroll_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)  # ACK inmediato para evitar "Query is too old"
    if query.data == 'crunchyroll_reset':
        context.user_data['search_state'] = 'crunchyroll_reset'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Crunchyroll", callback_data='crunchyroll_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de restablecimiento de Crunchyroll:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'crunchyroll_device':
        context.user_data['search_state'] = 'crunchyroll_device'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Crunchyroll", callback_data='crunchyroll_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de verificación de dispositivo de Crunchyroll:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_prime_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)  # ACK inmediato para evitar "Query is too old"
    if query.data == 'prime_otp':
        context.user_data['search_state'] = 'prime_otp'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Prime", callback_data='prime_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código OTP de Prime Video:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_max_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)  # ACK inmediato para evitar "Query is too old"
    if query.data == 'max_reset':
        context.user_data['search_state'] = 'max_reset'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Max", callback_data='max_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el enlace de restablecimiento de Max:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data == 'max_code':
        context.user_data['search_state'] = 'max_code'
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Max", callback_data='max_menu')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "Por favor, envía la dirección de correo para buscar el código de Max:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# Función auxiliar para enviar mensajes de forma segura
async def safe_send_message(update: Update, text: str, reply_markup=None, parse_mode=None):
    """
    Envía mensajes de forma segura verificando la existencia de los objetos necesarios
    """
    try:
        if update.callback_query:
            if reply_markup:
                return await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            else:
                return await update.callback_query.answer(
                    text=text,
                    show_alert=True
                )
        elif update.message:
            return await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            logger.error(f"No suitable message object found in update: {update}")
            return None
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

async def handle_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    email_addr = update.message.text.strip().lower()
    search_state = context.user_data.get('search_state')
    
    if not search_state:
        keyboard = [[InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]]
        await safe_send_message(
            update,
            "Por favor, selecciona primero una opción del menú.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Validate user's access to this email using database
    from database.connection import execute_query
    from config import ADMIN_ID
    
    user_id = update.effective_user.id
    bot_token = context.bot.token
    
    # Check if the user is superadmin or admin
    is_allowed = False
    
    if user_id == ADMIN_ID:
        is_allowed = True
    else:
        try:
            # Check if the user is admin
            admin_check = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if admin_check and admin_check[0][0] in ['admin', 'super_admin']:
                is_allowed = True
            else:
                # Check if the user has free access
                free_check = execute_query("""
                SELECT free_access FROM users
                WHERE id = %s AND bot_token = %s
                """, (user_id, bot_token))
                
                if free_check and free_check[0][0]:
                    is_allowed = True
                else:
                    # Check if the user has this email assigned
                    email_check = execute_query("""
                    SELECT id FROM user_emails
                    WHERE user_id = %s AND bot_token = %s AND email = %s
                    """, (user_id, bot_token, email_addr))
                    
                    if email_check:
                        is_allowed = True
        except Exception as e:
            logger.error(f"Error checking email permissions: {e}")
            is_allowed = False
    
    if not is_allowed:
        keyboard = [
            [InlineKeyboardButton("↩️ Volver", callback_data=f"{search_state.split('_')[0]}_menu")],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        await safe_send_message(
            update,
            "❌ No tienes autorización para usar este correo.\n"
            "📧 Solo puedes usar los correos asignados a tu cuenta.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Mostrar mensaje de búsqueda
    status_message = await update.message.reply_text("🔍 Buscando...")
    
    try:
        # Determinar el servicio y tipo de regex basado en el estado de búsqueda
        service_mapping = {
            'disney_code': ('disney', None),
            'disney_household': ('disney', 'household'),
            'disney_mydisney': ('disney', 'mydisney'),  # Nueva opción
            'netflix_reset': ('netflix', 'reset'),
            'netflix_home': ('netflix', 'update_home'),
            'netflix_home_code': ('netflix', 'home_code'),
            'netflix_login': ('netflix', 'login_code'),
            'netflix_country': ('netflix', 'country'),
            'netflix_activation': ('netflix', 'activation'),
            'crunchyroll_reset': ('crunchyroll', None),
            'crunchyroll_device': ('crunchyroll', 'device'),
            'prime_otp': ('prime', None),
            'max_reset': ('max', None),
            'max_code': ('max', 'code')
        }
        
        if search_state not in service_mapping:
            await status_message.edit_text(f"❌ Estado de búsqueda no válido: {search_state}")
            return
        
        service, regex_type = service_mapping[search_state]
        
        # Actualizar mensaje con el tipo de búsqueda
        search_description = {
            'disney_code': "código de Disney",
            'disney_household': "código de actualización de hogar Disney",
            'disney_mydisney': "código OTP de My Disney",  # Nueva descripción
            'netflix_reset': "enlace de restablecimiento de Netflix",
            'netflix_home': "enlace de actualización de hogar Netflix",
            'netflix_home_code': "código de hogar Netflix",
            'netflix_login': "código de inicio de sesión Netflix",
            'netflix_country': "país de la cuenta Netflix",
            'netflix_activation': "enlace de activación de Netflix",
            'crunchyroll_reset': "enlace de reset de Crunchyroll",
            'crunchyroll_device': "enlace de verificación de dispositivo Crunchyroll",
            'prime_otp': "código OTP de Prime",
            'max_reset': "enlace de reset de Max",
            'max_code': "código de Max"
        }
        
        await safe_edit_message_text(
            status_message,
            f"🔍 Buscando {search_description.get(search_state, 'información')}..."
        )
        
        # Ejecutar la búsqueda en un hilo separado para no bloquear el bot
        loop = asyncio.get_running_loop()
        
        # Función auxiliar para ejecutar la búsqueda
        def run_search():
            return email_service.search_emails(
                email_addr=email_addr,
                service=service,
                regex_type=regex_type,
                bot_token=bot_token,
                user_id=user_id
            )
        
        # Ejecutar en executor
        result = await loop.run_in_executor(None, run_search)
        
        # Crear teclado base para todos los resultados
        service_menu_name = service + "_menu"
        keyboard_base = [
            [InlineKeyboardButton(f"↩️ Volver al {service.capitalize()}", callback_data=service_menu_name)],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        
        # Procesar resultado
        if result:
            result_value = result['result']
            is_link = result['is_link']
            
            # If this was a Disney code search, trigger email change monitoring
            if search_state.startswith('disney_'):
                try:
                    from handlers.disney_email_monitor import disney_email_monitor
                    # Start monitoring task in background (don't await)
                    asyncio.create_task(
                        disney_email_monitor.check_disney_email_changes(
                            email_addr, bot_token, user_id, context
                        )
                    )
                except Exception as e:
                    logger.error(f"Error starting Disney email monitoring: {e}")
            
            if is_link:
                # Es un enlace, añadir botón para abrirlo
                keyboard = [
                    [InlineKeyboardButton("🔗 Abrir URL", url=result_value)],
                    *keyboard_base
                ]
                await safe_edit_message_text(
                    status_message,
                    f"✅ {search_description.get(search_state, 'Resultado')} encontrado:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                # Es un código u otro valor
                await safe_edit_message_text(
                    status_message,
                    f"✅ {search_description.get(search_state, 'Resultado')}: {result_value}",
                    reply_markup=InlineKeyboardMarkup(keyboard_base)
                )
        else:
            # No se encontró nada
            await safe_edit_message_text(
                status_message,
                f"❌ No se encontró ningún {search_description.get(search_state, 'resultado')} en los correos recientes.",
                reply_markup=InlineKeyboardMarkup(keyboard_base)
            )
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error en handle_email_input: {error_msg}")

        if "No tienes acceso" in error_msg:
            await safe_edit_message_text(status_message, f"❌ {error_msg}")
        elif "timed out" in error_msg.lower():
            await safe_edit_message_text(
                status_message,
                "❌ La búsqueda tardó demasiado. Por favor intenta de nuevo."
            )
        else:
            keyboard = [
                [InlineKeyboardButton("↩️ Volver", callback_data=f"{search_state.split('_')[0]}_menu")],
                [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
            ]
            await safe_edit_message_text(
                status_message,
                f"❌ Error al procesar la solicitud: {error_msg}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def handle_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle URL-related button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('url_'):
        url_hash = query.data
        if hasattr(update, 'url_cache') and url_hash in update.url_cache:
            url = update.url_cache[url_hash]
            await query.answer("¡URL copiada al portapapeles!", show_alert=True)
    elif query.data == 'back_to_menu':
        from handlers.user_handlers import handle_menu_selection
        await handle_menu_selection(update, context)
	
