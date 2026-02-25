from datetime import datetime, timedelta

class ResellerTimeManager:
    @staticmethod
    def validate_time_input(time_str: str, max_days: int = 30) -> tuple[bool, str]:
        """
        Valida que el tiempo solicitado no exceda el máximo permitido
        Retorna: (es_válido, mensaje_error)
        """
        try:
            amount = int(time_str[:-1])
            unit = time_str[-1].lower()
            
            if unit not in ['d', 'm']:
                return False, "Unidad de tiempo inválida. Use 'd' para días o 'm' para minutos"
                
            if unit == 'd':
                if amount > max_days:
                    return False, f"No puedes agregar más de {max_days} días"
                return True, ""
                
            elif unit == 'm':
                max_minutes = max_days * 1440  # minutos en max_days
                if amount > max_minutes:
                    return False, f"No puedes agregar más de {max_minutes} minutos ({max_days} días)"
                return True, ""
                
        except ValueError:
            return False, "El valor de tiempo debe ser un número seguido de 'd' o 'm'"
            
        return False, "Error de validación de tiempo"
    
    @staticmethod
    def calculate_expiration(time_str: str) -> datetime:
        """Calcula la fecha de expiración basada en el tiempo proporcionado"""
        amount = int(time_str[:-1])
        unit = time_str[-1].lower()
        
        if unit == 'd':
            return datetime.now() + timedelta(days=amount)
        elif unit == 'm':
            return datetime.now() + timedelta(minutes=amount)
        
        raise ValueError("Formato de tiempo inválido")
    
def validate_reseller_time_limit(self, reseller_id, requested_time):
    """Validar que el tiempo solicitado no exceda el límite del revendedor"""
    max_allowed = self.get_reseller_max_time(reseller_id)
    return self.compare_time_periods(requested_time, max_allowed)
