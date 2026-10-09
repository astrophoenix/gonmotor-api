from rest_framework import viewsets, permissions
from rest_framework.exceptions import PermissionDenied

from apps.authentication.permissions import TieneRecurso
from apps.authentication.utils import get_contexto_desde_request
from .models import Empresa
from .serializers import EmpresaSerializer


class EmpresaViewSet(viewsets.ModelViewSet):
    queryset = Empresa.objects.filter(is_active=True).order_by('nombre_comercial')
    serializer_class = EmpresaSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('empresa')]

    def perform_create(self, serializer):
        contexto = get_contexto_desde_request(self.request)
        if contexto.hay_contexto and contexto.rol != 'ADMIN_SISTEMA':
            raise PermissionDenied('Solo el administrador del sistema puede dar de alta una empresa.')
        super().perform_create(serializer)
