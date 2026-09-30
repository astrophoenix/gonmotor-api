"""Exportación de citas a iCalendar (.ics) para el calendario del cliente.

Dos vías con el mismo contenido:

- `GET /api/citas/<id>/ics/`  → autenticada (uso interno / integraciones).
- `GET /api/citas/compartir/<token>/` → pública, mediante un enlace firmado
  con `django.core.signing`, pensado para compartir con el cliente.

Las horas se emiten en tiempo local flotante (sin `Z` ni `TZID`): `fecha_cita`
y `hora_cita` son la hora de pared que se muestra en la interfaz, de modo que
un cliente en la zona del taller ve exactamente la hora acordada.
"""

from datetime import datetime, timedelta

from django.core import signing
from django.http import HttpResponse
from django.utils import timezone

SALT_ENLACE_CITA_ICS = 'cita-ics'

CRLF = '\r\n'

ESTADO_CANCELADO = 'CANCELADA'


def token_enlace_ics(cita_id):
    """Token opaco y no manipulable que da acceso a la descarga de la cita."""
    return signing.dumps(cita_id, salt=SALT_ENLACE_CITA_ICS)


def escapar_texto(valor):
    """Escapa los caracteres reservados de un valor de texto (RFC 5545)."""
    if valor is None:
        return ''
    texto = str(valor)
    for original, reemplazo in (
        ('\\', '\\\\'),
        (';', '\\;'),
        (',', '\\,'),
        ('\r\n', '\\n'),
        ('\n', '\\n'),
        ('\r', '\\n'),
    ):
        texto = texto.replace(original, reemplazo)
    return texto


def doblar_linea(linea):
    """Aplica el plegado de líneas del RFC 5545 (máximo 75 octetos)."""
    restantes = linea.encode('utf-8')
    if len(restantes) <= 75:
        return linea

    partes = []
    tope = 75
    while restantes:
        bloque = restantes[:tope]
        if len(restantes) > len(bloque):
            # Nunca partir un carácter UTF-8 por la mitad.
            while bloque and 0x80 <= bloque[-1] <= 0xBF:
                bloque = bloque[:-1]
        if not bloque:
            bloque = restantes[:tope]
        partes.append(bloque.decode('utf-8'))
        restantes = restantes[len(bloque):]
        tope = 74  # el espacio de continuación ocupa un octeto
    return (CRLF + ' ').join(partes)


def _campo(nombre, valor):
    return doblar_linea(f'{nombre}:{valor}')


def _inicio(cita):
    return datetime.combine(cita.fecha_cita, cita.hora_cita)


def _fin(cita):
    return _inicio(cita) + timedelta(minutes=cita.duracion_minutos or 60)


def resumen_cita(cita):
    taller = cita.taller.nombre if cita.taller_id else 'taller'
    placa = cita.vehiculo.placa if cita.vehiculo_id else ''
    return f'Cita en {taller} - {placa}' if placa else f'Cita en {taller}'


def descripcion_cita(cita):
    lineas = []
    if cita.taller_id:
        lineas.append(f'Taller: {cita.taller.nombre}')
    if cita.cliente_id:
        lineas.append(f'Cliente: {cita.cliente.nombre}')
    if cita.vehiculo_id:
        vehiculo = cita.vehiculo
        lineas.append(f'Vehículo: {vehiculo.marca} {vehiculo.modelo} ({vehiculo.placa})')
    lineas.append(f'Motivo: {cita.get_motivo_display()}')
    if cita.motivo_descripcion:
        lineas.append(cita.motivo_descripcion)
    return '\n'.join(lineas)


def ubicacion_cita(cita):
    if not cita.taller_id:
        return ''
    taller = cita.taller
    return ', '.join(parte for parte in (taller.direccion, taller.nombre) if parte)


def construir_ics(cita):
    """Devuelve el contenido completo del archivo .ics de la cita."""
    cancelada = cita.estado == ESTADO_CANCELADO
    campos = [
        ('UID', f'cita-{cita.pk}@gonmotor'),
        ('DTSTAMP', timezone.now().strftime('%Y%m%dT%H%M%SZ')),
        ('DTSTART', _inicio(cita).strftime('%Y%m%dT%H%M%S')),
        ('DTEND', _fin(cita).strftime('%Y%m%dT%H%M%S')),
        ('SUMMARY', escapar_texto(resumen_cita(cita))),
        ('DESCRIPTION', escapar_texto(descripcion_cita(cita))),
        ('STATUS', 'CANCELLED' if cancelada else 'CONFIRMED'),
        ('TRANSP', 'TRANSPARENT' if cancelada else 'OPAQUE'),
        ('SEQUENCE', '0'),
        ('CATEGORIES', escapar_texto(cita.get_motivo_display())),
    ]

    ubicacion = ubicacion_cita(cita)
    if ubicacion:
        campos.append(('LOCATION', escapar_texto(ubicacion)))

    lineas = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//gonmotor//Citas//ES',
              'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'BEGIN:VEVENT']
    lineas.extend(_campo(nombre, valor) for nombre, valor in campos)

    if not cancelada:
        lineas.extend([
            'BEGIN:VALARM',
            _campo('TRIGGER', '-PT1H'),
            'ACTION:DISPLAY',
            _campo('DESCRIPTION', escapar_texto(f'Recordatorio: {resumen_cita(cita)}')),
            'END:VALARM',
        ])

    lineas.extend(['END:VEVENT', 'END:VCALENDAR'])
    return CRLF.join(lineas) + CRLF


def nombre_archivo_ics(cita):
    placa = cita.vehiculo.placa if cita.vehiculo_id else 'cita'
    return f'cita-{cita.fecha_cita.isoformat()}-{placa}.ics'


def respuesta_ics(cita):
    """Respuesta HTTP descargable con el .ics de la cita."""
    respuesta = HttpResponse(
        construir_ics(cita),
        content_type='text/calendar; charset=utf-8',
    )
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre_archivo_ics(cita)}"'
    respuesta['Cache-Control'] = 'no-store'
    return respuesta
