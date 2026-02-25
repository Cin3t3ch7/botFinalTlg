from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import json
import os
import psutil
import subprocess
import asyncio
from datetime import datetime, timedelta
from config import ADMIN_ID
from handlers.admin_handlers import admin_required, AdminManager
from utils.logger_utility import bot_logger
from utils.notifications import AdminNotifier
from database.connection import execute_query
from datetime import datetime, timedelta 
from utils.permission_middleware import reseller_can_manage_user, admin_or_reseller_required

class UserManager:
    def __init__(self):
        pass
        
    def add_user(self, user_id, expiration, bot_token, created_by=None):
        """
        Add a new user with expiration date
        
        Args:
            user_id (int): Telegram user ID
            expiration (datetime): Expiration date for user access
            bot_token (str): Bot token
            created_by (int, optional): ID of admin who created the user
        """
        try:
            # Obtener el ID del rol de usuario
            role_result = execute_query("SELECT id FROM roles WHERE name = 'user'")
            if not role_result:
                raise ValueError("No se encontró el rol de usuario en la base de datos")
                
            user_role_id = role_result[0][0]
            
            # Verificar si el usuario ya existe
            user_exists = execute_query(
                "SELECT id FROM users WHERE id = %s AND bot_token = %s",
                (user_id, bot_token)
            )
            
            if user_exists:
                # Actualizar usuario existente
                execute_query("""
                UPDATE users 
                SET role_id = %s, access_until = %s
                WHERE id = %s AND bot_token = %s
                """, (user_role_id, expiration, user_id, bot_token))
            else:
                # Crear nuevo usuario
                execute_query("""
                INSERT INTO users (id, role_id, bot_token, access_until, created_by)
                VALUES (%s, %s, %s, %s, %s)
                """, (user_id, user_role_id, bot_token, expiration, created_by))
                
            return True
        except Exception as e:
            bot_logger.log_error(f"Error al añadir usuario {user_id}: {str(e)}")
            raise
    
    def is_user_valid(self, user_id, bot_token):
        """Verifica si un usuario está activo basado en su expiración general"""
        try:
            result = execute_query("""
            SELECT access_until FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not result:
                return False
                
            expiration = result[0][0]
            return datetime.now() < expiration
        except Exception as e:
            bot_logger.log_error(f"Error verificando validez del usuario {user_id}: {str(e)}")
            return False
            
    def get_user_emails(self, user_id, bot_token):
        """Obtiene la lista de correos autorizados para un usuario"""
        try:
            # Verificar si el usuario tiene acceso libre primero
            free_result = execute_query("""
            SELECT free_access FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if free_result and free_result[0][0]:
                # Usuario con acceso libre, devolver vacío para indicar acceso a todos
                return []
                
            # Obtener correos asignados
            email_result = execute_query("""
            SELECT email FROM user_emails
            WHERE user_id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            return [email[0] for email in email_result] if email_result else []
        except Exception as e:
            bot_logger.log_error(f"Error obteniendo correos del usuario {user_id}: {str(e)}")
            return []

    def add_emails(self, user_id, emails, bot_token, added_by=None):
        """
        Add emails to a user account
        
        Args:
            user_id (int): Telegram user ID
            emails (list): List of email addresses to add
            bot_token (str): Bot token
            added_by (int, optional): ID of admin who added the emails
        """
        try:
            for email in emails:
                execute_query("""
                INSERT INTO user_emails (user_id, bot_token, email)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, bot_token, email) DO NOTHING
                """, (user_id, bot_token, email.lower()))
                
            return True
        except Exception as e:
            bot_logger.log_error(f"Error añadiendo correos al usuario {user_id}: {str(e)}")
            raise

    def remove_user(self, user_id, bot_token):
        """
        Remove a user and all their data
        
        Args:
            user_id (int): Telegram user ID
            bot_token (str): Bot token
        """
        try:
            # Eliminar todos los correos del usuario
            execute_query("""
            DELETE FROM user_emails
            WHERE user_id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            # Eliminar el usuario
            execute_query("""
            DELETE FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            return True
        except Exception as e:
            bot_logger.log_error(f"Error eliminando al usuario {user_id}: {str(e)}")
            raise

    def remove_emails(self, user_id, emails_to_remove, bot_token):
        """
        Remove specific emails from a user's authorized email list
        
        Args:
            user_id (int): Telegram user ID
            emails_to_remove (list): List of email addresses to remove
            bot_token (str): Bot token
        
        Returns:
            tuple: (number of emails removed, list of emails not found)
        """
        try:
            removed_count = 0
            not_found = []
            
            # Verificar qué correos existen para este usuario
            existing_emails = execute_query("""
            SELECT email FROM user_emails
            WHERE user_id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            existing_set = {email[0].lower() for email in existing_emails} if existing_emails else set()
            
            for email in emails_to_remove:
                email_lower = email.lower()
                if email_lower in existing_set:
                    # Eliminar el correo
                    execute_query("""
                    DELETE FROM user_emails
                    WHERE user_id = %s AND bot_token = %s AND email = %s
                    """, (user_id, bot_token, email_lower))
                    removed_count += 1
                else:
                    not_found.append(email)
                    
            return removed_count, not_found
        except Exception as e:
            bot_logger.log_error(f"Error eliminando correos del usuario {user_id}: {str(e)}")
            raise
    
    def replace_email(self, user_id, old_email, new_email, bot_token):
        """
        Replace an old email with a new one
        
        Args:
            user_id (int): Telegram user ID
            old_email (str): Old email address to replace
            new_email (str): New email address
            bot_token (str): Bot token
        
        Returns:
            bool: True if email was replaced, False otherwise
        """
        try:
            # Verificar si el correo viejo existe
            old_exists = execute_query("""
            SELECT id FROM user_emails
            WHERE user_id = %s AND bot_token = %s AND email = %s
            """, (user_id, bot_token, old_email.lower()))
            
            if not old_exists:
                return False
                
            # Eliminar el correo viejo
            execute_query("""
            DELETE FROM user_emails
            WHERE user_id = %s AND bot_token = %s AND email = %s
            """, (user_id, bot_token, old_email.lower()))
            
            # Añadir el nuevo correo
            execute_query("""
            INSERT INTO user_emails (user_id, bot_token, email)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, bot_token, email) DO NOTHING
            """, (user_id, bot_token, new_email.lower()))
            
            # Registrar la garantía
            execute_query("""
            INSERT INTO warranty_records (user_id, bot_token, old_email, new_email)
            VALUES (%s, %s, %s, %s)
            """, (user_id, bot_token, old_email.lower(), new_email.lower()))
            
            return True
        except Exception as e:
            bot_logger.log_error(f"Error reemplazando correo del usuario {user_id}: {str(e)}")
            raise
    
    def get_all_users(self, bot_token):
        """Get information about all users in the system"""
        try:
            users = []
            current_time = datetime.now()
            
            # Obtener usuarios con sus roles y correos
            result = execute_query("""
            SELECT u.id, u.access_until, u.created_at, r.name as role_name, 
                u.free_access, u.created_by
            FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.bot_token = %s
            ORDER BY u.access_until
            """, (bot_token,))
            
            if not result:
                return []
                    
            for user_row in result:
                user_id, expiration, created_at, role_name, free_access, created_by = user_row
                
                # Obtener correos del usuario
                emails_result = execute_query("""
                SELECT email FROM user_emails
                WHERE user_id = %s AND bot_token = %s
                """, (user_id, bot_token))
                
                user_emails = [{"email": email[0]} for email in emails_result] if emails_result else []
                
                # Calcular tiempo restante (protegiendo contra None)
                if expiration is not None:
                    time_remaining = expiration - current_time
                else:
                    time_remaining = timedelta(0)  # Valor predeterminado si es None
                
                user_info = {
                    'user_id': user_id,
                    'expiration': expiration or datetime.now(),  # Valor predeterminado si es None
                    'time_remaining': time_remaining,
                    'role': role_name,
                    'emails': user_emails,
                    'total_emails': len(user_emails),
                    'created_at': created_at,
                    'free_access': free_access,
                    'created_by': created_by
                }
                
                users.append(user_info)
                    
            return users
        except Exception as e:
            bot_logger.log_error(f"Error obteniendo todos los usuarios: {str(e)}")
            return []

async def _process_bot_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action_type="restart"):
    """
    Función base para procesar acciones de reinicio o detención del bot
    
    Args:
        update: Objeto Update de Telegram
        context: Contexto del comando
        action_type: Tipo de acción ('restart' o 'stop')
    """
    try:
        # Parsear el delay si se proporciona
        time_str = context.args[0] if context.args else "0s"
        amount = int(time_str[:-1])
        unit = time_str[-1]
        
        if unit == 's':
            delay = amount
        elif unit == 'm':
            delay = amount * 60
        else:
            await update.message.reply_text("❌ Formato de tiempo inválido. Use 's' para segundos o 'm' para minutos.")
            return
        
        # Mensaje según el tipo de acción
        if action_type == "restart":
            await update.message.reply_text(f"🔄 Iniciando secuencia de reinicio con {delay} segundos de retraso...")
        else:  # stop
            await update.message.reply_text(f"🛑 El bot se detendrá en {delay} segundos...")
        
        # Esperar el tiempo indicado
        if delay > 0:
            await asyncio.sleep(delay)
        
        # Obtener información de PID actual
        pid_data = read_pid_files()
        
        # Acciones específicas por tipo
        if action_type == "restart":
            # Obtener la ruta del script y preparar reinicio
            current_script = os.path.abspath(__file__)
            bot_script = os.path.join(os.path.dirname(os.path.dirname(current_script)), 'botNew.py')
            
            if not os.path.exists(bot_script):
                await update.message.reply_text(f"❌ Error: No se encontró el archivo {bot_script}")
                return
                
            # Crear marker de reinicio
            with open('restart.marker', 'w') as f:
                f.write('1')
                
            # Iniciar nuevo proceso
            if os.name == 'nt':  # Windows
                subprocess.Popen(['python', bot_script], 
                               creationflags=subprocess.CREATE_NEW_CONSOLE,
                               cwd=os.path.dirname(bot_script))
            else:  # Linux/Unix
                subprocess.Popen(['python3', bot_script],
                               start_new_session=True,
                               cwd=os.path.dirname(bot_script))
                
            await update.message.reply_text("✅ Bot reiniciándose...")
            await asyncio.sleep(2)
            
        else:  # stop
            # Solo detener el proceso actual y sus hijos
            if pid_data['current'] and 'pid' in pid_data['current']:
                current_pid = pid_data['current']['pid']
                killed_process = kill_process_tree(current_pid)
                
                if killed_process:
                    status_msg = "✅ Detención completada:\n"
                    status_msg += f"- Proceso terminado: PID {current_pid}\n"
                    for child in killed_process.get('children', []):
                        status_msg += f"  • Proceso hijo: {child['name']} (PID {child['pid']})\n"
                        
                    await update.message.reply_text(status_msg)
                
                # Eliminar archivos PID
                if os.path.exists('bot.pid'):
                    os.remove('bot.pid')
                if os.path.exists('bot.old.pid'):
                    os.remove('bot.old.pid')
            
            await update.message.reply_text("🛑 Bot detenido.")
            
        # Salir del proceso actual
        exit_code = 0 if action_type == "restart" else 1
        os._exit(exit_code)
        
    except (IndexError, ValueError):
        await update.message.reply_text(f"❌ Uso: /{action_type} [tiempo]")
    except Exception as e:
        error_msg = f"❌ Error durante la acción '{action_type}': {str(e)}"
        bot_logger.log_error(error_msg)
        await update.message.reply_text(error_msg)

@reseller_can_manage_user
async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Add a new user with optional emails
    Usage: /adduser <user_id> <time> [email1 email2 ...]
    Time format: <number><unit> where unit is 'd' for days or 'm' for minutes
    """
    try:
        # Check for minimum required arguments
        if len(context.args) < 2:
            await update.message.reply_text("❌ Uso: /adduser <user_id> <tiempo> [email1 email2 ...]")
            return

        # Parse user_id and time
        user_id = int(context.args[0])
        time_str = context.args[1]
        emails = context.args[2:] if len(context.args) > 2 else []

        # Validate time format
        if not time_str[-1] in ['d', 'm']:
            await update.message.reply_text("❌ Formato de tiempo inválido. Use 'd' para días o 'm' para minutos.")
            return

        # Parse time value
        try:
            amount = int(time_str[:-1])
            unit = time_str[-1]
        except ValueError:
            await update.message.reply_text("❌ Valor de tiempo inválido. Debe ser un número seguido de 'd' o 'm'.")
            return

        # Calculate expiration
        if unit == 'd':
            expiration = datetime.now() + timedelta(days=amount)
        else:  # unit == 'm'
            expiration = datetime.now() + timedelta(minutes=amount)

        # Add user
        user_manager = UserManager()
        user_manager.add_user(
            user_id, 
            expiration, 
            context.bot.token,
            created_by=update.effective_user.id
        )

        # Add emails if provided
        if emails:
            user_manager.add_emails(
                user_id,
                emails,
                context.bot.token,
                added_by=update.effective_user.id
            )

        # Prepare response message
        response = (
            f"✅ Usuario {user_id} añadido exitosamente\n"
            f"⏱️ Expira: {expiration.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        if emails:
            response += f"📧 Correos añadidos: {len(emails)}"

        await update.message.reply_text(response)

        # Notificar al administrador si un revendedor realiza la acción
        if update.effective_user.id != ADMIN_ID:
            admin_manager = AdminManager()
            if not admin_manager.is_admin(update.effective_user.id, context.bot.token):
                try:
                    await AdminNotifier.notify_admin_action(
                        context,
                        update.effective_user.id,
                        "añadir_usuario",
                        f"Usuario añadido: {user_id}\nExpira: {expiration.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except Exception as e:
                    bot_logger.log_error(f"Error notificando al admin: {str(e)}")

    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error inesperado: {str(e)}")

@reseller_can_manage_user
async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Elimina un usuario del sistema completamente
    Uso: /removeuser <user_id>
    """
    try:
        if len(context.args) != 1:
            await update.message.reply_text("❌ Uso: /removeuser <user_id>")
            return
            
        user_id = int(context.args[0])
        bot_token = context.bot.token
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Eliminar usuario
        user_manager = UserManager()
        if user_manager.remove_user(user_id, bot_token):
            # Registrar acción
            bot_logger.logger.info(f"Usuario {user_id} eliminado por {update.effective_user.id}")
            
            # Notificar al administrador si un revendedor realiza la acción
            if update.effective_user.id != ADMIN_ID:
                admin_manager = AdminManager()
                if not admin_manager.is_admin(update.effective_user.id, bot_token):
                    try:
                        await AdminNotifier.notify_admin_action(
                            context,
                            update.effective_user.id,
                            "eliminar_usuario",
                            f"Usuario eliminado: {user_id}"
                        )
                    except Exception as e:
                        bot_logger.log_error(f"Error notificando al admin: {str(e)}")
            
            await update.message.reply_text(f"✅ Usuario {user_id} eliminado exitosamente con todos sus datos")
        else:
            await update.message.reply_text(f"❌ Error al eliminar el usuario {user_id}")
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@reseller_can_manage_user
async def addemail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando para añadir correos a un usuario
    Uso: /addemail <user_id> <email1> [email2 ...]
    """
    try:
        if len(context.args) < 2:
            await update.message.reply_text("Uso: /addemail <user_id> <email1> [email2 ...]")
            return
            
        user_id = int(context.args[0])
        emails = context.args[1:]
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, context.bot.token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Añadir correos
        user_manager = UserManager()
        user_manager.add_emails(
            user_id, 
            emails,
            context.bot.token,
            added_by=update.effective_user.id
        )
        
        await update.message.reply_text(
            f"✅ Correos añadidos exitosamente para el usuario {user_id}\n"
            f"📧 Correos: {', '.join(emails)}"
        )
        
        # Notificar al administrador si un revendedor realiza la acción
        if update.effective_user.id != ADMIN_ID:
            admin_manager = AdminManager()
            if not admin_manager.is_admin(update.effective_user.id, context.bot.token):
                try:
                    await AdminNotifier.notify_admin_action(
                        context,
                        update.effective_user.id,
                        "añadir_correos",
                        f"Usuario: {user_id}\nCorreos: {', '.join(emails)}"
                    )
                except Exception as e:
                    bot_logger.log_error(f"Error notificando al admin: {str(e)}")
        
    except ValueError:
        await update.message.reply_text("❌ Error: ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@reseller_can_manage_user
async def eliminar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Remove email addresses from a user's authorized list
    Usage: /eliminar <user_id> <email1> [email2 ...]
    """
    try:
        if len(context.args) < 2:
            await update.message.reply_text("❌ Uso: /eliminar <user_id> <email1> [email2 ...]")
            return
            
        user_id = int(context.args[0])
        emails = context.args[1:]
        caller_id = update.effective_user.id
        bot_token = context.bot.token
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Eliminar correos
        user_manager = UserManager()
        removed_count, not_found = user_manager.remove_emails(user_id, emails, bot_token)
        
        # Notificar al administrador si un revendedor realiza la acción
        if caller_id != ADMIN_ID:
            admin_manager = AdminManager()
            if not admin_manager.is_admin(caller_id, bot_token):
                try:
                    await AdminNotifier.notify_admin_action(
                        context,
                        caller_id,
                        "eliminar_correos",
                        f"Usuario: {user_id}\nCorreos eliminados: {', '.join(emails)}"
                    )
                except Exception as e:
                    bot_logger.log_error(f"Error notificando al admin: {str(e)}")
        
        # Preparar mensaje de respuesta
        response = []
        if removed_count > 0:
            response.append(f"✅ Se eliminaron {removed_count} correo(s) exitosamente")
        
        if not_found:
            response.append(f"❌ No se encontraron los siguientes correos: {', '.join(not_found)}")
            
        if not response:
            response.append("❌ No se realizó ninguna eliminación")
            
        await update.message.reply_text("\n".join(response))
        
    except ValueError:
        await update.message.reply_text("❌ Error: El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@reseller_can_manage_user
async def garantia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reemplaza un correo por otro para garantía
    Uso: /garantia <user_id> <old_email> <new_email>
    """
    try:
        if len(context.args) != 3:
            await update.message.reply_text(
                "❌ Uso: /garantia <user_id> <old_email> <new_email>"
            )
            return
            
        user_id = int(context.args[0])
        old_email = context.args[1].lower()
        new_email = context.args[2].lower()
        caller_id = update.effective_user.id
        bot_token = context.bot.token
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Realizar el reemplazo
        user_manager = UserManager()
        if user_manager.replace_email(user_id, old_email, new_email, bot_token):
            # Notificar al administrador si un revendedor realiza la acción
            if caller_id != ADMIN_ID:
                admin_manager = AdminManager()
                if not admin_manager.is_admin(caller_id, bot_token):
                    try:
                        await AdminNotifier.notify_admin_action(
                            context,
                            caller_id,
                            "garantia",
                            f"Usuario: {user_id}\nCorreo anterior: {old_email}\nNuevo correo: {new_email}"
                        )
                    except Exception as e:
                        bot_logger.log_error(f"Error notificando al admin: {str(e)}")
            
            # Mensaje para quien ejecutó el comando
            await update.message.reply_text(
                f"✅ Correo reemplazado exitosamente\n"
                f"📧 Anterior: {old_email}\n"
                f"📧 Nuevo: {new_email}"
            )

            # Notificar al usuario afectado
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ Garantía Aplicada\n\n"
                         f"✅ Correo reemplazado\n"
                         f"📧 Anterior: {old_email}\n"
                         f"📧 Nuevo: {new_email}"
                )
            except Exception as e:
                bot_logger.log_error(f"Error notificando al usuario {user_id}: {str(e)}")
                await update.message.reply_text(
                    "⚠️ No se pudo notificar al usuario del cambio de correo.\n"
                    "❗️ Es posible que el usuario no haya iniciado el bot."
                )
        else:
            await update.message.reply_text(
                f"❌ No se encontró el correo {old_email} para el usuario {user_id}"
            )
            
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
        bot_logger.log_error(f"Error en garantia_command: {str(e)}")

@admin_or_reseller_required
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista usuarios con opción de descarga de correos"""
    try:
        user_id = update.effective_user.id
        bot_token = context.bot.token
        user_manager = UserManager()
        admin_manager = AdminManager()
        
        # Determinar qué usuarios mostrar
        if user_id == ADMIN_ID or admin_manager.is_admin(user_id, bot_token):
            # Administradores ven todos los usuarios
            all_users = user_manager.get_all_users(bot_token)
            
            # Si se proporcionó un ID específico, filtrar
            if context.args:
                try:
                    target_user_id = int(context.args[0])
                    users_to_show = [user for user in all_users if user['user_id'] == target_user_id]
                    if not users_to_show:
                        await update.message.reply_text(f"❌ No se encontró el usuario con ID {target_user_id}")
                        return
                except ValueError:
                    await update.message.reply_text("❌ El ID de usuario debe ser un número")
                    return
            else:
                users_to_show = all_users
        else:
            # Verificar si es revendedor
            role_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            is_reseller = role_result and role_result[0][0] == 'reseller'
            
            if is_reseller:
                # Obtener usuarios creados por este revendedor
                all_users = user_manager.get_all_users(bot_token)
                users_to_show = [user for user in all_users if user['created_by'] == user_id]
                
                if not users_to_show:
                    await update.message.reply_text("📝 No has creado ningún usuario aún")
                    return
            else:
                await update.message.reply_text("❌ No tienes permisos para usar este comando")
                return

        # Si no hay usuarios
        if not users_to_show:
            await update.message.reply_text("📝 No hay usuarios registrados en el sistema")
            return

        # Enviar información de cada usuario en mensajes separados
        for user in users_to_show:
            time_remaining = user['time_remaining']
            days = time_remaining.days
            hours = time_remaining.seconds // 3600
            
            message = (
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ID: {user['user_id']}\n"
                f"⏳ Tiempo restante: {days}d {hours}h\n"
                f"📅 Expira: {user['expiration'].strftime('%Y-%m-%d %H:%M')}\n"
                f"🔑 Rol: {user['role']}\n"
                f"📧 Total correos: {user['total_emails']}"
            )
            
            # Crear botones para este usuario
            keyboard = []
            if user['total_emails'] > 0:
                keyboard.append([
                    InlineKeyboardButton(
                        "📥 Descargar correos", 
                        callback_data=f"download_emails_{user['user_id']}"
                    )
                ])
            
            # Enviar mensaje con botones si tiene correos
            if keyboard:
                await update.message.reply_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(message)
            
            await asyncio.sleep(0.5)
        
        # Mensaje final - CORREGIDO SIN USAR MARKDOWN
        keyboard = [[InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]]
        await update.message.reply_text(
            "📝 Fin del listado",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error al listar usuarios: {str(e)}")

@admin_required
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reinicia el bot después de un tiempo opcional"""
    await _process_bot_action(update, context, "restart")

@admin_required
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detiene el bot después de un tiempo opcional"""
    await _process_bot_action(update, context, "stop")

@admin_required
async def free_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Da acceso libre a cualquier correo a un usuario específico
    Uso: /free <user_id>
    """
    try:
        if len(context.args) != 1:
            await update.message.reply_text("❌ Uso: /free <user_id>")
            return
            
        user_id = int(context.args[0])
        bot_token = context.bot.token
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Marcar al usuario como free_access
        execute_query("""
        UPDATE users
        SET free_access = TRUE
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
            
        # Registrar en el log
        bot_logger.logger.info(f"Free access granted to user {user_id} by admin {update.effective_user.id}")
        
        await update.message.reply_text(
            f"✅ Acceso libre otorgado al usuario {user_id}\n"
            "📧 Ahora puede usar cualquier correo sin restricciones"
        )
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@admin_required
async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Da permiso a un usuario para usar el apartado de código de inicio de sesión de Netflix
    Uso: /code <user_id>
    """
    try:
        if len(context.args) != 1:
            await update.message.reply_text("❌ Uso: /code <user_id>")
            return
            
        user_id = int(context.args[0])
        bot_token = context.bot.token
        
        # Verificar si el usuario existe
        user_exists = execute_query("""
        SELECT id FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_exists:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
            return
        
        # Marcar al usuario con code_access
        execute_query("""
        UPDATE users
        SET code_access = TRUE
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
            
        # Registrar en el log
        bot_logger.logger.info(f"Code access granted to user {user_id} by admin {update.effective_user.id}")
        
        await update.message.reply_text(
            f"✅ Permiso de código otorgado al usuario {user_id}\n"
            "🎥 Ahora puede usar la opción Código de Login en Netflix"
        )
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@reseller_can_manage_user
async def addtime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) != 2:
            await update.message.reply_text("❌ Uso: /addtime <user_id/allid> <tiempo>")
            return

        target_id = context.args[0].lower()
        time_str = context.args[1]
        bot_token = context.bot.token
        caller_id = update.effective_user.id

        # Validar formato de tiempo
        if not time_str[-1] in ['d', 'm']:
            await update.message.reply_text("❌ Formato de tiempo inválido. Use 'd' para días o 'm' para minutos.")
            return

        try:
            amount = int(time_str[:-1])
            unit = time_str[-1]
        except ValueError:
            await update.message.reply_text("❌ El tiempo debe ser un número seguido de 'd' o 'm'")
            return

        # Convertir tiempo a timedelta
        if unit == 'd':
            time_delta = timedelta(days=amount)
        else:  # unit == 'm'
            time_delta = timedelta(minutes=amount)

        # Solo superadmin o admin pueden usar 'allid'
        if target_id == 'allid':
            # Verificar si el usuario tiene permisos para 'allid'
            if caller_id != ADMIN_ID:
                admin_manager = AdminManager()
                if not admin_manager.is_admin(caller_id, bot_token):
                    await update.message.reply_text("❌ Solo los administradores pueden actualizar el tiempo para todos los usuarios.")
                    return
            
            # Actualizar todos los usuarios
            try:
                execute_query("""
                UPDATE users
                SET access_until = CASE
                    WHEN access_until < CURRENT_TIMESTAMP THEN CURRENT_TIMESTAMP + %s
                    ELSE access_until + %s
                END
                WHERE bot_token = %s
                """, (time_delta, time_delta, bot_token))
                
                await update.message.reply_text(
                    f"✅ Tiempo actualizado para todos los usuarios\n"
                    f"⏱️ Tiempo añadido: {time_str}"
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Error al actualizar tiempo para todos los usuarios: {str(e)}")
        else:
            # Actualizar un usuario específico
            try:
                user_id = int(target_id)
                
                # Verificar si el usuario existe
                user_result = execute_query("""
                SELECT access_until FROM users
                WHERE id = %s AND bot_token = %s
                """, (user_id, bot_token))
                
                if not user_result:
                    await update.message.reply_text(f"❌ El usuario {user_id} no existe")
                    return
                
                current_expiration = user_result[0][0]
                
                # Si ya expiró, comenzar desde ahora
                if current_expiration < datetime.now():
                    new_expiration = datetime.now() + time_delta
                else:
                    new_expiration = current_expiration + time_delta
                
                execute_query("""
                UPDATE users
                SET access_until = %s
                WHERE id = %s AND bot_token = %s
                """, (new_expiration, user_id, bot_token))
                
                await update.message.reply_text(
                    f"✅ Tiempo actualizado para el usuario {user_id}\n"
                    f"⏱️ Tiempo añadido: {time_str}\n"
                    f"📅 Nueva fecha de expiración: {new_expiration.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                
                # Notificar al administrador si un revendedor realiza la acción
                if caller_id != ADMIN_ID:
                    admin_manager = AdminManager()
                    if not admin_manager.is_admin(caller_id, bot_token):
                        try:
                            await AdminNotifier.notify_admin_action(
                                context,
                                caller_id,
                                "añadir_tiempo",
                                f"Usuario: {user_id}\nTiempo añadido: {time_str}\nNueva expiración: {new_expiration.strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                        except Exception as e:
                            bot_logger.log_error(f"Error notificando al admin: {str(e)}")
                
            except ValueError:
                await update.message.reply_text("❌ El ID de usuario debe ser un número")
            except Exception as e:
                await update.message.reply_text(f"❌ Error al actualizar el tiempo: {str(e)}")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def handle_email_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle email download button callbacks"""
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = int(query.data.split('_')[2])
        caller_id = query.from_user.id
        bot_token = context.bot.token
        
        # Verificar permisos del usuario que solicita la descarga
        if caller_id != ADMIN_ID:
            admin_manager = AdminManager()
            is_admin = admin_manager.is_admin(caller_id, bot_token)
            
            if not is_admin:
                # Verificar si es un revendedor con acceso a este usuario
                role_result = execute_query("""
                SELECT r.name FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.id = %s AND u.bot_token = %s
                """, (caller_id, bot_token))
                
                is_reseller = role_result and role_result[0][0] == 'reseller'
                
                if is_reseller:
                    # Verificar si el revendedor creó este usuario
                    creator_check = execute_query("""
                    SELECT id FROM users
                    WHERE id = %s AND bot_token = %s AND created_by = %s
                    """, (user_id, bot_token, caller_id))
                    
                    if not creator_check:
                        await query.message.reply_text("❌ Solo puedes descargar correos de usuarios que tú hayas creado.")
                        return
                else:
                    await query.message.reply_text("❌ No tienes permisos para descargar correos.")
                    return
        
        # Obtener correos del usuario
        emails_result = execute_query("""
        SELECT email FROM user_emails
        WHERE user_id = %s AND bot_token = %s
        ORDER BY email
        """, (user_id, bot_token))
        
        if not emails_result:
            await query.message.reply_text(f"❌ No se encontraron correos para el usuario {user_id}")
            return
        
        user_emails = [email[0] for email in emails_result]
        
        file_path = f"temp_{user_id}_emails.txt"
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"Lista de correos para usuario {user_id}\n")
            f.write("=" * 50 + "\n\n")
            
            for email in user_emails:
                f.write(f"{email}\n")
            
            f.write(f"\nTotal de correos: {len(user_emails)}")
        
        await query.message.reply_document(
            document=open(file_path, 'rb'),
            filename=f"correos_usuario_{user_id}.txt",
            caption=f"📧 Lista de correos para usuario {user_id}"
        )
        
        os.remove(file_path)
        
    except Exception as e:
        error_msg = f"❌ Error al descargar correos: {str(e)}"
        bot_logger.log_error(error_msg)
        await query.message.reply_text(error_msg)

def read_pid_files():
    """Read current and old PID files"""
    pid_data = {'current': None, 'old': None}
    
    try:
        # Read current PID file
        if os.path.exists('bot.pid'):
            with open('bot.pid', 'r') as f:
                pid_data['current'] = json.load(f)
                
        # Read old PID file
        if os.path.exists('bot.old.pid'):
            with open('bot.old.pid', 'r') as f:
                pid_data['old'] = json.load(f)
    except Exception as e:
        print(f"Error reading PID files: {e}")
        
    return pid_data

def kill_process_tree(pid):
    """Kill a process and all its children"""
    try:
        parent = psutil.Process(pid)
        children_info = []
        
        # Get all children first
        children = parent.children(recursive=True)
        for child in children:
            try:
                children_info.append({
                    'pid': child.pid,
                    'name': child.name()
                })
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
                
        # Kill parent after children
        parent_name = parent.name()
        parent.kill()
        
        return {
            'pid': pid,
            'name': parent_name,
            'children': children_info
        }
        
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

# Export all commands
__all__ = [
    'adduser_command',
    'removeuser_command',
    'eliminar_command',
    'garantia_command',
    'list_command',
    'restart_command',
    'stop_command',
    'addtime_command',
    'addemail_command',
    'free_command',
    'code_command',
    'handle_email_download',
    'UserManager'
]
