# apps/authentication/validators.py
import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class ComplexPasswordValidator:
    """
    Valida que la contraseña contenga al menos:
    - Una letra mayúscula.
    - Un carácter especial dentro del grupo permitido: @$!%*?&._-
    """
    def __init__(self, allowed_special_chars=r"@$!%*?&._-"):
        self.allowed_special_chars = allowed_special_chars

    def validate(self, password, user=None):
        # 1. Validar al menos una letra mayúscula
        if not re.search(r'[A-Z]', password):
            raise ValidationError(
                _("La contraseña debe contener al menos una letra mayúscula (A-Z)."),
                code='password_no_uppercase',
            )

        # 2. Validar al menos un carácter especial permitido
        pattern = f'[{re.escape(self.allowed_special_chars)}]'
        if not re.search(pattern, password):
            raise ValidationError(
                _(f"La contraseña debe contener al menos un carácter especial del siguiente grupo: {self.allowed_special_chars}"),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            f"Tu contraseña debe incluir al menos una letra mayúscula y un carácter especial ({self.allowed_special_chars})."
        )