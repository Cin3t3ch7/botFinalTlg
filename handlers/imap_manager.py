import imaplib
import email
import re
import logging
from datetime import datetime, timedelta
from config import DEFAULT_IMAP_CONFIG
from database.connection import execute_query

# Configure logger for IMAP operations
imap_logger = logging.getLogger('imap_operations')
imap_logger.setLevel(logging.INFO)

class IMAPConnectionPool:
    def __init__(self):
        self.connections = {}
        self.last_used = {}
        imap_logger.info("Initialized new IMAP connection pool")
        
    def get_connection(self, email_or_domain, bot_token=None):
        current_time = datetime.now()
        
        # Extraer la parte base del email antes del '@'
        email_base = email_or_domain.split('@')[0] if '@' in email_or_domain else email_or_domain
        if '+' in email_base:
            email_base = email_base.split('+')[0]
        
        # Extraer dominio
        domain = email_or_domain.split('@')[1] if '@' in email_or_domain else email_or_domain
        
        # Primero, intentar obtener de la base de datos si hay un token
        config = None
        connection_key = None
        if bot_token:
            # Intentar primero con el dominio exacto
            result = execute_query("""
            SELECT domain, email, password, imap_server FROM imap_config 
            WHERE domain = %s AND bot_token = %s
            """, (domain, bot_token))
            
            if result:
                domain_info, email_account, password, imap_server = result[0]
                config = {
                    'EMAIL_ACCOUNT': email_account,
                    'PASSWORD': password,
                    'IMAP_SERVER': imap_server,
                    'IMAP_PORT': 993
                }
                connection_key = f"{domain}_{bot_token}"
                
        # Si no se encontró en la BD, usar la configuración predeterminada
        if not config:
            config = DEFAULT_IMAP_CONFIG.get(domain)
            connection_key = domain if config else None
            
            # Intentar con el email_base como última opción
            if not config:
                config = DEFAULT_IMAP_CONFIG.get(email_base)
                connection_key = email_base if config else None
        
        if not config or not connection_key:
            error_msg = f"No configuration found for: {email_or_domain}"
            imap_logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Verificar conexión existente
        if connection_key in self.connections:
            last_used = self.last_used.get(connection_key)
            if current_time - last_used > timedelta(minutes=5):
                imap_logger.info(f"Connection to {connection_key} expired, reconnecting...")
                self.close_connection(connection_key)
            else:
                try:
                    self.connections[connection_key].noop()
                    self.last_used[connection_key] = current_time
                    imap_logger.debug(f"Reusing existing connection to {connection_key}")
                    return self.connections[connection_key]
                except Exception as e:
                    imap_logger.error(f"Connection test failed for {connection_key}: {str(e)}")
                    self.close_connection(connection_key)
        
        try:
            imap_logger.info(f"Creating new connection to {config['IMAP_SERVER']}")
            conn = imaplib.IMAP4_SSL(config['IMAP_SERVER'], config['IMAP_PORT'])
            conn.login(config['EMAIL_ACCOUNT'], config['PASSWORD'])
            imap_logger.info(f"Successfully connected to {config['IMAP_SERVER']}")
            
            conn.select('INBOX')
            
            self.connections[connection_key] = conn
            self.last_used[connection_key] = current_time
            return conn
            
        except imaplib.IMAP4.error as e:
            error_msg = f"IMAP error connecting to {config['IMAP_SERVER']}: {str(e)}"
            imap_logger.error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Unexpected error connecting to {config['IMAP_SERVER']}: {str(e)}"
            imap_logger.error(error_msg)
            raise

    def close_connection(self, key):
        if key in self.connections:
            try:
                imap_logger.info(f"Closing connection to {key}")
                self.connections[key].logout()
            except Exception as e:
                imap_logger.error(f"Error closing connection to {key}: {str(e)}")
            finally:
                del self.connections[key]
                del self.last_used[key]
            
    def close_all_connections(self):
        imap_logger.info("Closing all IMAP connections")
        for key in list(self.connections.keys()):
            self.close_connection(key)
