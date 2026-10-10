from django.core.management.base import BaseCommand

from apps.authentication.permisos_repo import sembrar_permisos_si_vacio


class Command(BaseCommand):
    help = 'Siembra la matriz de permisos por rol desde la línea base declarativa.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Borra los permisos existentes y vuelve a la línea base declarativa.',
        )

    def handle(self, *args, **options):
        sembrar_permisos_si_vacio(forzar=options['force'])
        if options['force']:
            self.stdout.write(self.style.SUCCESS('Permisos re-sembrados desde la matriz.'))
        else:
            self.stdout.write(self.style.SUCCESS('Permisos sembrados (tabla vacía o sin cambios).'))