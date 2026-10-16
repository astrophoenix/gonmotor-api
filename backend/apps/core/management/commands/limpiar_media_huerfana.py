"""Lista (o borra) los archivos de MEDIA_ROOT que ningún registro referencia.

Quedan huérfanos los archivos de antes de `apps/core/archivos.py` (fotos
reemplazadas o borradas) y los de subidas cuya transacción se revirtió.

Uso:

    # Vista previa: solo muestra lo que borraría
    python manage.py limpiar_media_huerfana

    # Borrar de verdad
    python manage.py limpiar_media_huerfana --aplicar

Los registros con borrado lógico conservan sus archivos (se pueden restaurar).
Los archivos modificados en la última hora se ignoran: pueden pertenecer a una
subida que aún no confirmó su transacción.
"""
import time
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.archivos import campos_archivo

MARGEN_SEGUNDOS = 3600


def archivos_referenciados():
    nombres = set()
    for modelo in apps.get_models():
        campos = campos_archivo(modelo)
        if not campos:
            continue
        # `_base_manager` incluye los registros con borrado lógico.
        for fila in modelo._base_manager.values_list(*(c.attname for c in campos)):
            nombres.update(nombre for nombre in fila if nombre)
    return nombres


def _tamano(n):
    return f'{n / (1024 * 1024):.1f} MB'


class Command(BaseCommand):
    help = 'Lista o borra los archivos subidos que ningún registro referencia.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Borra los archivos (por defecto solo los lista).')

    def handle(self, *args, aplicar=False, **options):
        raiz = Path(settings.MEDIA_ROOT)
        if not raiz.is_dir():
            self.stdout.write('MEDIA_ROOT no existe; nada que revisar.')
            return

        referenciados = archivos_referenciados()
        limite = time.time() - MARGEN_SEGUNDOS
        huerfanos = [
            ruta for ruta in sorted(raiz.rglob('*'))
            if ruta.is_file()
            and ruta.relative_to(raiz).as_posix() not in referenciados
            and ruta.stat().st_mtime < limite
        ]
        total = sum(ruta.stat().st_size for ruta in huerfanos)

        for ruta in huerfanos:
            self.stdout.write(f'  {ruta.relative_to(raiz).as_posix()}')
            if aplicar:
                ruta.unlink()

        verbo = 'Borrados' if aplicar else 'Se borrarían'
        self.stdout.write(self.style.SUCCESS(f'{verbo} {len(huerfanos)} archivos ({_tamano(total)}).'))
        if huerfanos and not aplicar:
            self.stdout.write('Ejecuta con --aplicar para borrarlos.')
