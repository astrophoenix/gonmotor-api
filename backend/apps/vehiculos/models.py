from django.db import models
from apps.core.models import BaseModel


class Vehiculo(BaseModel):

    #🚗 Opciones del tipo de transmisión con TextChoices
    class TipoTransmision(models.TextChoices):
        MANUAL = 'M', 'Manual / Mecánica'
        AUTOMATICA = 'A', 'Automática'
        CVT = 'C', 'CVT'

    # ⛽ Opciones del tipo de combustible con TextChoices
    class TipoCombustible(models.TextChoices):
        GASOLINA = 'GAS', 'Gasolina'
        DIESEL = 'DIE', 'Diésel'
        HIBRIDO = 'HIB', 'Híbrido'
        ELECTRICO = 'ELE', 'Eléctrico'

    # Relación con el Cliente usando la cadena 'clientes.Cliente'
    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.CASCADE,
        related_name='vehiculos',
        verbose_name="Propietario / Cliente"
    )

    placa = models.CharField(
        verbose_name="Placa",
        max_length=10,
        unique=True,
        help_text="Ejemplo: PBA1234 (Sin guiones)"
    )
    vin = models.CharField(
        verbose_name="Número de Chasis / VIN",
        max_length=17,
        blank=True,
        default=""
    )
    numero_motor = models.CharField(
        verbose_name="Número de Motor",
        max_length=50,
        blank=True,
        default=""
    )
    marca = models.CharField(
        verbose_name="Marca",
        max_length=50
    )
    modelo = models.CharField(
        verbose_name="Modelo",
        max_length=50
    )
    anio = models.PositiveIntegerField(
        verbose_name="Año de Fabricación",
        blank=True,
        null=True
    )
    color = models.CharField(
        verbose_name="Color",
        max_length=30,
        blank=True,
        default=""
    )
    transmision = models.CharField(
        max_length=1,
        choices=TipoTransmision.choices,
        default=TipoTransmision.MANUAL
    )
    combustible = models.CharField(
        max_length=3,
        choices=TipoCombustible.choices,
        default=TipoCombustible.GASOLINA
    )
    kilometraje_actual = models.PositiveIntegerField(
        verbose_name="Kilometraje Actual",
        default=0
    )
    observaciones = models.TextField(
        verbose_name="Observaciones",
        blank=True,
        default=""
    )

    class Meta:
        verbose_name = "Vehículo"
        verbose_name_plural = "Vehículos"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"

    def save(self, *args, **kwargs):
        if self.placa:
            self.placa = self.placa.strip().upper()
        super().save(*args, **kwargs)