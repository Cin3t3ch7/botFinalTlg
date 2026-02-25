from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from datetime import datetime
from config import ADMIN_ID
from database.connection import execute_query
from utils.logger_utility import bot_logger

def check_user_permission(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        # Main admin siempre tiene acceso
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)
        
        try:
            # Primero verificar si es admin
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            
            if is_admin:
                return await func(update, context, *args, **kwargs)
            
            # Verificar si es un usuario normal con acceso válido
            user_result = execute_query("""
            SELECT access_until, blocked_reason FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not user_result:
                await update.message.reply_text(
                    "❌ No tienes acceso al bot. Por favor, contacta al administrador para obtener acceso."
                )
                return
                
            expiration = user_result[0][0]
            blocked_reason = user_result[0][1] if len(user_result[0]) > 1 else None
            
            # Verificar si la suscripción ha expirado o está bloqueado
            if datetime.now() > expiration:
                # Si hay una razón de bloqueo, es un bloqueo por seguridad
                if blocked_reason:
                    blocked_message = (
                        "🚨 CUENTA BLOQUEADA POR SEGURIDAD\n\n"
                        "⚠️ Tu cuenta ha sido bloqueada automáticamente\n\n"
                        f"📋 Motivo: {blocked_reason}\n"
                        f"🕐 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        "🔒 Esta es una medida de seguridad automática\n"
                        "📞 Contacta al administrador para resolver este problema\n\n"
                        "⚡ Tu acceso será restaurado una vez verificada tu identidad"
                    )
                    await update.message.reply_text(blocked_message)
                else:
                    # Es una expiración normal
                    await update.message.reply_text(
                        "⚠️ Tu suscripción ha expirado. Por favor, contacta al administrador para renovar tu acceso."
                    )
                return
                
            # Si todo está bien, ejecutar la función
            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            bot_logger.log_error(f"Error verificando permisos para {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error al verificar permisos. Por favor, contacta al administrador."
            )
            return
            
    return wrapper

def check_callback_permission(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.callback_query.from_user.id
        bot_token = context.bot.token
        
        # Main admin siempre tiene acceso
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)

        try:
            # Primero verificar si es admin
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            
            if is_admin:
                return await func(update, context, *args, **kwargs)
            
            # Verificar si es un usuario normal con acceso válido
            user_result = execute_query("""
            SELECT access_until, blocked_reason FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not user_result:
                await update.callback_query.answer(
                    "No tienes acceso al bot. Contacta al administrador.",
                    show_alert=True
                )
                return
                
            expiration = user_result[0][0]
            blocked_reason = user_result[0][1] if len(user_result[0]) > 1 else None
            
            # Verificar si la suscripción ha expirado o está bloqueado
            if datetime.now() > expiration:
                if blocked_reason:
                    await update.callback_query.answer(
                        f"🚨 Cuenta bloqueada por seguridad: {blocked_reason}. Contacta al administrador.",
                        show_alert=True
                    )
                else:
                    await update.callback_query.answer(
                        "Tu suscripción ha expirado. Contacta al administrador para renovar.",
                        show_alert=True
                    )
                return
                
            # Si todo está bien, ejecutar la función
            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            bot_logger.log_error(f"Error verificando permisos para callback {user_id}: {str(e)}")
            await update.callback_query.answer(
                "Error al verificar permisos. Contacta al administrador.",
                show_alert=True
            )
            return
            
    return wrapper

def admin_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        # Verificar si es admin o superadmin
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)
            
        try:
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if not admin_result or admin_result[0][0] not in ['admin', 'super_admin']:
                await update.message.reply_text(
                    "❌ Este comando está restringido solo para administradores."
                )
                return
                
            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            bot_logger.log_error(f"Error verificando admin para {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error al verificar permisos. Por favor, contacta al administrador."
            )
            return
        
    return wrapper

