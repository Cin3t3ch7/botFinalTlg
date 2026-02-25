import os
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram import Update, BotCommand
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from datetime import datetime
import json
from config import ADMIN_ID

# Import handlers
from handlers.admin_handlers import (
    admin_command,
    add_reseller_command,
    remove_reseller_command,
    admin_required,
    msg_command,
    unblock_command  # AÑADIDO: Importar el comando unblock
)
from handlers.extended_handlers import (
    adduser_command,
    removeuser_command,
    eliminar_command,
    garantia_command,
    list_command,
    restart_command,
    stop_command,
    addtime_command,
    addemail_command,
    free_command,
    code_command,
    handle_email_download,    
    UserManager
)
from handlers.user_handlers import start, handle_menu_selection
from handlers.email_search_handlers import (
    handle_disney_menu,
    handle_netflix_menu,
    handle_email_input,
    handle_url_callback,
    handle_crunchyroll_menu,  
    handle_prime_menu,        
    handle_max_menu,
    email_service
)
from handlers.imap_manager import IMAPConnectionPool

# Import utilities
from utils.permission_manager import PermissionManager
from utils.permission_middleware import check_user_permission, check_callback_permission
from utils.logger_utility import bot_logger
from utils.notifications import AdminNotifier
from database.connection import execute_query

# Silenciar logs no deseados
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Solo configurar el logger principal una vez
logger = logging.getLogger('main')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

