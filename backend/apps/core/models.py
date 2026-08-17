# apps/core/models.py
from django.db import models

class BaseModel(models.Model):
    """
    Modelo base abstracto que provee auditoría y estado para todas las tablas.
    """
    is_active = models.BooleanField(
        default=True, 
        verbose_name="Activo",
        help_text="Indica si el registro está activo o fue deshabilitado/eliminado lógicamente"
    )
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Fecha de creación"
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name="Fecha de última modificación"
    )

    class Meta:
        abstract = True