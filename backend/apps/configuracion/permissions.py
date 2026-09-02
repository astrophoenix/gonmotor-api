from rest_framework import permissions
from apps.authentication.models import UsuarioEmpresa


class IsEmpresaAdminOrReadOnly(permissions.BasePermission):
    """
    Solo administradores pueden modificar la configuración de la empresa.
    - Superusuarios de Django: siempre permitidos.
    - Roles permitidos para escritura: ADMIN_SISTEMA, ADMIN_EMPRESA, ADMIN_TALLER.
    - El resto de usuarios autenticados solo pueden leer (GET/HEAD/OPTIONS).
    """

    ADMIN_ROLES = ('ADMIN_SISTEMA', 'ADMIN_EMPRESA', 'ADMIN_TALLER')

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        if request.user.is_superuser:
            return True

        empresa_id = view.get_empresa_id(request)
        return UsuarioEmpresa.objects.filter(
            user=request.user,
            empresa_id=empresa_id,
            rol__in=self.ADMIN_ROLES,
            is_active=True,
            empresa__is_active=True
        ).exists()