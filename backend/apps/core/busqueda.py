"""Búsqueda global de la topbar.

Un único endpoint que consulta las entidades principales del taller y
devuelve los resultados ya agrupados y acotados, para que el buscador del
frontend haga una sola petición por búsqueda (en lugar de una por módulo).

Decisiones:

- El tenant (`empresa_id`) se resuelve con la misma utilidad que el resto de
  la API; no se devuelve nada de otras empresas.
- Cada grupo se limita a `LIMITE_POR_GRUPO` resultados (las coincidencias más
  relevantes/ordenadas) para mantener la respuesta ligera.
- El `tipo` y el `id` son lo mínimo que necesita el frontend para construir la
  URL de destino; así la elección de rutas queda en el cliente.
- Los documentos del flujo (recepciones, inspecciones, cotizaciones y órdenes)
  se buscan también inactivos, igual que en sus listados y detalle. Su `meta`
  es el estado legible y `estado` el código crudo (para el color del badge).
  Los catálogos (clientes, vehículos, etc.) solo devuelven activos.
"""

from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.ids_publicos import id_publico, referencia_corta
from apps.authentication.permisos_repo import permisos_en_request
from apps.authentication.utils import get_contexto_desde_request, get_empresa_id_desde_request
from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion
from apps.inventario.models import Repuesto, Servicio
from apps.ordenes.models import InspeccionVehiculo, OrdenTrabajo, RecepcionVehiculo
from apps.proveedores.models import Proveedor
from apps.vehiculos.models import Vehiculo

LARGO_MINIMO = 2
LARGO_MAXIMO = 80
LIMITE_POR_GRUPO = 5


def _unir(*partes):
    """Une valores no vacíos con separador medio («·»)."""
    return ' · '.join(str(parte).strip() for parte in partes if parte not in (None, ''))


def _combinar(*condiciones):
    """Combina condiciones Q con OR."""
    total = None
    for condicion in condiciones:
        total = condicion if total is None else total | condicion
    return total if total is not None else Q()


def _talleres_desde_request(request):
    """Talleres aplicables al alcance del usuario para acotar la búsqueda.

    Devuelve ``None`` (administración sin taller activo: sin recorte) o la lista
    de ids de talleres permitidos (posiblemente vacía → sin resultados para las
    entidades con taller).
    """
    from apps.authentication.utils import talleres_filtro_de_request

    return talleres_filtro_de_request(request)


def _con_taller(queryset, talleres):
    """Recorta por taller cuando hay un alcance definido (estricto)."""
    if talleres is None:
        return queryset
    return queryset.filter(sucursal_id__in=talleres)


def _nombre_cliente(cliente):
    return cliente.nombre if cliente else None


def _placa(vehiculo):
    return vehiculo.placa if vehiculo else None


def _detalle_vehiculo(vehiculo):
    if not vehiculo:
        return None
    return f'{vehiculo.marca or ""} {vehiculo.modelo or ""}'.strip() or None


def _resultado(tipo, objeto, titulo, subtitulo=None, meta=None, estado=None):
    return {
        'tipo': tipo,
        'id': id_publico(objeto),
        'titulo': titulo or '—',
        'subtitulo': subtitulo,
        'meta': meta,
        # Código crudo del estado (documentos del flujo) para colorear el badge.
        'estado': estado,
    }


def _grupo(tipo, etiqueta, resultados, icono=None):
    return {
        'tipo': tipo,
        'etiqueta': etiqueta,
        'icono': icono,
        'resultados': resultados,
    }


def _buscar_clientes(empresa_id, q, talleres=None):
    queryset = (
        Cliente.objects.filter(empresa_id=empresa_id, is_active=True)
        .filter(_combinar(
            Q(nombre__icontains=q),
            Q(identificacion__icontains=q),
            Q(email__icontains=q),
            Q(telefono__icontains=q),
        ))
        .order_by('nombre')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado('clientes', cliente, cliente.nombre, _unir(cliente.identificacion, cliente.telefono))
        for cliente in queryset
    ]


def _buscar_vehiculos(empresa_id, q, talleres=None):
    normalizado = q.replace('-', '').replace(' ', '').upper()
    condiciones = _combinar(
        Q(placa__icontains=q),
        Q(marca__icontains=q),
        Q(modelo__icontains=q),
        Q(vin__icontains=q),
        Q(numero_motor__icontains=q),
        Q(propietarios__cliente__nombre__icontains=q),
        Q(propietarios__cliente__identificacion__icontains=q),
    )
    if normalizado:
        condiciones |= Q(placa__icontains=normalizado)

    queryset = (
        Vehiculo.objects.operativos_en(empresa_id)
        .filter(condiciones)
        .distinct()
        .order_by('placa')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado('vehiculos', vehiculo, vehiculo.placa, _detalle_vehiculo(vehiculo))
        for vehiculo in queryset
    ]


