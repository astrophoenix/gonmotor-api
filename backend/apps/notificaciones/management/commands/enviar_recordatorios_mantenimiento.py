"""Comando para enviar recordatorios de mantenimiento por WhatsApp.

Uso:

    # Modo "vista previa" (no envía nada)
    python manage.py enviar_recordatorios_mantenimiento --preview

    # Enviar solo para una empresa
    python manage.py enviar_recordatorios_mantenimiento --empresa 1

    # Enviar para todas las empresas
    python manage.py enviar_recordatorios_mantenimiento

Sugerencia de automatización gratuita (sin celery): programar con cron o con
un servicio como Render Cron/Heroku Scheduler una vez al día:

    30 9 * * * cd /ruta/backend && venv/bin/python manage.py enviar_recordatorios_mantenimiento --empresa 1

Credenciales: ver apps/notificaciones/services/whatsapp.py (modo `mock` no
requiere ninguna; modo `twilio_sandbox` o `meta_api` usan variables del .env).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.empresas.models import Empresa
from apps.notificaciones.services import recordatorios
from apps.notificaciones.services.whatsapp import resumen_configuracion


class Command(BaseCommand):
    help = 'Envía recordatorios de mantenimiento por WhatsApp (o los simula en modo mock).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--empresa',
            type=int,
            default=None,
            help='ID de la empresa a procesar (por defecto: todas).',
        )
        parser.add_argument(
            '--preview',
            action='store_true',
            help='Solo muestra los vehículos que recibirían el recordatorio (no envía).',
        )

    def handle(self, *args, **options):
        preview = options['preview']
        info = resumen_configuracion()
        self.stdout.write(self.style.WARNING(
            f'Proveedor WhatsApp: {info["modo"]} '
            f'({"configurado" if info["configurado"] else "SIN configurar, usando mock"})'
        ))

        empresas = Empresa.objects.filter(is_active=True)
        if options['empresa']:
            empresas = empresas.filter(pk=options['empresa'])
            if not empresas.exists():
                raise CommandError(f'No existe la empresa {options["empresa"]}.')

        for empresa in empresas:
            preferencia = recordatorios.get_preferencia(empresa)
            resultado = recordatorios.ejecutar_para_empresa(
                empresa=empresa,
                preferencia=preferencia,
                preview=preview,
            )
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n[{empresa.nombre_comercial}] '
                f'pendientes={resultado["pendientes"]} '
                f'enviados={resultado["enviados"]} '
                f'errores={resultado["errores"]} '
                f'preview={resultado.get("preview", False)}'
            ))
            if preview:
                for cand in resultado.get('candidatos', []):
                    self.stdout.write(f"  - {cand['placa']} · {cand['vehiculo']} · "
                                      f"{cand['cliente']} ({cand['celular']}) · {cand['motivo']}")
            for error in resultado.get('errores_detalle', []):
                self.stderr.write(self.style.ERROR(
                    f"  [ERROR] {error['placa']}: {error['error']}"
                ))

        self.stdout.write(self.style.SUCCESS('\nProceso de recordatorios finalizado.'))