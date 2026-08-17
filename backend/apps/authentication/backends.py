from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Permite la autenticación usando tanto el username como el email.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        try:
            # Busca al usuario si el valor ingresado coincide con username O email (sin distinguir mayúsculas)
            user = User.objects.get(
                Q(username__iexact=username) | Q(email__iexact=username)
            )
        except User.DoesNotExist:
            # Ejecuta la función de hash de contraseña para evitar ataques de temporización
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            # En caso de que existan múltiples usuarios con el mismo email, toma el primero activo
            user = User.objects.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None