def _buscar_recepciones(empresa_id, q, talleres=None):
    queryset = _con_taller(
        RecepcionVehiculo.objects.filter(empresa_id=empresa_id),
        talleres,
    )
    queryset = (
        queryset
        .filter(_combinar(
            Q(numero_recepcion__icontains=q),
            Q(vehiculo__placa__icontains=q),
            Q(vehiculo__marca__icontains=q),
            Q(cliente__nombre__icontains=q),
            Q(cliente__identificacion__icontains=q),
        ))
        .select_related('cliente', 'vehiculo')
        .order_by('-fecha_ingreso')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado(
            'recepciones',
            recepcion,
            recepcion.numero_recepcion,
            _unir(_nombre_cliente(recepcion.cliente), _placa(recepcion.vehiculo)),
            recepcion.get_estado_display(),
            estado=recepcion.estado,
        )
        for recepcion in queryset
    ]


def _buscar_ordenes(empresa_id, q, talleres=None):
    queryset = _con_taller(
        OrdenTrabajo.objects.filter(empresa_id=empresa_id),
        talleres,
    )
    queryset = (
        queryset
        .filter(_combinar(
            Q(numero_orden__icontains=q),
            Q(vehiculo__placa__icontains=q),
            Q(vehiculo__marca__icontains=q),
            Q(cliente__nombre__icontains=q),
            Q(cliente__identificacion__icontains=q),
        ))
        .select_related('cliente', 'vehiculo')
        .order_by('-fecha_ingreso')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado(
            'ordenes',
            orden,
            orden.numero_orden,
            _unir(_nombre_cliente(orden.cliente), _placa(orden.vehiculo)),
            orden.get_estado_display(),
            estado=orden.estado,
        )
        for orden in queryset
    ]


def _buscar_cotizaciones(empresa_id, q, talleres=None):
    queryset = _con_taller(
        Cotizacion.objects.filter(empresa_id=empresa_id),
        talleres,
    )
    queryset = (
        queryset
        .filter(_combinar(
            Q(numero_cotizacion__icontains=q),
            Q(vehiculo__placa__icontains=q),
            Q(vehiculo__marca__icontains=q),
            Q(cliente__nombre__icontains=q),
            Q(cliente__identificacion__icontains=q),
        ))
        .select_related('cliente', 'vehiculo')
        .order_by('-created_at')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado(
            'cotizaciones',
            cotizacion,
            cotizacion.numero_cotizacion,
            _unir(_nombre_cliente(cotizacion.cliente), _placa(cotizacion.vehiculo)),
            cotizacion.get_estado_display(),
            estado=cotizacion.estado,
        )
        for cotizacion in queryset
    ]


def _buscar_inspecciones(empresa_id, q, talleres=None):
    queryset = _con_taller(
        InspeccionVehiculo.objects.filter(
            _combinar(Q(empresa_id=empresa_id), Q(recepcion__empresa_id=empresa_id)),
        ),
        talleres,
    )
    queryset = (
        queryset
        .filter(_combinar(
            Q(numero_inspeccion__icontains=q),
            Q(recepcion__numero_recepcion__icontains=q),
            Q(vehiculo__placa__icontains=q),
            Q(recepcion__vehiculo__placa__icontains=q),
            Q(cliente__nombre__icontains=q),
            Q(recepcion__cliente__nombre__icontains=q),
        ))
        .select_related('cliente', 'vehiculo', 'recepcion', 'recepcion__cliente', 'recepcion__vehiculo')
        .order_by('-created_at')[:LIMITE_POR_GRUPO]
    )
    resultados = []
    for inspeccion in queryset:
        cliente = inspeccion.cliente or (inspeccion.recepcion.cliente if inspeccion.recepcion_id else None)
        vehiculo = inspeccion.vehiculo or (inspeccion.recepcion.vehiculo if inspeccion.recepcion_id else None)
        numero = inspeccion.numero_inspeccion or f'Inspección {referencia_corta(inspeccion)}'
        resultados.append(_resultado(
            'inspecciones',
            inspeccion,
            numero,
            _unir(_nombre_cliente(cliente), _placa(vehiculo)),
            inspeccion.get_estado_display(),
            estado=inspeccion.estado,
        ))
    return resultados


