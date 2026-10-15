"""
Lookup ``sintilde_icontains``: contiene, sin distinguir mayúsculas ni tildes
("perez" encuentra "Pérez", "nunez" encuentra "Núñez").

Usa ``f_unaccent`` (migración ``core.0007``), un envoltorio IMMUTABLE de
``unaccent``: la función de la extensión es STABLE y Postgres no permite
indexarla. Los índices trigram de búsqueda están definidos sobre la misma
expresión, ``upper(f_unaccent(col))``, así que este lookup los aprovecha.
"""
from django.db.models import CharField, TextField
from django.db.models.lookups import IContains


class SinTildeIContains(IContains):
    lookup_name = 'sintilde_icontains'

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        # `process_rhs` de IContains ya envuelve el término en `%...%` y escapa `%`/`_`.
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return (
            f'UPPER(f_unaccent({lhs}::text)) LIKE UPPER(f_unaccent({rhs}))',
            (*lhs_params, *rhs_params),
        )


def registrar():
    CharField.register_lookup(SinTildeIContains)
    TextField.register_lookup(SinTildeIContains)
