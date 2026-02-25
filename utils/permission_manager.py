import logging
from datetime import datetime, timezone, timedelta
from config import ADMIN_ID
from utils.logger_utility import bot_logger
from database.connection import execute_query

class PermissionManager:
    def __init__(self):
        pass
        
    def is_authorized(self, user_id, bot_token):
        """Check if a user is authorized with proper timezone handling"""
        # Main admin always has access
        if user_id == ADMIN_ID:
            return True
            
        try:
            # Consultar en la base de datos
            result = execute_query("""
            SELECT access_until FROM users 
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not result or not result[0][0]:
                bot_logger.logger.warning(f"Authorization check failed: User not found for {user_id}")
                return False
                
            expiration = result[0][0]
            current_time = datetime.now()
            
            is_valid = current_time < expiration
            
            # Log detallado del estado de autorización
            bot_logger.logger.info(
                f"Authorization check for user {user_id}:\n"
                f"Current time: {current_time}\n"
                f"Expiration: {expiration}\n"
                f"Status: {'Authorized' if is_valid else 'Expired'}"
            )
            
            return is_valid
            
        except Exception as e:
            bot_logger.log_error(f"Error checking authorization for user {user_id}: {str(e)}")
            return False
        
    def is_admin(self, user_id, bot_token):
        """Check if a user has admin privileges"""
        # Main admin always has admin privileges
        if user_id == ADMIN_ID:
            return True
            
        try:
            # Consultar en la base de datos
            result = execute_query("""
            SELECT r.name FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.id = %s AND u.bot_token = %s
            """, (user_id, bot_token))
            
            if not result:
                return False
                
            role_name = result[0][0]
            return role_name == 'admin' or role_name == 'super_admin'
            
        except Exception as e:
            bot_logger.log_error(f"Error checking admin status for user {user_id}: {str(e)}")
            return False
        
    def get_user_credits(self, user_id, bot_token):
        """Placeholder for credits functionality - would be implemented in DB"""
        # Main admin has unlimited credits
        if user_id == ADMIN_ID:
            return float('inf')
        
        return 0  # Para implementación futura
        
    def check_and_log_time_issues(self, user_id, bot_token):
        """Detailed time check and logging for debugging"""
        try:
            result = execute_query("""
            SELECT access_until FROM users 
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not result:
                bot_logger.logger.warning(f"Time check failed: User not found for {user_id}")
                return False
                
            expiration = result[0][0]
            current_time = datetime.now()
            time_diff = expiration - current_time
            
            bot_logger.logger.info(
                f"Detailed time check for user {user_id}:\n"
                f"Current time: {current_time}\n"
                f"Expiration: {expiration}\n"
                f"Time difference: {time_diff}\n"
                f"Days remaining: {time_diff.days}\n"
                f"Hours remaining: {time_diff.seconds // 3600}\n"
                f"Status: {'Active' if time_diff.total_seconds() > 0 else 'Expired'}"
            )
            
            return time_diff.total_seconds() > 0
                
        except Exception as e:
            bot_logger.log_error(f"Error in detailed time check for user {user_id}: {str(e)}")
            return False
            
    def get_user_expiration_info(self, user_id, bot_token):
        """Get detailed expiration information for a user"""
        try:
            result = execute_query("""
            SELECT access_until, created_at FROM users 
            WHERE id = %s AND bot_token = %s
            """, (user_id, bot_token))
            
            if not result:
                return None
                
            expiration = result[0][0]
            created_at = result[0][1]
            current_time = datetime.now()
            time_diff = expiration - current_time
            
            return {
                'expiration': expiration,
                'current_time': current_time,
                'days_remaining': time_diff.days,
                'hours_remaining': time_diff.seconds // 3600,
                'is_active': time_diff.total_seconds() > 0,
                'created_at': created_at,
                'credits': 0  # Para implementación futura
            }
            
        except Exception as e:
            bot_logger.log_error(f"Error getting expiration info for user {user_id}: {str(e)}")
            return None
