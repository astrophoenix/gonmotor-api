from datetime import time

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from apps.core.models import BaseModel


class Empresa(BaseModel):
    """
    Representa al Negocio / Razón Social principal (Cliente SaaS).
    """
    nombre_comercial = models.CharField(
        max_length=200, 
        verbose_name="Nombre Comercial"
    )
    razon_social = models.CharField(
        max_length=200, 
        blank=True, 
        default="", 
        verbose_name="Razón Social"
    )
    ruc = models.CharField(
        max_length=13, 
        unique=True, 
        verbose_name="RUC"
    )
    email_contacto = models.EmailField(
        verbose_name="Correo Electrónico de Contacto"
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default="", 
        verbose_name="Teléfono Principal"
    )
    logo = models.ImageField(
        upload_to='empresa_logos/',
        blank=True,
        null=True,
        verbose_name="Logo / Imagen de Empresa"
    )

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return f"{self.nombre_comercial} ({self.ruc})"


class Taller(BaseModel):
    """
    Representa un Taller físico de la Empresa.
    """
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='talleres',
        verbose_name="Empresa Perteneciente"
    )
    nombre = models.CharField(
        max_length=150, 
        verbose_name="Nombre del Taller"
    )
    codigo_sucursal = models.CharField(
        max_length=10, 
        blank=True, 
        default="001",
        verbose_name="Código de Taller (SRI / Contífico)"
    )
    ciudad = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Ciudad"
    )
    direccion = models.TextField(
        verbose_name="Dirección Físicas"
    )
    telefono = models.CharField(
        max_length=20, 
        blank=True, 
        default="", 
        verbose_name="Teléfono Taller"
    )

    # --- SECUENCIAS / NUMERACIÓN DE DOCUMENTOS ---
    # Cada taller mantiene prefijos, contadores y longitud de dígitos
    # independientes para Recepciones, Inspecciones, Cotizaciones y OTs.
    prefijo_recepcion = models.CharField(
        max_length=10, blank=True, default='REC-', verbose_name='Prefijo Recepciones'
    )
    siguiente_recepcion = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)], verbose_name='Siguiente número de Recepción'
    )
    digitos_recepcion = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(2), MaxValueValidator(10)], verbose_name='Dígitos de Recepción'
    )
    prefijo_inspeccion = models.CharField(
        max_length=10, blank=True, default='INS-', verbose_name='Prefijo Inspecciones'
    )
    siguiente_inspeccion = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)], verbose_name='Siguiente número de Inspección'
    )
    digitos_inspeccion = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(2), MaxValueValidator(10)], verbose_name='Dígitos de Inspección'
    )
    prefijo_cotizacion = models.CharField(
        max_length=10, blank=True, default='COT-', verbose_name='Prefijo Cotizaciones'
    )
    siguiente_cotizacion = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)], verbose_name='Siguiente número de Cotización'
    )
    digitos_cotizacion = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(2), MaxValueValidator(10)], verbose_name='Dígitos de Cotización'
    )
    prefijo_ot = models.CharField(
        max_length=10, blank=True, default='OT-', verbose_name='Prefijo Órdenes de Trabajo'
    )
    siguiente_ot = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)], verbose_name='Siguiente número de OT'
    )
    digitos_ot = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(2), MaxValueValidator(10)], verbose_name='Dígitos de OT'
    )

    # --- CAPACIDAD DE CITAS / HORARIO DE ATENCIÓN ---
    # Configurable por taller: cada sucursal decide cuántos vehículos puede
    # recibir y en qué franja horaria agenda citas.
    capacidad_citas_dia = models.PositiveIntegerField(
        default=0,
        verbose_name='Citas máximas por día',
        help_text='Máximo de citas que el taller puede recibir en un día. 0 = sin límite.',
    )
    capacidad_simultanea = models.PositiveIntegerField(
        default=0,
        verbose_name='Atenciones simultáneas máximas',
        help_text=(
            'Máximo de vehículos que pueden estar atendiéndose al mismo tiempo '
            '(citas en curso y vehículos ya ingresados al taller). 0 = sin límite.'
        ),
    )
    hora_apertura = models.TimeField(
        default=time(8, 0),
        verbose_name='Hora de apertura',
        help_text='Inicio del horario en el que se pueden agendar citas.',
    )
    hora_cierre = models.TimeField(
        default=time(17, 0),
        verbose_name='Hora de cierre',
        help_text='Fin del horario en el que se pueden agendar citas.',
    )

    class Meta:
        verbose_name = "Taller"
        verbose_name_plural = "Talleres"
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'codigo_sucursal'],
                condition=models.Q(is_active=True),
                name='una_sucursal_activa_por_empresa_codigo'
            ),
            models.CheckConstraint(
                condition=models.Q(hora_cierre__gt=models.F('hora_apertura')),
                name='taller_horario_atencion_valido',
            ),
        ]

    def __str__(self):
        return f"{self.empresa.nombre_comercial} - {self.nombre}"