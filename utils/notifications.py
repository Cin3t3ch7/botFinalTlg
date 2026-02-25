from telegram.ext import ContextTypes
from datetime import datetime
from utils.logger_utility import bot_logger
from config import ADMIN_ID

class AdminNotifier:
    @staticmethod
    async def notify_admin_action(context: ContextTypes.DEFAULT_TYPE, reseller_id: int, action: str, details: str):
        """Notifica al admin principal sobre acciones importantes de revendedores"""
        message = (
            f"🔔 Acción de revendedor:\n"
            f"👤 ID: {reseller_id}\n"
            f"📝 Acción: {action}\n"
            f"📋 Detalles: {details}\n"
            f"⏰ Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=message
            )
        except Exception as e:
            bot_logger.log_error(f"Error notificando al admin: {str(e)}")
    
    @staticmethod
    async def notify_quota_exceeded(context: ContextTypes.DEFAULT_TYPE, reseller_id: int, quota_type: str):
        """Notifica cuando un revendedor excede sus cuotas"""
        message = (
            f"⚠️ Alerta de cuota excedida:\n"
            f"👤 Revendedor ID: {reseller_id}\n"
            f"📊 Tipo de cuota: {quota_type}\n"
            f"⏰ Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=message
            )
        except Exception as e:
            bot_logger.log_error(f"Error notificando cuota excedida: {str(e)}")

async def notify_quota_warning(self, reseller_id, quota_type, current_usage):
    """Notificar cuando un revendedor está cerca de su límite"""
    threshold = self.get_warning_threshold(quota_type)
    if current_usage >= threshold:
        await self.send_warning_notification(reseller_id, quota_type)
