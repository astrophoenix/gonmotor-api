"""Historial de un vehículo dentro de la empresa activa.

Agrupa los documentos del flujo (cita, recepción, inspección, cotización y
orden) en **visitas**: todo lo que está enlazado por las FKs que describe
`apps/ordenes/relaciones_flujo.py` forma una sola visita. Lo que no llega a una
recepción, inspección u orden (una cita cancelada, una cotización suelta) queda
como **evento** en la misma línea de tiempo.

Alcance: la empresa del JWT y todos sus talleres, sin recorte por taller. Es
una excepción deliberada a `FiltroTallerMixin`: al técnico le sirve saber qué
se hizo en otra sede. Los documentos de un taller fuera del alcance del usuario
se marcan `accesible=False` (su ficha sigue protegida por taller).

Todo se carga en un número fijo de consultas y se arma en memoria.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.authentication.permisos_repo import permisos_en_request
from apps.authentication.utils import get_contexto_desde_request, talleres_permitidos
from apps.citas.models import Cita
from apps.core.ids_publicos import id_publico, referencia_corta
from apps.cotizaciones.models import Cotizacion
from apps.ordenes.models import (
    DetalleRepuestoOrdenTrabajo,
    DetalleServicioOrdenTrabajo,
    InspeccionVehiculo,
    OrdenTrabajo,
    RecepcionVehiculo,
)
from apps.ordenes.relaciones_flujo import (
    _item_cita,
    _item_inspeccion,
    _item_orden,
    _item_recepcion,
)

ESTADOS_ORDEN_CERRADA = (OrdenTrabajo.EstadoOrden.COMPLETADO, OrdenTrabajo.EstadoOrden.ENTREGADO)
MAX_FOTOS_POR_VISITA = 12
MAX_PENDIENTES = 5
RECURSO_POR_TIPO = {
    'cita': 'citas',
    'recepcion': 'recepciones',
    'inspeccion': 'inspecciones',
    'cotizacion': 'cotizaciones',
    'orden': 'ordenes',
}
DIAS_MINIMOS_KM_POR_ANIO = 30


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


class _Grupos:
    """Union-find sobre claves ('tipo', pk)."""

    def __init__(self):
        self.padre = {}

    def agregar(self, clave):
        self.padre.setdefault(clave, clave)

    def raiz(self, clave):
        while self.padre[clave] != clave:
            self.padre[clave] = self.padre[self.padre[clave]]
            clave = self.padre[clave]
        return clave

    def unir(self, a, b):
        if a in self.padre and b in self.padre:
            self.padre[self.raiz(a)] = self.raiz(b)


def _como_datetime(fecha):
    if fecha is None:
        return None
    if isinstance(fecha, datetime):
        return fecha
    return timezone.make_aware(datetime.combine(fecha, time.min))


def _nombre_usuario(usuario):
    if usuario is None:
        return None
    return usuario.get_full_name() or usuario.username


def _taller(taller):
    if taller is None:
        return None
    return {'id': id_publico(taller), 'nombre': taller.nombre}


def _url_absoluta(request, archivo):
    if not archivo:
        return None
    url = archivo.url
    return request.build_absolute_uri(url) if request is not None else url


def _foto(request, foto, origen):
    return {
        'id': id_publico(foto),
        'origen': origen,
        'imagen': _url_absoluta(request, foto.imagen),
        'miniatura': _url_absoluta(request, foto.miniatura or foto.imagen),
        'descripcion': foto.descripcion,
    }


def _testigos_encendidos(inspeccion):
    return [
        campo.verbose_name
        for campo in inspeccion._meta.fields
        if campo.name.startswith('testigo_') and getattr(inspeccion, campo.name)
    ]


def _normalizar(texto):
    return ' '.join((texto or '').lower().split())


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def _cargar_documentos(vehiculo, empresa_id):
    ordenes = list(
        OrdenTrabajo.objects.filter(empresa_id=empresa_id, vehiculo=vehiculo)
        .select_related('sucursal', 'mecanico_principal')
        .prefetch_related(
            Prefetch('servicios', queryset=DetalleServicioOrdenTrabajo.objects.order_by('id')),
            Prefetch('repuestos', queryset=DetalleRepuestoOrdenTrabajo.objects.order_by('id')),
            'fotos',
        )
    )
    recepciones = list(
        RecepcionVehiculo.objects.filter(empresa_id=empresa_id, vehiculo=vehiculo)
        .select_related('sucursal')
        .prefetch_related('fotos')
    )
    inspecciones = list(
        InspeccionVehiculo.objects.filter(empresa_id=empresa_id, vehiculo=vehiculo)
        .select_related('sucursal', 'responsable')
        .prefetch_related('fotos', 'servicios_detectados', 'repuestos_sugeridos')
    )
    # La cotización puede no tener vehículo: se cuelga de su documento de origen.
    cotizaciones = list(
        Cotizacion.objects.filter(empresa_id=empresa_id)
        .filter(
            Q(vehiculo=vehiculo)
            | Q(recepcion_origen__in=[r.pk for r in recepciones])
            | Q(inspeccion_origen__in=[i.pk for i in inspecciones])
            | Q(orden_trabajo_origen__in=[o.pk for o in ordenes])
        )
        .select_related('sucursal')
        .distinct()
    )
    citas = list(
        Cita.objects.filter(empresa_id=empresa_id, vehiculo=vehiculo).select_related('taller')
    )
    return ordenes, recepciones, inspecciones, cotizaciones, citas


def _agrupar(ordenes, recepciones, inspecciones, cotizaciones, citas):
    grupos = _Grupos()
    for tipo, docs in (
        ('orden', ordenes),
        ('recepcion', recepciones),
        ('inspeccion', inspecciones),
        ('cotizacion', cotizaciones),
        ('cita', citas),
    ):
        for doc in docs:
            grupos.agregar((tipo, doc.pk))

    for orden in ordenes:
        if orden.cotizacion_origen_id:
            grupos.unir(('orden', orden.pk), ('cotizacion', orden.cotizacion_origen_id))
    for recepcion in recepciones:
        if recepcion.orden_trabajo_id:
            grupos.unir(('recepcion', recepcion.pk), ('orden', recepcion.orden_trabajo_id))
    for inspeccion in inspecciones:
        if inspeccion.recepcion_id:
            grupos.unir(('inspeccion', inspeccion.pk), ('recepcion', inspeccion.recepcion_id))
        if inspeccion.orden_trabajo_id:
            grupos.unir(('inspeccion', inspeccion.pk), ('orden', inspeccion.orden_trabajo_id))
    for cotizacion in cotizaciones:
        for tipo, pk in (
            ('recepcion', cotizacion.recepcion_origen_id),
            ('inspeccion', cotizacion.inspeccion_origen_id),
            ('orden', cotizacion.orden_trabajo_origen_id),
        ):
            if pk:
                grupos.unir(('cotizacion', cotizacion.pk), (tipo, pk))
    for cita in citas:
        if cita.recepcion_generada_id:
            grupos.unir(('cita', cita.pk), ('recepcion', cita.recepcion_generada_id))

    conjuntos = {}
    for tipo, docs in (
        ('cita', citas),
        ('recepcion', recepciones),
        ('inspeccion', inspecciones),
        ('cotizacion', cotizaciones),
        ('orden', ordenes),
    ):
        for doc in sorted(docs, key=lambda d: (d.created_at, d.pk)):
            raiz = grupos.raiz((tipo, doc.pk))
            conjuntos.setdefault(raiz, {t: [] for t in ('cita', 'recepcion', 'inspeccion', 'cotizacion', 'orden')})
            conjuntos[raiz][tipo].append(doc)
    return list(conjuntos.values())


# ---------------------------------------------------------------------------
# Serialización
# ---------------------------------------------------------------------------


def _documento(item, taller_id, permitidos, visible):
    if not visible:
        return None
    item = {k: item[k] for k in ('id', 'numero', 'estado', 'estadoDisplay', 'url')}
    item['accesible'] = taller_id is None or taller_id in permitidos
    return item


def _item_cotizacion_simple(cotizacion):
    return {
        'id': id_publico(cotizacion),
        'numero': cotizacion.numero_cotizacion or referencia_corta(cotizacion),
        'estado': cotizacion.estado,
        'estadoDisplay': cotizacion.get_estado_display(),
        'url': f'/crud/cotizaciones/ver/?id={id_publico(cotizacion)}',
    }


def _documentos(docs, permitidos, ver):
    salida = {}
    for tipo, item_de, taller_de in (
        ('cita', _item_cita, lambda d: d.taller_id),
        ('recepcion', _item_recepcion, lambda d: d.sucursal_id),
        ('inspeccion', _item_inspeccion, lambda d: d.sucursal_id),
        ('cotizacion', _item_cotizacion_simple, lambda d: d.sucursal_id),
        ('orden', _item_orden, lambda d: d.sucursal_id),
    ):
        recurso = RECURSO_POR_TIPO[tipo]
        salida[tipo] = [
            doc for doc in (
                _documento(item_de(d), taller_de(d), permitidos, recurso in ver) for d in docs[tipo]
            )
            if doc is not None
        ]
    return salida


def _visita(request, docs, permitidos, ver):
    ordenes, recepciones, inspecciones = docs['orden'], docs['recepcion'], docs['inspeccion']
    orden = ordenes[-1] if ordenes else None
    recepcion = recepciones[0] if recepciones else None
    inspeccion = inspecciones[-1] if inspecciones else None
    cita = docs['cita'][0] if docs['cita'] else None

    fechas = [
        f for f in (
            recepcion.fecha_ingreso if recepcion else None,
            orden.fecha_ingreso if orden else None,
            inspeccion.fecha_inspeccion if inspeccion else None,
        ) if f
    ]
    fecha = min(fechas) if fechas else None
    fecha_salida = (orden.fecha_entrega if orden else None) or (recepcion.fecha_salida if recepcion else None)

    lecturas = [
        km for km in (
            recepcion.kilometraje_ingreso if recepcion else None,
            inspeccion.kilometraje_diagnostico if inspeccion else None,
        ) if km is not None
    ]
    kilometraje = orden.kilometraje if orden else (max(lecturas) if lecturas else None)

    tipo_trabajo = (
        (orden.tipo_trabajo, orden.get_tipo_trabajo_display()) if orden
        else (inspeccion.tipo_inspeccion, inspeccion.get_tipo_inspeccion_display()) if inspeccion
        else (recepcion.tipo_recepcion, recepcion.get_tipo_recepcion_display())
    )
    motivo = (
        (recepcion.motivo_ingreso if recepcion else None)
        or (inspeccion.motivo_ingreso if inspeccion else None)
        or (cita.motivo_descripcion if cita else None)
        or (orden.observaciones if orden else None)
        or ''
    )
    taller = (orden or recepcion or inspeccion).sucursal

    if orden:
        estado = {'documento': 'orden', 'valor': orden.estado, 'display': orden.get_estado_display()}
    elif inspeccion:
        estado = {'documento': 'inspeccion', 'valor': inspeccion.estado, 'display': inspeccion.get_estado_display()}
    else:
        estado = {'documento': 'recepcion', 'valor': recepcion.estado, 'display': recepcion.get_estado_display()}

    servicios = [s.descripcion for o in ordenes for s in o.servicios.all()] if 'ordenes' in ver else []
    repuestos = [
        {'descripcion': r.descripcion, 'cantidad': str(r.cantidad.normalize())}
        for o in ordenes for r in o.repuestos.all()
    ] if 'ordenes' in ver else []

    fotos = []
    if 'recepciones' in ver:
        fotos += [_foto(request, f, 'recepcion') for r in recepciones for f in r.fotos.all()]
    if 'inspecciones' in ver:
        fotos += [_foto(request, f, 'inspeccion') for i in inspecciones for f in i.fotos.all()]
    if 'ordenes' in ver:
        fotos += [_foto(request, f, 'orden') for o in ordenes for f in o.fotos.all()]

    documentos = _documentos(docs, permitidos, ver)
    detalle = {
        'diagnostico': inspeccion.diagnostico_tecnico if inspeccion and 'inspecciones' in ver else None,
        'codigos_dtc': inspeccion.codigos_dtc if inspeccion and 'inspecciones' in ver else None,
        'recomendaciones': inspeccion.recomendaciones if inspeccion and 'inspecciones' in ver else None,
        'testigos': _testigos_encendidos(inspeccion) if inspeccion and 'inspecciones' in ver else [],
        'repuestos': repuestos,
        'fotos': fotos[:MAX_FOTOS_POR_VISITA],
        'total_fotos': len(fotos),
    }

    return {
        'tipo': 'visita',
        'fecha': fecha,
        'fecha_salida': fecha_salida,
        'dias_en_taller': (fecha_salida.date() - fecha.date()).days if fecha and fecha_salida else None,
        'kilometraje': kilometraje,
        'tipo_trabajo': tipo_trabajo[0],
        'tipo_trabajo_display': tipo_trabajo[1],
        'motivo': motivo,
        'taller': _taller(taller),
        'estado': estado,
        'servicios': servicios,
        'total': str(orden.total) if orden and 'ordenes' in ver else None,
        'tecnico': _nombre_usuario(
            (orden.mecanico_principal if orden else None)
            or (inspeccion.responsable if inspeccion else None)
        ),
        'documentos': documentos,
        'detalle': detalle,
        '_texto': _normalizar(' '.join([
            motivo,
            ' '.join(servicios),
            ' '.join(r['descripcion'] for r in repuestos),
            detalle['diagnostico'] or '',
            detalle['codigos_dtc'] or '',
            detalle['recomendaciones'] or '',
            ' '.join(d['numero'] for lista in documentos.values() for d in lista),
        ])),
        '_taller_id': taller.pk if taller else None,
    }


def _eventos(docs, permitidos, ver):
    """Documentos que no llegaron a una visita: citas y cotizaciones sueltas."""
    eventos = []
    if 'citas' in ver:
        for cita in docs['cita']:
            eventos.append({
                'tipo': 'evento',
                'subtipo': 'cita',
                'fecha': _como_datetime(cita.fecha_hora_programada or cita.fecha_cita),
                'texto': f'Cita {cita.get_estado_display().lower()}',
                'motivo': cita.motivo_descripcion or cita.get_motivo_display(),
                'kilometraje': cita.kilometraje_aproximado,
                'taller': _taller(cita.taller),
                'documentos': _documentos(docs, permitidos, ver),
                '_texto': _normalizar(f'{cita.motivo_descripcion or ""} {cita.get_motivo_display()}'),
                '_taller_id': cita.taller_id,
                '_tipo_trabajo': cita.motivo,
            })
    if 'cotizaciones' in ver:
        for cotizacion in docs['cotizacion']:
            eventos.append({
                'tipo': 'evento',
                'subtipo': 'cotizacion',
                'fecha': cotizacion.created_at,
                'texto': f'Cotización {cotizacion.get_estado_display().lower()}',
                'motivo': '',
                'kilometraje': None,
                'total': str(cotizacion.total),
                'taller': _taller(cotizacion.sucursal),
                'documentos': _documentos(docs, permitidos, ver),
                '_texto': _normalizar(cotizacion.numero_cotizacion),
                '_taller_id': cotizacion.sucursal_id,
                '_tipo_trabajo': None,
            })
    return eventos


def _pendientes(visitas_docs):
    """Lo diagnosticado en una inspección que ninguna orden posterior ejecutó."""
    ejecutados = []  # (fecha, descripción normalizada)
    for docs in visitas_docs:
        for orden in docs['orden']:
            for linea in list(orden.servicios.all()) + list(orden.repuestos.all()):
                ejecutados.append((orden.fecha_ingreso, _normalizar(linea.descripcion)))

    pendientes = []
    for docs in visitas_docs:
        for inspeccion in docs['inspeccion']:
            if inspeccion.estado != 'FINALIZADA':
                continue
            lineas = list(inspeccion.servicios_detectados.all()) + list(inspeccion.repuestos_sugeridos.all())
            for linea in lineas:
                descripcion = _normalizar(linea.descripcion)
                hecho = any(
                    desc == descripcion and fecha >= inspeccion.fecha_inspeccion - timedelta(days=1)
                    for fecha, desc in ejecutados
                )
                if not hecho:
                    pendientes.append({
                        'descripcion': linea.descripcion,
                        'prioridad': linea.prioridad,
                        'prioridad_display': linea.get_prioridad_display(),
                        'origen': {
                            'id': id_publico(inspeccion),
                            'numero': inspeccion.numero_inspeccion or referencia_corta(inspeccion),
                            'url': f'/crud/inspecciones/ver/?id={id_publico(inspeccion)}',
                        },
                        'fecha': inspeccion.fecha_inspeccion,
                    })
    orden_prioridad = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2}
    pendientes.sort(key=lambda p: (orden_prioridad.get(p['prioridad'], 3), -p['fecha'].timestamp()))
    return pendientes[:MAX_PENDIENTES]


def _indicadores(visitas, vehiculo, ver):
    con_fecha = [v for v in visitas if v['fecha']]
    ultima = max((v['fecha'] for v in con_fecha), default=None)
    lecturas = sorted((v['fecha'], v['kilometraje']) for v in con_fecha if v['kilometraje'] is not None)
    km_por_anio = None
    if len(lecturas) >= 2:
        dias = (lecturas[-1][0] - lecturas[0][0]).days
        recorrido = lecturas[-1][1] - lecturas[0][1]
        if dias >= DIAS_MINIMOS_KM_POR_ANIO and recorrido > 0:
            km_por_anio = round(recorrido * 365 / dias)
    gasto = None
    if 'ordenes' in ver:
        gasto = sum(
            (Decimal(v['total']) for v in visitas
             if v['total'] is not None and v['estado']['documento'] == 'orden'
             and v['estado']['valor'] in ESTADOS_ORDEN_CERRADA),
            Decimal('0.00'),
        )
    return {
        'visitas': len(visitas),
        'ultima_visita': ultima,
        'dias_desde_ultima_visita': (timezone.now() - ultima).days if ultima else None,
        'kilometraje_actual': vehiculo.kilometraje_actual,
        'km_por_anio': km_por_anio,
        'gasto_total': str(gasto) if gasto is not None else None,
    }


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def _filtrar(entradas, filtros):
    q = _normalizar(filtros.get('q'))
    tipo = filtros.get('tipo')
    taller_pk = filtros.get('taller_pk')
    desde, hasta = filtros.get('desde'), filtros.get('hasta')

    def pasa(e):
        if q and q not in e['_texto']:
            return False
        if tipo:
            tipo_e = e.get('tipo_trabajo') if e['tipo'] == 'visita' else e.get('_tipo_trabajo')
            if tipo_e != tipo:
                return False
        if taller_pk is not None and e['_taller_id'] != taller_pk:
            return False
        fecha = e['fecha'].date() if e['fecha'] else None
        if desde and (fecha is None or fecha < desde):
            return False
        if hasta and (fecha is None or fecha > hasta):
            return False
        return True

    return [e for e in entradas if pasa(e)]


def construir_historial(request, vehiculo, empresa_id, filtros):
    """Devuelve `(indicadores, pendientes, entradas)` con las entradas ya filtradas."""
    contexto = get_contexto_desde_request(request)
    permisos = permisos_en_request(request, contexto.rol, contexto.empresa_id)
    ver = {recurso for recurso, acciones in permisos.items() if 'ver' in acciones}
    permitidos = set(talleres_permitidos(request.user, contexto).values_list('pk', flat=True))

    grupos = _agrupar(*_cargar_documentos(vehiculo, empresa_id))
    visitas_docs = [g for g in grupos if g['orden'] or g['recepcion'] or g['inspeccion']]
    sueltos = [g for g in grupos if not (g['orden'] or g['recepcion'] or g['inspeccion'])]

    visitas = [_visita(request, docs, permitidos, ver) for docs in visitas_docs]
    eventos = [e for docs in sueltos for e in _eventos(docs, permitidos, ver)]

    indicadores = _indicadores(visitas, vehiculo, ver)
    # Opciones del filtro Taller: los talleres donde el vehículo tiene historial.
    talleres = {e['taller']['id']: e['taller'] for e in visitas + eventos if e.get('taller')}
    indicadores['talleres'] = sorted(talleres.values(), key=lambda t: t['nombre'])
    pendientes = _pendientes(visitas_docs) if 'inspecciones' in ver else []

    entradas = _filtrar(visitas + eventos, filtros)
    entradas.sort(key=lambda e: e['fecha'] or timezone.now(), reverse=True)
    for entrada in entradas:
        entrada.pop('_texto', None)
        entrada.pop('_taller_id', None)
        entrada.pop('_tipo_trabajo', None)
    return indicadores, pendientes, entradas
