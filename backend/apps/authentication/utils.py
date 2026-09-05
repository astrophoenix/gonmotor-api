from apps.authentication.models import UsuarioEmpresa
from apps.empresas.models import Empresa  # O el modelo correspondiente a Empresa en tu app


def get_empresa_id_desde_request(request):
    """
    Obtiene y valida el empresa_id activo para el usuario en sesión.
    Aplica para usuarios normales y superusuarios (garantizando aislamiento por Tenant).
    """
    user = request.user
    if not user or not user.is_authenticated:
        return None

    empresa_id = None

    # 1. Leer desde las claims del JWT
    if hasattr(request, 'auth') and isinstance(request.auth, dict):
        empresa_id = request.auth.get('empresa_id')

    # 2. Leer desde la cabecera HTTP personalizada X-Empresa-ID
    if not empresa_id:
        empresa_id = request.headers.get('X-Empresa-ID')

    # 3. Validación para usuarios normales
    if empresa_id and not user.is_superuser:
        tiene_acceso = UsuarioEmpresa.objects.filter(
            user=user,
            empresa_id=empresa_id,
            is_active=True,
            empresa__is_active=True
        ).exists()

        if tiene_acceso:
            return empresa_id
        return None  # No tiene acceso a esta empresa

    # 4. Si es Superusuario y envió un empresa_id válido, se le asigna dicho contexto
    if empresa_id and user.is_superuser:
        if Empresa.objects.filter(id=empresa_id, is_active=True).exists():
            return empresa_id

    # 5. Fallback: Si no se envió ningún ID de empresa explícito
    # Para usuarios estándar:
    empresas_usuario = UsuarioEmpresa.objects.filter(
        user=user,
        is_active=True,
        empresa__is_active=True
    ).values_list('empresa_id', flat=True)

    if empresas_usuario.count() == 1:
        return empresas_usuario.first()

    # Para Superusuarios sin empresa explícita en JWT/Header:
    if user.is_superuser:
        primera_relacion = empresas_usuario.first()
        if primera_relacion:
            return primera_relacion
        empresa_global = Empresa.objects.filter(is_active=True).first()
        return empresa_global.id if empresa_global else None

    return None