def _buscar_proveedores(empresa_id, q, talleres=None):
    queryset = (
        Proveedor.objects.filter(empresa_id=empresa_id, is_active=True)
        .filter(_combinar(
            Q(nombre__icontains=q),
            Q(identificacion__icontains=q),
            Q(email__icontains=q),
            Q(telefono__icontains=q),
            Q(contacto__icontains=q),
        ))
        .order_by('nombre')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado('proveedores', proveedor, proveedor.nombre, _unir(proveedor.identificacion, proveedor.contacto or proveedor.telefono))
        for proveedor in queryset
    ]


def _buscar_repuestos(empresa_id, q, talleres=None):
    queryset = (
        Repuesto.objects.filter(empresa_id=empresa_id, is_active=True)
        .filter(_combinar(
            Q(codigo__icontains=q),
            Q(nombre__icontains=q),
            Q(marca__icontains=q),
            Q(numero_parte__icontains=q),
        ))
        .order_by('nombre')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado('repuestos', repuesto, repuesto.nombre, _unir(repuesto.codigo, repuesto.marca))
        for repuesto in queryset
    ]


def _buscar_servicios(empresa_id, q, talleres=None):
    queryset = (
        Servicio.objects.filter(empresa_id=empresa_id, is_active=True)
        .filter(_combinar(
            Q(codigo__icontains=q),
            Q(nombre__icontains=q),
            Q(descripcion__icontains=q),
        ))
        .order_by('nombre')[:LIMITE_POR_GRUPO]
    )
    return [
        _resultado('servicios', servicio, servicio.nombre, servicio.codigo)
        for servicio in queryset
    ]


# Orden de presentación de los grupos en el buscador (los más usados primero).
# Recurso de permisos que exige cada grupo (`ver`). Repuestos y servicios
# comparten `inventario`; el resto coincide con su tipo.
RECURSO_POR_TIPO = {'repuestos': 'inventario', 'servicios': 'inventario'}

BUSCADORES = (
    ('clientes', 'Clientes', _buscar_clientes),
    ('vehiculos', 'Vehículos', _buscar_vehiculos),
    ('recepciones', 'Recepciones', _buscar_recepciones),
    ('ordenes', 'Órdenes de trabajo', _buscar_ordenes),
    ('cotizaciones', 'Cotizaciones', _buscar_cotizaciones),
    ('inspecciones', 'Inspecciones', _buscar_inspecciones),
    ('proveedores', 'Proveedores', _buscar_proveedores),
    ('repuestos', 'Repuestos', _buscar_repuestos),
    ('servicios', 'Servicios', _buscar_servicios),
)


class BusquedaGlobalView(APIView):
    """`GET /api/core/buscar/?q=<término>` → resultados agrupados por entidad.

    Respuesta:

        {
          "q": "gsu",
          "total": 3,
          "grupos": [
            {
              "tipo": "vehiculos",
              "etiqueta": "Vehículos",
              "resultados": [
                {"tipo": "vehiculos", "id": 7, "titulo": "GSU5812",
                 "subtitulo": "Toyota Corolla", "meta": null, "estado": null}
              ]
            }
          ]
        }

    Con menos de `LARGO_MINIMO` caracteres devuelve una lista vacía (no es un
    error) para no disparar consultas mientras el usuario apenas escribe.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        empresa_id = get_empresa_id_desde_request(request)
        if not empresa_id:
            return Response(
                {'detail': 'No hay una empresa activa asociada al usuario.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        q = (request.query_params.get('q') or '').strip()
        if len(q) < LARGO_MINIMO:
            return Response({'q': q, 'total': 0, 'grupos': []})
        q = q[:LARGO_MAXIMO]

        grupos = []
        talleres = _talleres_desde_request(request)
        contexto = get_contexto_desde_request(request)
        permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
        for tipo, etiqueta, buscador in BUSCADORES:
            # Solo lo que el rol puede consultar: la búsqueda no es otra puerta.
            if 'ver' not in permisos.get(RECURSO_POR_TIPO.get(tipo, tipo), ()):
                continue
            resultados = buscador(empresa_id, q, talleres)
            if resultados:
                grupos.append(_grupo(tipo, etiqueta, resultados))

        total = sum(len(grupo['resultados']) for grupo in grupos)
        return Response({'q': q, 'total': total, 'grupos': grupos})
