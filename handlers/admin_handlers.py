from telegram import Update
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
from config import ADMIN_ID
from utils.logger_utility import bot_logger
from functools import wraps
from database.connection import execute_query

class AdminManager:
    def __init__(self):
        pass
            
    def is_admin(self, user_id, bot_token):
        """Verifica si un usuario es administrador"""
        # El super admin siempre es admin
        if user_id == ADMIN_ID:
            return True
            
        # Verificar en la base de datos
        try:
            result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            return result and result[0][0] in ['admin', 'super_admin']
        except Exception as e:
            bot_logger.log_error(f"Error verificando admin: {str(e)}")
            return False

    def is_super_admin(self, user_id):
        """Verifica si un usuario es super administrador"""
        return user_id == ADMIN_ID

def admin_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        admin_manager = AdminManager()
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)
            
        # Verificar si es admin en la base de datos
        is_valid_admin = admin_manager.is_admin(user_id, bot_token)
        
        if not is_valid_admin:
            await update.message.reply_text("❌ Este comando está restringido solo para administradores.")
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Otorga permisos de administrador a un usuario"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Solo el super administrador puede otorgar privilegios de administrador.")
        return
    
    try:
        user_id = int(context.args[0])
        time_str = context.args[1]
        
        if user_id == ADMIN_ID:
            await update.message.reply_text("❌ No se pueden modificar los privilegios del super administrador.")
            return
        
        amount = int(time_str[:-1])
        unit = time_str[-1]
        
        if unit == 'd':
            expiration = datetime.now() + timedelta(days=amount)
        elif unit == 'm':
            expiration = datetime.now() + timedelta(minutes=amount)
        else:
            await update.message.reply_text("❌ Formato de tiempo inválido. Use 'd' para días o 'm' para minutos.")
            return
            
        # Obtener el ID del rol de admin
        role_result = execute_query("SELECT id FROM roles WHERE name = 'admin'")
        if not role_result:
            await update.message.reply_text("❌ Error: No se encontró el rol de administrador en la base de datos.")
            return
            
        admin_role_id = role_result[0][0]
        
        # Verificar si el usuario existe
        user_exists = execute_query(
            "SELECT id FROM users WHERE id = %s AND bot_token = %s",
            (user_id, context.bot.token)
        )
        
        if user_exists:
            # Actualizar rol y tiempo de expiración
            execute_query("""
            UPDATE users 
            SET role_id = %s, access_until = %s
            WHERE id = %s AND bot_token = %s
            """, (admin_role_id, expiration, user_id, context.bot.token))
        else:
            # Crear nuevo usuario con rol de admin
            execute_query("""
            INSERT INTO users (id, role_id, bot_token, access_until, created_by)
            VALUES (%s, %s, %s, %s, %s)
            """, (user_id, admin_role_id, context.bot.token, expiration, update.effective_user.id))
            
        await update.message.reply_text(
            f"✅ Privilegios de administrador otorgados a {user_id}\n"
            f"📅 Expiración: {expiration.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
    except (IndexError, ValueError):
        await update.message.reply_text("Uso: /admin <user_id> <tiempo>")

@admin_required
async def add_reseller_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Añade un nuevo revendedor con correos autorizados
    Uso: /addreseller <user_id> <tiempo> <email1> [email2 ...]
    """
    try:
        if len(context.args) < 3:
            await update.message.reply_text(
                "❌ Uso: /addreseller <user_id> <tiempo> <email1> [email2 ...]"
            )
            return
            
        user_id = int(context.args[0])
        time_str = context.args[1]
        emails = context.args[2:]
        
        if not time_str[-1] in ['d', 'm']:
            await update.message.reply_text(
                "❌ Formato de tiempo inválido. Use 'd' para días o 'm' para minutos."
            )
            return
            
        amount = int(time_str[:-1])
        unit = time_str[-1]
        
        if unit == 'd':
            expiration = datetime.now() + timedelta(days=amount)
        else:
            expiration = datetime.now() + timedelta(minutes=amount)
        
        # Obtener el ID del rol de revendedor
        role_result = execute_query("SELECT id FROM roles WHERE name = 'reseller'")
        if not role_result:
            await update.message.reply_text("❌ Error: No se encontró el rol de revendedor en la base de datos.")
            return
            
        reseller_role_id = role_result[0][0]
        
        # Verificar si el usuario existe
        user_exists = execute_query(
            "SELECT id FROM users WHERE id = %s AND bot_token = %s",
            (user_id, context.bot.token)
        )
        
        # Iniciar transacción para manejar múltiples inserciones
        try:
            # Primero crear o actualizar el usuario como revendedor
            if user_exists:
                execute_query("""
                UPDATE users 
                SET role_id = %s, access_until = %s, blocked_reason = NULL
                WHERE id = %s AND bot_token = %s
                """, (reseller_role_id, expiration, user_id, context.bot.token))
            else:
                execute_query("""
                INSERT INTO users (id, role_id, bot_token, access_until, created_by)
                VALUES (%s, %s, %s, %s, %s)
                """, (user_id, reseller_role_id, context.bot.token, expiration, update.effective_user.id))
            
            # Ahora agregar los emails autorizados
            for email in emails:
                execute_query("""
                INSERT INTO user_emails (user_id, bot_token, email)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, bot_token, email) DO NOTHING
                """, (user_id, context.bot.token, email.lower()))
            
            await update.message.reply_text(
                f"✅ Revendedor {user_id} añadido exitosamente\n"
                f"📧 Correos autorizados: {len(emails)}\n"
                f"⏱️ Expira: {expiration.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
        except Exception as e:
            bot_logger.log_error(f"Error en add_reseller_command: {str(e)}")
            await update.message.reply_text(f"❌ Error al añadir revendedor: {str(e)}")
            
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@admin_required
async def remove_reseller_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina un revendedor"""
    try:
        if len(context.args) != 1:
            await update.message.reply_text("❌ Uso: /removereseller <user_id>")
            return
            
        user_id = int(context.args[0])
        
        # Obtener el ID del rol de revendedor
        role_result = execute_query("SELECT id FROM roles WHERE name = 'reseller'")
        if not role_result:
            await update.message.reply_text("❌ Error: No se encontró el rol de revendedor en la base de datos.")
            return
            
        reseller_role_id = role_result[0][0]
        
        # Verificar si el usuario es un revendedor
        user_role = execute_query("""
        SELECT role_id FROM users 
        WHERE id = %s AND bot_token = %s
        """, (user_id, context.bot.token))
        
        if not user_role or user_role[0][0] != reseller_role_id:
            await update.message.reply_text(f"❌ El usuario {user_id} no es un revendedor.")
            return
            
        # Obtener el ID del rol de usuario normal
        user_role_result = execute_query("SELECT id FROM roles WHERE name = 'user'")
        if not user_role_result:
            await update.message.reply_text("❌ Error: No se encontró el rol de usuario en la base de datos.")
            return
            
        user_role_id = user_role_result[0][0]
        
        # Cambiar el rol de revendedor a usuario normal
        execute_query("""
        UPDATE users 
        SET role_id = %s
        WHERE id = %s AND bot_token = %s
        """, (user_role_id, user_id, context.bot.token))
        
        await update.message.reply_text(f"✅ Revendedor {user_id} degradado a usuario normal exitosamente")
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@admin_required
async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Desbloquea un usuario que fue bloqueado por seguridad
    Uso: /unblock <user_id> <tiempo>
    """
    try:
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Uso: /unblock <user_id> <tiempo>\n"
                "Ejemplo: /unblock 123456789 30d"
            )
            return
            
        user_id = int(context.args[0])
        time_str = context.args[1]
        bot_token = context.bot.token
        admin_id = update.effective_user.id
        
        # Validar formato de tiempo
        if not time_str[-1] in ['d', 'm']:
            await update.message.reply_text(
                "❌ Formato de tiempo inválido. Use 'd' para días o 'm' para minutos."
            )
            return
            
        try:
            amount = int(time_str[:-1])
            unit = time_str[-1]
        except ValueError:
            await update.message.reply_text("❌ El tiempo debe ser un número seguido de 'd' o 'm'")
            return
        
        # Calcular nueva fecha de expiración
        if unit == 'd':
            new_expiration = datetime.now() + timedelta(days=amount)
        else:  # unit == 'm'
            new_expiration = datetime.now() + timedelta(minutes=amount)
        
        # Verificar si el usuario existe
        user_result = execute_query("""
        SELECT access_until, blocked_reason FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        if not user_result:
            await update.message.reply_text(f"❌ El usuario {user_id} no existe en el sistema.")
            return
        
        current_expiration = user_result[0][0]
        blocked_reason = user_result[0][1] if len(user_result[0]) > 1 else None
        
        # Verificar si el usuario está realmente bloqueado
        if current_expiration and current_expiration > datetime.now():
            await update.message.reply_text(
                f"⚠️ El usuario {user_id} no está bloqueado.\n"
                f"📅 Su acceso expira: {current_expiration.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return
        
        # Desbloquear al usuario
        try:
            execute_query("""
            UPDATE users 
            SET access_until = %s, blocked_reason = NULL
            WHERE id = %s AND bot_token = %s
            """, (new_expiration, user_id, bot_token))
        except Exception as e:
            # Si falla con blocked_reason, intentar sin ella
            bot_logger.log_error(f"Error actualizando con blocked_reason: {e}")
            execute_query("""
            UPDATE users 
            SET access_until = %s
            WHERE id = %s AND bot_token = %s
            """, (new_expiration, user_id, bot_token))
        
        # Mensaje de confirmación para el admin
        admin_message = (
            f"✅ Usuario {user_id} desbloqueado exitosamente\n\n"
            f"📅 Nueva expiración: {new_expiration.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"👤 Desbloqueado por: {admin_id}\n"
            f"🕐 Fecha de desbloqueo: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        if blocked_reason:
            admin_message += f"\n📋 Razón del bloqueo anterior: {blocked_reason}"
        
        await update.message.reply_text(admin_message)
        
        # Notificar al usuario que fue desbloqueado
        try:
            user_message = (
                "🔓 CUENTA DESBLOQUEADA\n\n"
                "✅ Tu cuenta ha sido desbloqueada por un administrador\n\n"
                f"📅 Tu acceso expira: {new_expiration.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🕐 Desbloqueado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "🎉 Ya puedes usar el bot normalmente\n"
                "⚠️ Por favor, sigue las normas de uso para evitar futuros bloqueos"
            )
            
            if blocked_reason:
                user_message += f"\n\n📋 Motivo del bloqueo anterior: {blocked_reason}"
            
            await context.bot.send_message(
                chat_id=user_id,
                text=user_message
            )
            
            await update.message.reply_text("📱 Usuario notificado sobre el desbloqueo")
            
        except Exception as e:
            bot_logger.log_error(f"Error notificando al usuario {user_id} sobre desbloqueo: {e}")
            await update.message.reply_text(
                "⚠️ Usuario desbloqueado, pero no se pudo enviar la notificación.\n"
                "Es posible que el usuario no haya iniciado el bot."
            )
        
        # Log del desbloqueo
        bot_logger.logger.info(f"Usuario {user_id} desbloqueado por admin {admin_id}. Nueva expiración: {new_expiration}")
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        bot_logger.log_error(f"Error en comando unblock: {str(e)}")
        await update.message.reply_text(f"❌ Error al desbloquear usuario: {str(e)}")

@admin_required
async def msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Envía un mensaje a un usuario específico o a todos los usuarios
    Uso: /msg <user_id/allid> <mensaje>
    """
    try:
        # Verificar que haya al menos un destinatario y un mensaje
        if len(context.args) < 2:
            await update.message.reply_text("❌ Uso: /msg <user_id/allid> <mensaje>")
            return
            
        target = context.args[0].lower()
        message_text = " ".join(context.args[1:])
        bot_token = context.bot.token
        sender_name = update.effective_user.full_name or "Admin"
        sent_count = 0
        failed_count = 0
        
        # Crear un mensaje informativo para enviar
        admin_message = (
            f"📣 Mensaje oficial del bot:\n\n"
            f"{message_text}\n\n"
            f"📩 Enviado por: {sender_name}"
        )
        
        # Si es para todos los usuarios
        if target == "allid":
            # Obtener todos los usuarios válidos (no expirados)
            user_results = execute_query("""
            SELECT id FROM users
            WHERE bot_token = %s AND access_until > CURRENT_TIMESTAMP
            """, (bot_token,))
            
            if not user_results:
                await update.message.reply_text("❌ No se encontraron usuarios para enviar el mensaje.")
                return
                
            total_users = len(user_results)
            
            # Informar que el proceso ha iniciado
            status_message = await update.message.reply_text(
                f"📤 Enviando mensaje a {total_users} usuarios..."
            )
            
            # Enviar el mensaje a cada usuario
            for user_row in user_results:
                user_id = user_row[0]
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=admin_message
                    )
                    sent_count += 1
                except Exception as e:
                    bot_logger.log_error(f"Error enviando mensaje a usuario {user_id}: {str(e)}")
                    failed_count += 1
                    
                # Actualizar el status cada 10 usuarios
                if (sent_count + failed_count) % 10 == 0:
                    await status_message.edit_text(
                        f"📤 Enviando mensaje: {sent_count + failed_count}/{total_users} usuarios procesados..."
                    )
            
            # Actualizar mensaje final
            await status_message.edit_text(
                f"✅ Mensaje enviado a {sent_count} usuarios\n"
                f"❌ Fallidos: {failed_count}"
            )
        else:
            # Es para un usuario específico
            try:
                user_id = int(target)
                
                # Verificar si el usuario existe
                user_exists = execute_query("""
                SELECT id FROM users
                WHERE id = %s AND bot_token = %s
                """, (user_id, bot_token))
                
                if not user_exists:
                    await update.message.reply_text(f"❌ El usuario {user_id} no existe.")
                    return
                
                # Enviar el mensaje
                await context.bot.send_message(
                    chat_id=user_id,
                    text=admin_message
                )
                
                await update.message.reply_text(f"✅ Mensaje enviado correctamente al usuario {user_id}")
                
            except ValueError:
                await update.message.reply_text("❌ El ID de usuario debe ser un número o 'allid'")
    
    except Exception as e:
        bot_logger.log_error(f"Error en comando msg: {str(e)}")
        await update.message.reply_text(f"❌ Error al enviar mensaje: {str(e)}")

def super_admin_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Este comando está restringido solo al super administrador.")
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

__all__ = [
    'AdminManager',
    'admin_command',
    'add_reseller_command',
    'remove_reseller_command',
    'admin_required',
    'super_admin_required',
    'msg_command',
    'unblock_command'
]
