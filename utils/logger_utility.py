import logging
from datetime import datetime
import os

class BotLogger:
    def __init__(self, name='my_bot'):
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Configure main logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        # Avoid duplicate logs
        if not self.logger.handlers:
            # Create formatters
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            
            # File handler - daily rotation
            file_handler = logging.FileHandler(
                os.path.join(logs_dir, f'bot_{datetime.now().strftime("%Y%m%d")}.log')
            )
            file_handler.setFormatter(formatter)
            
            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            
            # Add handlers
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
    
    def log_bot_start(self, pid):
        self.logger.info("Iniciando el bot de Telegram")
        self.logger.info(f"Creado nuevo archivo PID {pid}")
    
    def log_bot_ready(self):
        self.logger.info("Bot iniciado correctamente")
    
    def log_imap_connection(self, domain):
        self.logger.info(f"Sesión IMAP iniciada para el dominio {domain}")
    
    def log_search_attempt(self, email, attempt, service):
        self.logger.info(f"Intento {attempt} de búsqueda de correos {service} para {email}")
    
    def log_code_found(self, email, code, service):
        self.logger.info(f"Código {service} encontrado para {email}: {code}")
    
    def log_code_not_found(self, email, service):
        self.logger.info(f"No se encontró código {service} para {email}")
    
    def log_user_command(self, user_id, command):
        self.logger.info(f"Usuario {user_id} ejecutó el comando: {command}")
    
    def log_error(self, error_msg):
        self.logger.error(f"Error: {error_msg}")
    
    def log_email_operation(self, operation_type, user_id, email):
        """Log email operations without expiration info"""
        self.logger.info(f"Email {operation_type} - User: {user_id}, Email: {email}")

    def log_user_status(self, user_id, is_valid):
        """Log para el estado de validez de un usuario"""
        status = "activo" if is_valid else "expirado"
        self.logger.info(f"Usuario {user_id} está {status}")

    def log_email_validation(self, user_id, email, is_valid):
        """Log para validación de correos"""
        result = "autorizado" if is_valid else "no autorizado"
        self.logger.info(f"Correo {email} para usuario {user_id} está {result}")
    
    def log_reseller_action(reseller_id, action, target):
        bot_logger.logger.info(f"Reseller {reseller_id} performed {action} on {target}")

# Create singleton instance
bot_logger = BotLogger()