@admin_required
async def addimap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Añade una nueva configuración IMAP
    Uso: /addimap <domain> <email> <password> <server>
    """
    try:
        args = context.args
        if len(args) < 4:
            await update.message.reply_text(
                "❌ Uso: /addimap <domain> <email> <password> <server>\n"
                "Nota: Si la contraseña contiene espacios, enciérrala entre comillas."
            )
            return
            
        domain = args[0].lower()
        email = args[1]
        server = args[-1]  # El último argumento es el servidor
        
        # Todo lo que está entre email y servidor es la contraseña
        password = " ".join(args[2:-1])
        
        bot_token = context.bot.token
        
        # Verificar si ya existe una configuración para este dominio
        existing = execute_query("""
        SELECT id FROM imap_config
        WHERE domain = %s AND bot_token = %s
        """, (domain, bot_token))
        
        if existing:
            # Actualizar configuración existente
            execute_query("""
            UPDATE imap_config
            SET email = %s, password = %s, imap_server = %s
            WHERE domain = %s AND bot_token = %s
            """, (email, password, server, domain, bot_token))
            
            await update.message.reply_text(
                f"✅ Configuración IMAP actualizada para el dominio {domain}"
            )
        else:
            # Insertar nueva configuración
            execute_query("""
            INSERT INTO imap_config (domain, email, password, imap_server, bot_token)
            VALUES (%s, %s, %s, %s, %s)
            """, (domain, email, password, server, bot_token))
            
            await update.message.reply_text(
                f"✅ Nueva configuración IMAP agregada para el dominio {domain}"
            )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

class EmailBot:
    def __init__(self):
        self.token = None
        self.application = None
        self.imap_pool = IMAPConnectionPool()
        self.permission_manager = PermissionManager()

    async def post_init(self, application):
        """Hook que se ejecuta asíncronamente luego de inicializar la aplicación"""
        try:
            token_short = self.token[:10] if self.token else "Unknown"
            bot_logger.log_info(f"🔄 Verificando comandos registrados en Telegram para bot {token_short}...")
            
            # Lista completa de comandos a mostrar en el menú del cliente
            commands = [
                BotCommand("start", "Inicia el bot y muestra el menú principal"),
                BotCommand("admin", "Otorga privilegios de administrador. Uso: <user_id> <tiempo>"),
                BotCommand("addreseller", "Añade un revendedor. Uso: <user_id> <tiempo> <email1> [email2...]"),
                BotCommand("adduser", "Añade un usuario. Uso: <user_id> <tiempo> [email1 email2...]"),
                BotCommand("removeuser", "Elimina un usuario. Uso: <user_id>"),
                BotCommand("eliminar", "Elimina correos de un usuario. Uso: <user_id> <email1> [email2...]"),
                BotCommand("garantia", "Reemplaza un correo por otro. Uso: <user_id> <old_email> <new_email>"),
                BotCommand("list", "Lista los usuarios"),
                BotCommand("addemail", "Añadir correos a un usuario específico"),
                BotCommand("addimap", "Añade configuración IMAP. Uso: <domain> <email> <password> <server>"),
                BotCommand("free", "Da acceso libre a un usuario. Uso: <user_id>"),
                BotCommand("addtime", "Añade tiempo a usuarios. Uso: <user_id/allid> <tiempo>"),
                BotCommand("code", "Da permiso código Netflix. Uso: <user_id>"),
                BotCommand("reinicio", "Reinicia el bot. Uso: [tiempo]"),
                BotCommand("stop", "Detiene el bot. Uso: [tiempo]"),
                BotCommand("msg", "Envía mensajes a usuarios. Uso: <user_id/allid> <mensaje>")
            ]
            
            # Obtener los comandos que Telegram tiene actualmente
            current_commands = await application.bot.get_my_commands()
            current_dict = {cmd.command: cmd.description for cmd in current_commands}
            new_dict = {cmd.command: cmd.description for cmd in commands}
            
            if current_dict != new_dict:
                bot_logger.log_info(f"⚖️ Detectados cambios en la estructura de comandos para bot {token_short}. Actualizando...")
                await application.bot.set_my_commands(commands)
                bot_logger.log_info(f"✅ Comandos registrados exitosamente para bot {token_short}.")
            else:
                bot_logger.log_info(f"✅ Los comandos ya estaban al día para bot {token_short}. (Se saltó actualización)")
                
        except TelegramError as e:
            bot_logger.log_error(f"❌ Error configurando comandos en Telegram para bot {token_short}: {e}")
        except Exception as e:
            bot_logger.log_error(f"❌ Excepción inesperada en post_init para bot {token_short}: {e}")
        
    def setup(self):
        if not self.token:
            raise ValueError("Token not provided")
            
        # Create PID file and log it
        pid = os.getpid()
        with open('bot.pid', 'w') as f:
            json.dump({
                'pid': pid,
                'token': self.token,
                'timestamp': datetime.now().isoformat(),
                'cmdline': os.path.abspath(__file__)
            }, f)
        bot_logger.log_bot_start(pid)
            
        # Initialize application with custom request timeouts
        request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
        application = ApplicationBuilder().token(self.token).request(request).post_init(self.post_init).build()
        
        # Guardar token y super admin id en el contexto del bot
        application.bot_data["token"] = self.token
        application.bot_data["super_admin_id"] = ADMIN_ID
        
        self.application = application
        
        # Add command handlers - start command doesn't need permission check
        application.add_handler(CommandHandler('start', start))
        
        # Add super admin only commands
        super_admin_handlers = [
            CommandHandler('admin', admin_command),
            CommandHandler('addreseller', add_reseller_command),
            CommandHandler('removereseller', remove_reseller_command)
        ]
        
        for handler in super_admin_handlers:
            application.add_handler(handler)
        
        # Add commands available to both admins and resellers
        shared_handlers = [
            CommandHandler('adduser', adduser_command),
            CommandHandler('addtime', addtime_command),
            CommandHandler('removeuser', removeuser_command),
            CommandHandler('eliminar', eliminar_command),
            CommandHandler('garantia', garantia_command),
            CommandHandler('list', list_command),
            CommandHandler('addemail', addemail_command),
        ]
        
        for handler in shared_handlers:
            application.add_handler(handler)
        
        # Add admin-only commands
        admin_handlers = [
            CommandHandler('reinicio', restart_command),
            CommandHandler('stop', stop_command),
            CommandHandler('free', free_command),
            CommandHandler('code', code_command),
            CommandHandler('addimap', addimap_command),
            CommandHandler('msg', msg_command),
            CommandHandler('unblock', unblock_command)  # AÑADIDO: Comando unblock
        ]
        
        for handler in admin_handlers:
            application.add_handler(handler)
        
        # Add callback query handlers with permission checks
        callback_handlers = [
            CallbackQueryHandler(
                check_callback_permission(handle_menu_selection),
                pattern=r'^(disney_menu|netflix_menu|crunchyroll_menu|prime_menu|max_menu|info_user|main_menu|back_to_menu|view_my_info|config_imap|add_admin|add_reseller|imap_details_\d+|imap_delete_\d+)$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_disney_menu),
                pattern='^disney_(code|home|mydisney)$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_netflix_menu),
                pattern='^netflix_(reset_link|update_home|home_code|login_code|country|activation)$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_crunchyroll_menu),
                pattern='^crunchyroll_(reset|device)$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_prime_menu),
                pattern='^prime_otp$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_max_menu),
                pattern='^max_(reset|code)$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_url_callback),
                pattern='^url_.*$'
            ),
            CallbackQueryHandler(
                check_callback_permission(handle_email_download),
                pattern=r'^download_emails_\d+$'
            )
        ]
        
        for handler in callback_handlers:
            application.add_handler(handler)
        
        # Add message handler for email input with permission check
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            check_user_permission(handle_email_input)
        ))
        
        # Setup error handler
        application.add_error_handler(self.error_handler)
        
        bot_logger.log_bot_ready()
        return application
    
    async def error_handler(self, update: Update, context):
        """Global error handler for all updates."""
        error_message = f"An error occurred: {context.error}"
        bot_logger.log_error(error_message)
        
        try:
            # Check if update and message exist
            if update and hasattr(update, 'effective_message') and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Ha ocurrido un error. Por favor, intenta nuevamente más tarde."
                )
            elif update and hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.answer(
                    "❌ Ha ocurrido un error. Por favor, intenta nuevamente más tarde.",
                    show_alert=True
                )
        except Exception as e:
            bot_logger.log_error(f"Error sending error message: {e}")
            # Log the full update object for debugging
            bot_logger.log_error(f"Update object: {update}")
    
    def cleanup(self):
        """Cleanup resources before shutdown"""
        try:
            self.imap_pool.close_all_connections()
            email_service.cleanup()  # Cerrar conexiones del servicio de email
            if os.path.exists('bot.pid'):
                os.remove('bot.pid')
            logger.info("Bot cleanup completed successfully")
        except Exception as e:
            bot_logger.log_error(f"Error during cleanup: {e}")
