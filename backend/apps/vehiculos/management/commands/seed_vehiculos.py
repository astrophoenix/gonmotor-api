from django.core.management.base import BaseCommand
from apps.vehiculos.models import Vehiculo, VehiculoPropietario
from apps.empresas.models import Empresa
from apps.clientes.models import Cliente


class Command(BaseCommand):
    help = "Crea vehículos de prueba y los asigna a clientes según requerimientos específicos"

    def handle(self, *args, **options):
        self.stdout.write("Iniciando seed de vehículos...")

        casos = [
            {
                "cliente_id": 79,
                "empresa_id": 2,
                "cantidad": 2,
                "vehiculos": [
                    {
                        "placa": "PBA1234",
                        "marca": "Toyota",
                        "modelo": "Corolla",
                        "anio": 2020,
                        "color": "Blanco",
                        "transmision": "A",
                        "combustible": "GAS",
                        "kilometraje_actual": 25000,
                        "observaciones": "Vehículo de Carmen Soto",
                    },
                    {
                        "placa": "PBA5678",
                        "marca": "Toyota",
                        "modelo": "Corolla",
                        "anio": 2020,
                        "color": "Blanco",
                        "transmision": "A",
                        "combustible": "GAS",
                        "kilometraje_actual": 25000,
                        "observaciones": "Vehículo de Carmen Soto",
                    },
                ],
            },
            {
                "cliente_id": 58,
                "empresa_id": 3,
                "cantidad": 2,
                "vehiculos": [
                    {
                        "placa": "GYE9012",
                        "marca": "Honda",
                        "modelo": "Civic",
                        "anio": 2021,
                        "color": "Negro",
                        "transmision": "M",
                        "combustible": "GAS",
                        "kilometraje_actual": 18000,
                        "observaciones": "Vehículo de Víctor Luna",
                    },
                    {
                        "placa": "GYE3456",
                        "marca": "Honda",
                        "modelo": "Civic",
                        "anio": 2021,
                        "color": "Negro",
                        "transmision": "M",
                        "combustible": "GAS",
                        "kilometraje_actual": 18000,
                        "observaciones": "Vehículo de Víctor Luna",
                    },
                ],
            },
            {
                "cliente_id": 47,
                "empresa_id": 1,
                "cantidad": 1,
                "vehiculos": [
                    {
                        "placa": "UIO1122",
                        "marca": "Nissan",
                        "modelo": "Sentra",
                        "anio": 2019,
                        "color": "Gris",
                        "transmision": "A",
                        "combustible": "GAS",
                        "kilometraje_actual": 35000,
                        "observaciones": "Vehículo de cliente 47",
                    },
                ],
            },
        ]

        creados = 0

        for caso in casos:
            cliente_id = caso["cliente_id"]
            empresa_id = caso["empresa_id"]
            vehiculos_data = caso["vehiculos"]

            try:
                cliente = Cliente.objects.get(id=cliente_id)
            except Cliente.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Cliente con id={cliente_id} no existe. Saltando caso."))
                continue

            try:
                empresa = Empresa.objects.get(id=empresa_id)
            except Empresa.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Empresa con id={empresa_id} no existe. Saltando caso."))
                continue

            self.stdout.write(f"Cliente: {cliente.nombre} | Empresa: {empresa.nombre_comercial}")

            for v_data in vehiculos_data:
                placa = v_data["placa"].strip().upper()
                if Vehiculo.objects.filter(placa=placa).exists():
                    self.stdout.write(self.style.WARNING(f"  Vehículo con placa {placa} ya existe. Saltando."))
                    continue

                vehiculo = Vehiculo.objects.create(
                    placa=placa,
                    marca=v_data["marca"],
                    modelo=v_data["modelo"],
                    anio=v_data.get("anio"),
                    color=v_data.get("color", ""),
                    transmision=v_data.get("transmision", "M"),
                    combustible=v_data.get("combustible", "GAS"),
                    kilometraje_actual=v_data.get("kilometraje_actual", 0),
                    observaciones=v_data.get("observaciones", ""),
                )
                vehiculo.empresas.add(empresa)

                VehiculoPropietario.objects.create(
                    vehiculo=vehiculo,
                    cliente=cliente,
                    es_actual=True,
                )

                self.stdout.write(self.style.SUCCESS(f"  Creado: {vehiculo.placa} - {vehiculo.marca} {vehiculo.modelo}"))
                creados += 1

        self.stdout.write(self.style.SUCCESS(f"Proceso finalizado. Vehículos creados: {creados}"))
