from rest_framework import viewsets

from apps.authentication.utils import (
    get_empresa_id_desde_request,
    get_taller_desde_request,
)

from .models import Gasto
from .serializers import GastoSerializer


class GastoViewSet(viewsets.ModelViewSet):
    serializer_class = GastoSerializer
    queryset = Gasto.objects.select_related('taller', 'creado_por', 'empresa')

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_active=True)
        request = self.request
        if not request or not request.user.is_authenticated:
            return queryset.none()

        empresa_id = get_empresa_id_desde_request(request)
        if empresa_id:
            queryset = queryset.filter(empresa_id=empresa_id)

        taller_id = self.request.query_params.get('taller_id')
        if taller_id:
            queryset = queryset.filter(taller_id=taller_id)

        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            queryset = queryset.filter(fecha__gte=fecha_desde)

        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            queryset = queryset.filter(fecha__lte=fecha_hasta)

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria=categoria)

        return queryset
