"""Disponibilidad de agenda: horario de atención y capacidad del taller.

Cada sucursal (`empresas.Taller`) configura:

- `hora_apertura` / `hora_cierre`: franja en la que se pueden agendar citas.
- `capacidad_citas_dia`: máximo de citas vigentes en un día (0 = sin límite).
- `capacidad_simultanea`: máximo de vehículos atendiéndose a la vez (0 = sin
  límite).

La capacidad simultánea considera las citas **y** los vehículos que ya están
físicamente en el taller (recepciones sin fecha de salida), de modo que el cupo
refleja la ocupación real de las bahías y no solo el número de filas de agenda.

La verificación se hace siempre sobre la fila del taller bloqueada con
`select_for_update()` dentro de una transacción (ver `bloquear_taller`), para
que dos creaciones simultáneas no puedan colarse en el último cupo.
"""

from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.empresas.models import Taller
from apps.ordenes.models import RecepcionVehiculo

from .models import Cita

# Estados en los que la cita sigue consumiendo cupo del día.
ESTADOS_VIGENTES = (
    Cita.EstadoCita.PROGRAMADA,
    Cita.EstadoCita.CONFIRMADA,
    Cita.EstadoCita.EN_PROGRESO,
    Cita.EstadoCita.COMPLETADA,
)

# Estados que ocupan agenda por sí mismos (sin recepción asociada).
ESTADOS_OCUPAN_AGENDA = (
    Cita.EstadoCita.PROGRAMADA,
    Cita.EstadoCita.CONFIRMADA,
    Cita.EstadoCita.EN_PROGRESO,
)

ESTADO_RECHAZADA = 'NO_ACEPTADA'


class ErrorAgenda(Exception):
    """Error de agenda entendible por el usuario (horario o cupo)."""

    campo = 'hora_cita'

    def __init__(self, mensaje, campo=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        if campo:
            self.campo = campo

    def como_error(self):
        return {self.campo: self.mensaje}


def formato_hora(valor):
    """'08:00:00' -> '08:00'."""
    if not valor:
        return ''
    texto = str(valor)
    return texto[:5] if len(texto) >= 5 else texto


def formato_fecha(fecha):
    return fecha.strftime('%d/%m/%Y') if fecha else ''


def resolver_taller_de_cita(empresa_id, taller=None):
    """Taller sobre el que se evalúa la agenda de la cita.

    Si la cita no trae sucursal explícita se usa el taller por defecto de la
    empresa (mismo criterio que `Cita.convertir_a_recepcion`).
    """
    if taller is not None:
        return taller
    if not empresa_id:
        return None
    from apps.empresas.services import resolver_taller

    return resolver_taller(empresa_id, None)


def intervalo_de(fecha, hora, duracion_minutos):
    """Intervalo `[inicio, fin)` de una cita como datetimes conscientes."""
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(fecha, hora), tz)
    duracion = max(int(duracion_minutos or 0), 1)
    return inicio, inicio + timedelta(minutes=duracion)


def validar_horario_atencion(taller, fecha, hora, duracion_minutos):
    """Valida que la cita caiga dentro del horario de atención del taller."""
    if taller is None or taller.hora_apertura is None or taller.hora_cierre is None:
        return

    apertura = formato_hora(taller.hora_apertura)
    cierre = formato_hora(taller.hora_cierre)

    if hora < taller.hora_apertura:
        raise ErrorAgenda(
            f'El taller abre a las {apertura}. La cita debe comenzar a partir de esa hora.'
        )

    _, fin = intervalo_de(fecha, hora, duracion_minutos)
    tz = timezone.get_current_timezone()
    fin_permitido = timezone.make_aware(datetime.combine(fecha, taller.hora_cierre), tz)
    if fin > fin_permitido:
        raise ErrorAgenda(
            f'El taller cierra a las {cierre}. Con la duración indicada la cita '
            f'terminaría después de la hora de cierre.'
        )


def bloquear_taller(taller):
    """Bloquea la fila del taller dentro de la transacción en curso.

    Todas las escrituras que validan cupo deben pasar por aquí: al serializar el
    acceso a la fila, el recuento de ocupación y la inserción no pueden
    entremezclarse con otra creación simultánea.
    """
    if taller is None or taller.pk is None:
        return None
    return Taller.objects.select_for_update().get(pk=taller.pk)


def _citas_del_dia(taller, fecha, cita_excluida=None):
    qs = (
        Cita.objects.filter(taller=taller, fecha_cita=fecha, is_active=True)
        .select_related('recepcion_generada', 'vehiculo')
    )
    if cita_excluida:
        qs = qs.exclude(pk=cita_excluida)
    return qs


def _recepciones_del_taller(taller, inicio_dia, fin_dia):
    """Vehículos físicamente presentes en el taller durante `fecha`.

    Incluye los que entraron días anteriores y aún no salieron. Excluye las
    rechazadas y las que salieron antes de que empiece el día.
    """
    return (
        RecepcionVehiculo.objects.filter(sucursal=taller, is_active=True, fecha_ingreso__lte=fin_dia)
        .filter(Q(fecha_salida__isnull=True) | Q(fecha_salida__gt=inicio_dia))
        .exclude(estado=ESTADO_RECHAZADA)
        .select_related('vehiculo')
    )