def admin_or_reseller_required(func):
    """
    Decorador que permite que tanto administradores como revendedores usen un comando.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        # Si es el super admin, permitir sin restricciones
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)
            
        try:
            # Verificar si es admin o revendedor
            role_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if not role_result or role_result[0][0] not in ['admin', 'super_admin', 'reseller']:
                await update.message.reply_text(
                    "❌ Este comando está restringido para administradores y revendedores."
                )
                return
                
            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            bot_logger.log_error(f"Error verificando permisos para {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error al verificar permisos. Por favor, contacta al administrador."
            )
            return
        
    return wrapper

def reseller_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        # El super admin siempre puede ejecutar
        if user_id == ADMIN_ID:
            return await func(update, context, *args, **kwargs)
        
        try:
            # Verificar rol del usuario
            role_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if not role_result:
                await update.message.reply_text(
                    "❌ No tienes acceso al bot. Por favor, contacta al administrador."
                )
                return
                
            role_name = role_result[0][0]
            
            # Admins y revendedores pueden usar estos comandos
            if role_name in ['admin', 'super_admin', 'reseller']:
                return await func(update, context, *args, **kwargs)
                
            await update.message.reply_text(
                "❌ No tienes permisos de revendedor para usar este comando."
            )
            return
            
        except Exception as e:
            bot_logger.log_error(f"Error verificando permisos para {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error al verificar permisos. Por favor, contacta al administrador."
            )
            return
    return wrapper

def reseller_can_manage_user(func):
    """
    Decorador que verifica si un revendedor puede gestionar a un usuario específico.
    Sólo puede gestionar usuarios que él mismo creó.
    Los administradores pueden gestionar cualquier usuario.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Verificar si es el comando adduser (caso especial)
        command = update.message.text.split()[0] if update.message and update.message.text else ""
        is_adduser_command = "/adduser" in command
        
        # Si no hay argumentos, mostrar ayuda según el comando
        if not context.args:
            command_name = command.split("/")[-1] if command.startswith("/") else "comando"
            
            # Mensajes de ayuda según el comando
            help_messages = {
                "adduser": "❌ Uso: /adduser <user_id> <tiempo> [email1 email2 ...]",
                "removeuser": "❌ Uso: /removeuser <user_id>",
                "eliminar": "❌ Uso: /eliminar <user_id> <email1> [email2 ...]",
                "garantia": "❌ Uso: /garantia <user_id> <old_email> <new_email>",
                "addtime": "❌ Uso: /addtime <user_id/allid> <tiempo>",
                "addemail": "❌ Uso: /addemail <user_id> <email1> [email2 ...]"
            }
            
            message = help_messages.get(command_name, f"❌ Uso: /{command_name} <argumentos>")
            await update.message.reply_text(message)
            return

        try:
            user_id = int(context.args[0])  # ID del usuario a gestionar
            caller_id = update.effective_user.id  # ID del revendedor/admin
            bot_token = context.bot.token

            # Si es el super admin, permitir sin restricciones
            if caller_id == ADMIN_ID:
                return await func(update, context, *args, **kwargs)
                
            # Verificar si es admin
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (caller_id, bot_token))
            
            is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            if is_admin:
                return await func(update, context, *args, **kwargs)
            
            # Si no es admin, verificar si es revendedor
            reseller_check = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s AND r.name = 'reseller'
            """, (caller_id, bot_token))
            
            if not reseller_check:
                await update.message.reply_text("❌ No tienes permisos para ejecutar este comando.")
                return
                
            # Para el comando adduser, permitir sin verificar si el usuario existe
            if is_adduser_command:
                return await func(update, context, *args, **kwargs)
                
            # Para otros comandos, verificar si el revendedor creó al usuario
            creator_check = execute_query("""
            SELECT id FROM users
            WHERE id = %s AND bot_token = %s AND created_by = %s
            """, (user_id, bot_token, caller_id))
            
            if not creator_check:
                await update.message.reply_text(
                    "❌ No tienes permiso para gestionar este usuario.\n"
                    "👤 Solo puedes gestionar usuarios que tú hayas creado."
                )
                return
                
            # Si pasó todas las verificaciones, permitir ejecutar el comando
            return await func(update, context, *args, **kwargs)
            
        except ValueError:
            await update.message.reply_text("❌ El ID de usuario debe ser un número")
        except Exception as e:
            bot_logger.log_error(f"Error verificando permisos: {str(e)}")
            await update.message.reply_text("❌ Error al verificar permisos.")
    
    return wrapper

__all__ = [
    'check_user_permission',
    'check_callback_permission',
    'admin_required',
    'admin_or_reseller_required',
    'reseller_required',
    'reseller_can_manage_user'
]
