from rest_framework import permissions, viewsets

from apps.authentication.permissions import TieneRecurso
from apps.authentication.utils import (
    get_empresa_id_desde_request,
    get_taller_desde_request,
)
from apps.core.mixins import FiltroTallerMixin

from .models import Gasto
from .serializers import GastoSerializer


class GastoViewSet(FiltroTallerMixin, viewsets.ModelViewSet):
    serializer_class = GastoSerializer
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('gastos')]
    queryset = Gasto.objects.select_related('taller', 'creado_por', 'empresa')
    # Taller activo (`X-Taller-ID`); el filtro manual `?taller_id=` manda si viene.
    taller_filter_field = 'taller'
    taller_query_param = 'taller_id'

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_active=True)
        request = self.request
        if not request or not request.user.is_authenticated:
            return queryset.none()

        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return queryset.none()
        queryset = queryset.filter(empresa_id=empresa_id)

        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            queryset = queryset.filter(fecha__gte=fecha_desde)

        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            queryset = queryset.filter(fecha__lte=fecha_hasta)

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria=categoria)

        return self.filtrar_por_taller(queryset)
