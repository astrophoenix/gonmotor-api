"""Envía las notificaciones programadas cuya fecha ya llegó.

Uso:

    python manage.py procesar_notificaciones
    python manage.py procesar_notificaciones --limite 20

Debe correr con frecuencia (cada 15 minutos) para que los recordatorios de
cita de 2 h salgan a tiempo. En Render (plan gratuito, sin cron jobs) lo
dispara un cron externo con `POST /api/notificaciones/cron/procesar/`; ver
`.github/workflows/procesar-notificaciones.yml`.
"""

from django.core.management.base import BaseCommand

from apps.notificaciones.services.cola import LIMITE_POR_EJECUCION, procesar_pendientes


class Command(BaseCommand):
    help = 'Envía las notificaciones programadas pendientes (WhatsApp o correo).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limite',
            type=int,
            default=LIMITE_POR_EJECUCION,
            help=f'Máximo de notificaciones por ejecución (por defecto {LIMITE_POR_EJECUCION}).',
        )

    def handle(self, *args, **options):
        resumen = procesar_pendientes(limite=options['limite'])
        self.stdout.write(self.style.SUCCESS(
            'Notificaciones procesadas={procesadas} enviadas={enviadas} canceladas={canceladas} '
            'reprogramadas={reprogramadas} errores={errores}'.format(**resumen)
        ))
