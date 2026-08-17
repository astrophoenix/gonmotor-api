# apps/authentication/permissions.py
from rest_framework import permissions


class IsAdminOrAsesor(permissions.BasePermission):
    """
    Permite el acceso solo a Administradores y Asesores de servicio.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Superusuarios de Django siempre tienen acceso
        if request.user.is_superuser:
            return True

        # Verificar el rol en el perfil
        return hasattr(request.user, 'profile') and request.user.profile.rol in ['ADMIN', 'ASESOR']


class IsMecanicoOrReadOnly(permissions.BasePermission):
    """
    Los mecánicos solo pueden leer, mientras que los administradores/asesores pueden modificar.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS: # GET, HEAD, OPTIONS
            return True

        return request.user.is_superuser or (
            hasattr(request.user, 'profile') and request.user.profile.rol in ['ADMIN', 'ASESOR']
        )