def ocupacion_del_dia(taller, fecha, cita_excluida=None):
    """Ocupación real del taller en `fecha`.

    Devuelve:
    - `citas_dia`: citas vigentes del día (cupos de agenda consumidos).
    - `intervalos`: franjas ocupadas por citas aún no convertidas y por
      vehículos ingresados, con su origen.
    """
    tz = timezone.get_current_timezone()
    inicio_dia = timezone.make_aware(datetime.combine(fecha, time.min), tz)
    fin_dia = timezone.make_aware(datetime.combine(fecha, time.max), tz)

    citas = list(_citas_del_dia(taller, fecha, cita_excluida))
    citas_dia = sum(1 for c in citas if c.estado in ESTADOS_VIGENTES)

    intervalos = []
    for cita in citas:
        if cita.estado == Cita.EstadoCita.COMPLETADA and cita.recepcion_generada_id:
            continue  # su vehículo ya está contado vía recepción
        inicio, fin = intervalo_de(cita.fecha_cita, cita.hora_cita, cita.duracion_minutos)
        intervalos.append({
            'inicio': inicio,
            'fin': fin,
            'origen': 'cita',
            'referencia': cita.id,
            'detalle': cita.vehiculo.placa if cita.vehiculo_id else '',
        })

    for recepcion in _recepciones_del_taller(taller, inicio_dia, fin_dia):
        inicio = max(recepcion.fecha_ingreso, inicio_dia)
        fin = min(recepcion.fecha_salida or fin_dia, fin_dia)
        if fin <= inicio:
            continue
        intervalos.append({
            'inicio': inicio,
            'fin': fin,
            'origen': 'recepcion',
            'referencia': recepcion.id,
            'detalle': recepcion.vehiculo.placa if recepcion.vehiculo_id else '',
        })

    return {'citas_dia': citas_dia, 'intervalos': intervalos}


def concurrencia_maxima(intervalos, inicio, fin):
    """Máximo de franjas ocupadas al mismo tiempo dentro de `[inicio, fin)`."""
    eventos = []
    for intervalo in intervalos:
        desde = max(intervalo['inicio'], inicio)
        hasta = min(intervalo['fin'], fin)
        if desde < hasta:
            eventos.append((desde, 1))
            eventos.append((hasta, -1))
    eventos.sort(key=lambda evento: (evento[0], evento[1]))

    actual = maximo = 0
    for _, delta in eventos:
        actual += delta
        if actual > maximo:
            maximo = actual
    return maximo


def verificar_disponibilidad(taller, fecha, hora, duracion_minutos, cita_excluida=None):
    """Valida horario y capacidad. Lanza `ErrorAgenda` si no hay cupo.

    `taller=None` (empresa sin sucursales configuradas) no valida nada.
    """
    if taller is None:
        return None

    validar_horario_atencion(taller, fecha, hora, duracion_minutos)
    ocupacion = ocupacion_del_dia(taller, fecha, cita_excluida)

    if taller.capacidad_citas_dia and ocupacion['citas_dia'] >= taller.capacidad_citas_dia:
        raise ErrorAgenda(
            f'El taller no tiene cupo disponible para el {formato_fecha(fecha)}: '
            f'ya se ocuparon {ocupacion["citas_dia"]} de '
            f'{taller.capacidad_citas_dia} citas.',
            campo='fecha_cita',
        )

    if taller.capacidad_simultanea:
        inicio, fin = intervalo_de(fecha, hora, duracion_minutos)
        ocupados = concurrencia_maxima(ocupacion['intervalos'], inicio, fin)
        if ocupados + 1 > taller.capacidad_simultanea:
            raise ErrorAgenda(
                f'En ese horario ya hay {ocupados} vehículo'
                f'{"s" if ocupados != 1 else ""} atendiéndose '
                f'(máximo permitido: {taller.capacidad_simultanea}). '
                'Elige otra hora.',
            )

    return ocupacion


def disponibilidad(taller, fecha, cita_excluida=None, hora=None, duracion_minutos=None):
    """Payload de `GET /api/citas/disponibilidad/`."""
    ocupacion = ocupacion_del_dia(taller, fecha, cita_excluida) if taller else {
        'citas_dia': 0,
        'intervalos': [],
    }
    inicio_dia = timezone.make_aware(datetime.combine(fecha, time.min))
    fin_dia = timezone.make_aware(datetime.combine(fecha, time.max))
    pico = concurrencia_maxima(ocupacion['intervalos'], inicio_dia, fin_dia)

    intervalos = sorted(
        (
            {
                'inicio': item['inicio'].strftime('%H:%M'),
                'fin': item['fin'].strftime('%H:%M'),
                'origen': item['origen'],
                'referencia': item['referencia'],
                'detalle': item['detalle'],
            }
            for item in ocupacion['intervalos']
        ),
        key=lambda item: item['inicio'],
    )

    payload = {
        'fecha': fecha.isoformat(),
        'aplicable': taller is not None,
        'taller': (
            {'id': taller.pk, 'nombre': taller.nombre} if taller else None
        ),
        'horario': (
            {
                'apertura': formato_hora(taller.hora_apertura),
                'cierre': formato_hora(taller.hora_cierre),
            }
            if taller else None
        ),
        'capacidad_dia': taller.capacidad_citas_dia if taller else 0,
        'citas_ocupadas': ocupacion['citas_dia'],
        'citas_disponibles': (
            max(taller.capacidad_citas_dia - ocupacion['citas_dia'], 0)
            if taller and taller.capacidad_citas_dia else None
        ),
        'capacidad_simultanea': taller.capacidad_simultanea if taller else 0,
        'concurrentes_max': pico,
        'intervalos': intervalos,
        'solicitud': None,
    }

    if taller and hora is not None:
        duracion = int(duracion_minutos or 60)
        solicitud = {'hora': formato_hora(hora), 'duracion_minutos': duracion}
        try:
            verificar_disponibilidad(taller, fecha, hora, duracion, cita_excluida)
            solicitud.update({'disponible': True, 'campo': None, 'motivo': None})
        except ErrorAgenda as exc:
            solicitud.update({'disponible': False, 'campo': exc.campo, 'motivo': exc.mensaje})
        payload['solicitud'] = solicitud

    return payload
