from rest_framework import viewsets, status
from rest_framework.response import Response
from apps.empresas.models import Empresa
from apps.authentication.utils import get_empresa_id_desde_request
from .serializers import EmpresaConfigSerializer
from .permissions import IsEmpresaAdminOrReadOnly


class EmpresaConfigViewSet(viewsets.ModelViewSet):
    """
    Endpoint de configuración de la empresa del taller (datos fiscales/corporativos).

    GET   /api/configuracion/empresa/   -> Empresa del tenant en sesión
    GET   /api/configuracion/empresa/{id}/ -> Empresa por id (tenant permitido)
    PATCH /api/configuracion/empresa/{id}/ -> Actualiza datos (solo administradores)
    """
    serializer_class = EmpresaConfigSerializer
    permission_classes = [IsEmpresaAdminOrReadOnly]

    def get_empresa_id(self, request):
        return get_empresa_id_desde_request(request)

    def get_queryset(self):
        return Empresa.objects.filter(is_active=True)

    def list(self, request, *args, **kwargs):
        empresa_id = self.get_empresa_id(request)
        if not empresa_id:
            return Response(
                {"detail": "No se pudo determinar la empresa en sesión."},
                status=status.HTTP_403_FORBIDDEN
            )
        try:
            empresa = Empresa.objects.get(id=empresa_id, is_active=True)
        except Empresa.DoesNotExist:
            return Response(
                {"detail": "La empresa en sesión no existe o está inactiva."},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = self.get_serializer(empresa)
        return Response(serializer.data)