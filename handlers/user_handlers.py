from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import json
from datetime import datetime
from handlers.admin_handlers import AdminManager
from handlers.extended_handlers import UserManager
from utils.permission_manager import PermissionManager
from utils.logger_utility import bot_logger
from config import ADMIN_ID
from database.connection import execute_query

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    username = update.effective_user.username
    bot_token = context.bot.token
    
    # Si es el super admin, permitir acceso directamente
    if user_id == ADMIN_ID:
        return await show_menu(update, context)
    
    try:
        # Verificar si es un usuario existente
        user_result = execute_query("""
        SELECT u.access_until, r.name as role_name
        FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE u.id = %s AND u.bot_token = %s
        """, (user_id, bot_token))
        
        # Si es un usuario nuevo
        if not user_result:
            # Construir mensaje de notificación para admins
            notification = (
                "🆕 *Nuevo usuario detectado*\n\n"
                f"🆔 ID: `{user_id}`\n"
                f"👤 Nombre: {user_name}\n"
                f"📝 Username: {'@' + username if username else 'No establecido'}\n"
                f"📅 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            try:
                # Enviar notificación al super admin
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=notification,
                    parse_mode='Markdown'
                )
                
                # Enviar notificación a otros admins activos
                admin_results = execute_query("""
                SELECT u.id FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE r.name = 'admin' AND u.access_until > CURRENT_TIMESTAMP AND u.bot_token = %s
                """, (bot_token,))
                
                if admin_results:
                    for admin_row in admin_results:
                        admin_id = admin_row[0]
                        if admin_id != ADMIN_ID:  # No duplicar al super admin
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=notification,
                                parse_mode='Markdown'
                            )
            except Exception as e:
                bot_logger.log_error(f"Error enviando notificación de nuevo usuario: {str(e)}")
            
            await update.message.reply_text(
                "❌ No tienes acceso al bot.\n"
                "📝 Contacta al administrador para solicitar acceso."
            )
            bot_logger.logger.warning(f"Unauthorized access attempt from user {user_id}")
            return
        
        # Usuario existente, verificar expiración
        expiration = user_result[0][0]
        role_name = user_result[0][1]
        
        if datetime.now() > expiration:
            await update.message.reply_text(
                "⚠️ Tu suscripción ha expirado.\n" 
                "📝 Contacta al administrador para renovar tu acceso."
            )
            bot_logger.logger.warning(f"Expired subscription access attempt from user {user_id} with role {role_name}")
            return

        # Calcular y almacenar el tiempo restante para el usuario en context
        time_remaining = expiration - datetime.now()
        days_remaining = time_remaining.days
        hours_remaining = time_remaining.seconds // 3600
        
        # Guardar en el contexto para usarlo en show_menu
        context.user_data['days_remaining'] = days_remaining
        context.user_data['hours_remaining'] = hours_remaining

        await show_menu(update, context)
        bot_logger.logger.info(f"User {user_id} with role {role_name} started bot successfully")

    except Exception as e:
        await update.message.reply_text(
            "❌ Error al verificar permisos.\n"
            "📝 Contacta al administrador."
        )
        bot_logger.logger.error(f"Error verifying permissions for user {user_id}: {str(e)}")
        return
       
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.username or "Usuario"
    bot_token = context.bot.token
    
    # Obtener el rol del usuario
    role_result = execute_query("""
    SELECT r.name FROM users u
    JOIN roles r ON u.role_id = r.id
    WHERE u.id = %s AND u.bot_token = %s
    """, (user_id, bot_token))
    
    role_name = "Usuario"
    if user_id == ADMIN_ID:
        role_name = "Super Admin"
    elif role_result:
        role_mapping = {
            'super_admin': 'Super Admin',
            'admin': 'Administrador',
            'reseller': 'Revendedor',
            'user': 'Usuario'
        }
        role_name = role_mapping.get(role_result[0][0], "Usuario")
    
    # Obtener información de tiempo restante si no está en el super admin
    time_info = ""
    if user_id != ADMIN_ID:
        # Si los datos de tiempo están en el contexto, usarlos
        if 'days_remaining' in context.user_data and 'hours_remaining' in context.user_data:
            days = context.user_data['days_remaining']
            hours = context.user_data['hours_remaining']
        else:
            # Si no están en el contexto, consultarlos de la base de datos
            user_info = execute_query("""
            SELECT access_until FROM users
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if user_info and user_info[0][0]:
                expiration = user_info[0][0]
                time_remaining = expiration - datetime.now()
                days = time_remaining.days
                hours = time_remaining.seconds // 3600
            else:
                days = 0
                hours = 0
        
        time_info = f"⏳ Tiempo restante: {days}d {hours}h\n"
    
    # Determinar si el usuario fue creado por un revendedor
    is_reseller_user = False
    try:
        creator_result = execute_query("""
        SELECT u2.id FROM users u1
        JOIN users u2 ON u1.created_by = u2.id
        JOIN roles r ON u2.role_id = r.id
        WHERE u1.id = %s AND u1.bot_token = %s AND r.name = 'reseller'
        """, (user_id, bot_token))
        
        is_reseller_user = bool(creator_result)
    except Exception:
        pass
    
    # Crear botones en formato 2x2 + 1 + 1
    keyboard = [
        [
            InlineKeyboardButton("Netflix", callback_data='netflix_menu'),
            InlineKeyboardButton("Disney", callback_data='disney_menu')
        ],
        [
            InlineKeyboardButton("Max", callback_data='max_menu'),
            InlineKeyboardButton("Prime", callback_data='prime_menu')
        ],
        [InlineKeyboardButton("Crunchyroll", callback_data='crunchyroll_menu')],
        [InlineKeyboardButton("⚙️ Configuración", callback_data='info_user')]
    ]
    
    # Si es usuario de revendedor, remover Prime Video
    if is_reseller_user:
        keyboard[1] = [InlineKeyboardButton("Max", callback_data='max_menu')]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Crear mensaje de bienvenida personalizado con tiempo restante
    welcome_message = (
        f"🎉 ¡Bienvenido al Bot!\n\n"
        f"👤 Usuario: @{user_name}\n"
        f"🎭 Rol: {role_name}\n"
        f"{time_info}\n"  # Añadido el tiempo restante
        f"💫 Selecciona una opción del menú:"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_message,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            welcome_message,
            reply_markup=reply_markup
        )

async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_token = context.bot.token
    
    if query.data == 'disney_menu':
        # Verificar si el usuario fue creado por un revendedor
        user_id = update.effective_user.id
        
        is_reseller_user = False
        try:
            creator_result = execute_query("""
            SELECT u2.id FROM users u1
            JOIN users u2 ON u1.created_by = u2.id
            JOIN roles r ON u2.role_id = r.id
            WHERE u1.id = %s AND u1.bot_token = %s AND r.name = 'reseller'
            """, (user_id, bot_token))
            
            is_reseller_user = bool(creator_result)
        except Exception:
            pass
        
        # Reseller users now have access to all Disney buttons
        keyboard = [
            [InlineKeyboardButton("Buscar Código Disney", callback_data='disney_code')],
            [InlineKeyboardButton("Actualizar Hogar", callback_data='disney_home')],
            [InlineKeyboardButton("My Disney", callback_data='disney_mydisney')],
            [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
        ]
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecciona una opción de Disney:", reply_markup=reply_markup)
   
    elif query.data == 'netflix_menu':
        # Verificar si el usuario fue creado por un revendedor
        user_id = update.effective_user.id
        
        is_reseller_user = False
        try:
            creator_result = execute_query("""
            SELECT u2.id FROM users u1
            JOIN users u2 ON u1.created_by = u2.id
            JOIN roles r ON u2.role_id = r.id
            WHERE u1.id = %s AND u1.bot_token = %s AND r.name = 'reseller'
            """, (user_id, bot_token))
            
            is_reseller_user = bool(creator_result)
        except Exception:
            pass
        
        # Verificar si el usuario tiene permiso de código de login
        code_access_result = execute_query("""
        SELECT code_access FROM users
        WHERE id = %s AND bot_token = %s
        """, (user_id, bot_token))
        
        has_login_code_permission = code_access_result and code_access_result[0][0]
        
        # En el archivo user_handlers.py, función handle_menu_selection, caso 'netflix_menu'
        if is_reseller_user:
            # Menú para usuarios de revendedores
            keyboard = [
                [InlineKeyboardButton("Enlace Reset Password", callback_data='netflix_reset_link')],
                [InlineKeyboardButton("Actualizar Hogar", callback_data='netflix_update_home')],
                [InlineKeyboardButton("Código de Hogar", callback_data='netflix_home_code')],
                [InlineKeyboardButton("Aprovación de inicio", callback_data='netflix_activation')],
                [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
            ]
            
            # Añadir opción de código de login si tiene permiso
            if has_login_code_permission or user_id == ADMIN_ID:
                keyboard.insert(3, [InlineKeyboardButton("Código de Login", callback_data='netflix_login_code')])
        else:
            # Base de menú para usuarios normales
            keyboard = [
                [InlineKeyboardButton("Enlace Reset Password", callback_data='netflix_reset_link')],
                [InlineKeyboardButton("Actualizar Hogar", callback_data='netflix_update_home')],
                [InlineKeyboardButton("Código de Hogar", callback_data='netflix_home_code')],
                [InlineKeyboardButton("País de la Cuenta", callback_data='netflix_country')],
                [InlineKeyboardButton("Aprovación de inicio", callback_data='netflix_activation')],
                [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
            ]
            
            # Añadir opción de código de login si tiene permiso
            if has_login_code_permission or user_id == ADMIN_ID:
                keyboard.insert(4, [InlineKeyboardButton("Código de Login", callback_data='netflix_login_code')])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecciona una opción de Netflix:", reply_markup=reply_markup)

    elif query.data == 'crunchyroll_menu':
        keyboard = [
            [InlineKeyboardButton("Reset Password", callback_data='crunchyroll_reset')],
            [InlineKeyboardButton("Verificación de dispositivo", callback_data='crunchyroll_device')],
            [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecciona una opción de Crunchyroll:", reply_markup=reply_markup)

    elif query.data == 'prime_menu':
        keyboard = [
            [InlineKeyboardButton("Código OTP", callback_data='prime_otp')],
            [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecciona una opción de Prime Video:", reply_markup=reply_markup)

    elif query.data == 'max_menu':
        keyboard = [
            [InlineKeyboardButton("Reset Password", callback_data='max_reset')],
            [InlineKeyboardButton("Código Max", callback_data='max_code')],
            [InlineKeyboardButton("Volver al Menú Principal", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecciona una opción de Max:", reply_markup=reply_markup)
   
    elif query.data == 'info_user':
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        try:
            # Verificar si es admin o super admin
            is_admin = False
            if user_id == ADMIN_ID:
                is_admin = True
            else:
                admin_result = execute_query("""
                SELECT r.name FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.id = %s AND u.bot_token = %s
                """, (user_id, bot_token))
                
                is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            
            # Crear botones según el rol del usuario
            keyboard = [
                [InlineKeyboardButton("ℹ️ Mi Información", callback_data='view_my_info')]
            ]
            
            # Añadir botones específicos para administradores
            if is_admin:
                keyboard.append([InlineKeyboardButton("📧 Configuración IMAP", callback_data='config_imap')])
            
            keyboard.append([InlineKeyboardButton("↩️ Volver al Menú Principal", callback_data='main_menu')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "⚙️ Panel de Configuración\n\nSelecciona una opción:",
                reply_markup=reply_markup
            )
                    
        except Exception as e:
            bot_logger.log_error(f"Error obteniendo información del usuario {user_id}: {str(e)}")
            await query.edit_message_text(
                f"❌ Error al obtener información: {str(e)}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Volver al Menú Principal", callback_data='main_menu')
                ]])
            )
    
    elif query.data == 'view_my_info':
        # Este caso muestra la información detallada del usuario
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        try:
            # Obtener información completa del usuario
            user_result = execute_query("""
            SELECT u.access_until, u.created_at, r.name as role_name, u.free_access
            FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if not user_result:
                await query.edit_message_text(
                    "❌ No se encontró información de usuario.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user'),
                        InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')
                    ]])
                )
                return
                
            expiration_date, creation_date, role_name, free_access = user_result[0]
            
            # Obtener correos asignados
            email_result = execute_query("""
            SELECT email FROM user_emails
            WHERE user_id = %s AND bot_token = %s
            ORDER BY email
            """, (user_id, bot_token))
            
            emails = [email[0] for email in email_result] if email_result else []
            
            # Construir mensaje de información sin usar markdown
            info_message = "📱 Información del Usuario\n\n"
            
            # Información básica
            info_message += f"🆔 ID: {user_id}\n"
            info_message += f"👤 Nombre: {update.effective_user.full_name}\n"
            info_message += f"👤 Username: @{update.effective_user.username if update.effective_user.username else 'No establecido'}\n"
            
            # Estado y permisos
            info_message += f"🛡️ Rol: {role_name}\n"
            
            # Si es revendedor, añadir información específica
            if role_name == 'reseller':
                # Obtener usuarios creados por este revendedor
                users_created = execute_query("""
                SELECT COUNT(*) FROM users
                WHERE created_by = %s AND bot_token = %s
                """, (user_id, bot_token))
                
                users_count = users_created[0][0] if users_created else 0
                
                info_message += "\n📊 Información de Revendedor:\n"
                info_message += f"👥 Usuarios creados: {users_count}\n"
            
            # Fechas y estado
            if creation_date:
                info_message += f"\n📅 Fecha de registro: {creation_date.strftime('%Y-%m-%d %H:%M')}\n"
            
            # Calcular tiempo restante
            if expiration_date:
                time_remaining = expiration_date - datetime.now()
                days_left = time_remaining.days
                hours_left = int((time_remaining.seconds / 3600))
                
                if days_left >= 0 or hours_left >= 0:
                    info_message += f"⏳ Tiempo restante: {days_left}d {hours_left}h\n"
                    info_message += f"📆 Expira: {expiration_date.strftime('%Y-%m-%d %H:%M')}\n"
                else:
                    info_message += "⚠️ Suscripción expirada\n"
            else:
                info_message += "⚠️ No se encontró fecha de expiración\n"
            
            # Acceso y correos
            info_message += f"\n🔓 Acceso libre: {'✅ Sí' if free_access else '❌ No'}\n"
            info_message += f"📧 Correos registrados: {len(emails)}\n"
            
            # Lista de correos si hay menos de 10
            if 0 < len(emails) <= 10:
                info_message += "\n📬 Lista de correos:\n"
                for email in emails:
                    info_message += f"• {email}\n"
            elif len(emails) > 10:
                info_message += f"\n📬 Correos totales: {len(emails)} (demasiados para mostrar)\n"
            
            # Botones para volver
            keyboard = [
                [InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user')],
                [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                info_message,
                reply_markup=reply_markup
            )
                
        except Exception as e:
            bot_logger.log_error(f"Error obteniendo información del usuario {user_id}: {str(e)}")
            await query.edit_message_text(
                f"❌ Error al obtener información: {str(e)}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user'),
                    InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')
                ]])
            )
    
    elif query.data == 'config_imap':
        # Verificar si es admin o super admin
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        admin_result = execute_query("""
        SELECT r.name FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE u.id = %s AND u.bot_token = %s
        """, (user_id, bot_token))
        
        is_admin = (admin_result and admin_result[0][0] in ['admin', 'super_admin']) or user_id == ADMIN_ID
        
        if not is_admin:
            await query.answer("❌ No tienes permisos para esta acción", show_alert=True)
            return
        
        # Obtener configuraciones IMAP actuales
        imap_configs = execute_query("""
        SELECT id, domain, email, imap_server FROM imap_config
        WHERE bot_token = %s
        ORDER BY domain
        """, (bot_token,))
        
        # Crear mensaje con las instrucciones
        message = "📧 Configuraciones IMAP\n\n"
        message += "Para añadir una nueva configuración IMAP usa el comando:\n"
        message += "/addimap dominio email contraseña servidor\n\n"
        message += "Ejemplo:\n"
        message += "/addimap gmail.com usuario@gmail.com mipassword imap.gmail.com"
        
        # Crear teclado con botones para cada dominio
        keyboard = []
        
        if imap_configs:
            # Añadir un botón por cada dominio configurado
            for config_id, domain, email, server in imap_configs:
                # Usar el ID de configuración para crear un callback_data único
                keyboard.append([InlineKeyboardButton(f"🔗 {domain}", callback_data=f"imap_details_{config_id}")])
        
        # Botones de navegación
        keyboard.append([InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user')])
        keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data.startswith('imap_details_'):
        # Extraer el ID de la configuración IMAP
        config_id = int(query.data.split('_')[2])
        user_id = query.from_user.id
        bot_token = context.bot.token
        
        # Verificar permisos de administrador
        admin_result = execute_query("""
        SELECT r.name FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE u.id = %s AND u.bot_token = %s
        """, (user_id, bot_token))
        
        is_admin = (admin_result and admin_result[0][0] in ['admin', 'super_admin']) or user_id == ADMIN_ID
        
        if not is_admin:
            await query.answer("❌ No tienes permisos para ver estos detalles", show_alert=True)
            return
        
        # Obtener detalles de la configuración IMAP
        config_details = execute_query("""
        SELECT domain, email, imap_server FROM imap_config
        WHERE id = %s AND bot_token = %s
        """, (config_id, bot_token))
        
        if not config_details:
            await query.answer("❌ Configuración no encontrada", show_alert=True)
            return
        
        domain, email, server = config_details[0]
        
        # Crear mensaje con los detalles
        message = f"📧 Detalles IMAP: {domain}\n\n"
        message += f"🔹 Dominio: {domain}\n"
        message += f"🔹 Email: {email}\n"
        message += f"🔹 Servidor: {server}\n\n"
        message += "La contraseña no se muestra por seguridad."
        
        # Botones de navegación - AÑADIDO BOTÓN DE ELIMINAR
        keyboard = [
            [InlineKeyboardButton("🗑️ Eliminar configuración", callback_data=f"imap_delete_{config_id}")],
            [InlineKeyboardButton("↩️ Volver a Configuraciones IMAP", callback_data='config_imap')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data.startswith('imap_delete_'):
        # Extraer el ID de la configuración IMAP
        config_id = int(query.data.split('_')[2])
        user_id = query.from_user.id
        bot_token = context.bot.token
        
        # Verificar permisos de administrador
        admin_result = execute_query("""
        SELECT r.name FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE u.id = %s AND u.bot_token = %s
        """, (user_id, bot_token))
        
        is_admin = (admin_result and admin_result[0][0] in ['admin', 'super_admin']) or user_id == ADMIN_ID
        
        if not is_admin:
            await query.answer("❌ No tienes permisos para eliminar configuraciones", show_alert=True)
            return
        
        # Obtener información de la configuración antes de eliminarla
        config_info = execute_query("""
        SELECT domain FROM imap_config
        WHERE id = %s AND bot_token = %s
        """, (config_id, bot_token))
        
        if not config_info:
            await query.answer("❌ Configuración no encontrada", show_alert=True)
            return
        
        domain = config_info[0][0]
        
        # Eliminar la configuración
        execute_query("""
        DELETE FROM imap_config
        WHERE id = %s AND bot_token = %s
        """, (config_id, bot_token))
        
        # Mostrar mensaje de confirmación
        message = f"✅ Configuración IMAP para dominio {domain} eliminada correctamente"
        
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Configuraciones IMAP", callback_data='config_imap')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == 'add_admin':
        # Verificar si es admin o super admin
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        if user_id != ADMIN_ID:
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            
            if not is_admin:
                await query.answer("❌ No tienes permisos para esta acción", show_alert=True)
                return
        
        message = "👤 *Añadir Administrador*\n\n"
        message += "Para agregar un nuevo administrador usa el comando:\n"
        message += "`/admin <user_id> <tiempo>`\n\n"
        message += "Donde:\n"
        message += "- `user_id` es el ID de Telegram del usuario\n"
        message += "- `tiempo` es la duración del acceso (ej: 30d para 30 días)\n\n"
        message += "Ejemplo:\n"
        message += "`/admin 123456789 30d`"
        
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif query.data == 'add_reseller':
        # Verificar si es admin o super admin
        user_id = update.effective_user.id
        bot_token = context.bot.token
        
        if user_id != ADMIN_ID:
            admin_result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            is_admin = admin_result and admin_result[0][0] in ['admin', 'super_admin']
            
            if not is_admin:
                await query.answer("❌ No tienes permisos para esta acción", show_alert=True)
                return
        
        message = "🔄 *Añadir Revendedor*\n\n"
        message += "Para agregar un nuevo revendedor usa el comando:\n"
        message += "`/addreseller <user_id> <tiempo> <email1> [email2 ...]`\n\n"
        message += "Donde:\n"
        message += "- `user_id` es el ID de Telegram del usuario\n"
        message += "- `tiempo` es la duración del acceso (ej: 30d para 30 días)\n"
        message += "- `email1, email2, ...` son los correos autorizados para el revendedor\n\n"
        message += "Ejemplo:\n"
        message += "`/addreseller 123456789 30d correo1@gmail.com correo2@hotmail.com`"
        
        keyboard = [
            [InlineKeyboardButton("↩️ Volver a Configuración", callback_data='info_user')],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data='main_menu')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif query.data == 'back_to_menu':
        # Clear the search state
        if 'search_state' in context.user_data:
            del context.user_data['search_state']
        await handle_menu_selection(update, context)
    
    elif query.data == 'main_menu':
        await show_menu(update, context)
