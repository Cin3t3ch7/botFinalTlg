from telegram import Update
import os
from telegram.ext import ContextTypes
from datetime import datetime
from handlers.admin_handlers import admin_required
from utils.permission_manager import PermissionManager

@admin_required
async def check_user_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para verificar y diagnosticar el tiempo de un usuario"""
    try:
        if not context.args:
            await update.message.reply_text("❌ Uso: /checktime <user_id>")
            return
            
        user_id = int(context.args[0])
        permission_manager = PermissionManager()
        
        # Usar la función check_and_log_time_issues para diagnóstico
        is_valid = permission_manager.check_and_log_time_issues(user_id)
        
        # Obtener información detallada
        info = permission_manager.get_user_expiration_info(user_id)
        
        if info:
            message = (
                f"📊 *Diagnóstico de tiempo para usuario {user_id}*\n\n"
                f"⏰ Estado actual: {'Activo ✅' if is_valid else 'Expirado ❌'}\n"
                f"📅 Fecha de registro: {info['created_at'].strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"⌛️ Fecha de expiración: {info['expiration'].strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"⏳ Tiempo restante: {info['days_remaining']}d {info['hours_remaining']}h\n"
                f"💎 Créditos: {info['credits']}\n\n"
                f"🔄 Hora actual: {info['current_time'].strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
        else:
            message = f"❌ No se encontró información para el usuario {user_id}"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except ValueError:
        await update.message.reply_text("❌ El ID de usuario debe ser un número")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

@admin_required
async def check_time_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando para verificar el tiempo de todos los usuarios con problemas
    Uso: /checktimeall
    """
    try:
        permission_manager = PermissionManager()
        from database.connection import execute_query
        
        # Consultar todos los usuarios directamente de la base de datos
        # Obtenemos usuarios que ya expiraron O que expiran en los próximos 2 días
        query = """
        SELECT id, access_until 
        FROM users 
        WHERE bot_token = %s 
        AND (access_until < CURRENT_TIMESTAMP OR access_until < CURRENT_TIMESTAMP + INTERVAL '2 days')
        ORDER BY access_until ASC
        """
        
        bot_token = context.bot.token
        raw_users = execute_query(query, (bot_token,))
        
        users_with_issues = []
        users_ok_count = 0
        
        # Contar total usuarios activos para estadísticas
        count_query = """
        SELECT COUNT(*) FROM users 
        WHERE bot_token = %s AND access_until > CURRENT_TIMESTAMP + INTERVAL '2 days'
        """
        ok_result = execute_query(count_query, (bot_token,))
        if ok_result:
            users_ok_count = ok_result[0][0]
        
        if raw_users:
            current_time = datetime.now()
            
            for user_row in raw_users:
                user_id = user_row[0]
                expiration = user_row[1]
                
                # Calcular tiempo restante
                if expiration > current_time:
                    remaining = expiration - current_time
                    is_expired = False
                    days = remaining.days
                    hours = remaining.seconds // 3600
                else:
                    remaining = current_time - expiration
                    is_expired = True
                    days = remaining.days
                    hours = remaining.seconds // 3600
                
                user_status = {
                    'id': user_id,
                    'is_expired': is_expired,
                    'days': days,
                    'hours': hours,
                    'expiration': expiration
                }
                
                users_with_issues.append(user_status)
        
        # Preparar mensaje
        message = "📊 *Estado de tiempo de usuarios*\n\n"
        
        if users_with_issues:
            message += "⚠️ *Usuarios con problemas o próximos a expirar:*\n\n"
            for user in users_with_issues:
                status = "❌ Expirado hace" if user['is_expired'] else "⚠️ Expira en"
                message += (
                    f"👤 ID: `{user['id']}`\n"
                    f"📌 Estado: {status} {user['days']}d {user['hours']}h\n"
                    f"📅 Fecha: {user['expiration'].strftime('%Y-%m-%d %H:%M')}\n"
                    f"──────────────────\n"
                )
        else:
            message += "✅ No hay usuarios expirados ni próximos a expirar.\n\n"
        
        message += f"✅ Usuarios activos sin problemas reportados: {users_ok_count}"
        
        # Dividir mensaje si es muy largo
        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")
