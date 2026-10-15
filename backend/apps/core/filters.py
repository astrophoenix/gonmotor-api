"""
Búsqueda de listados apta para volumen alto.

``SearchFilter`` de DRF busca en campos relacionados con JOIN + ``OR``:
``... JOIN cliente JOIN vehiculo WHERE numero ILIKE x OR cliente.nombre ILIKE x``.
Postgres no puede usar índices en un ``OR`` que mezcla tablas, así que une
todas las filas del tenant y descarta casi todas (lineal con el volumen). Con
relaciones a muchos, además, duplica filas y obliga a ``DISTINCT``.

``BusquedaIndexadaFilter`` mantiene la misma interfaz (``search_fields``,
términos separados por espacios con ``AND``, campos con ``OR``) pero convierte
cada término en una búsqueda por clave primaria sobre la UNIÓN de ramas, una
por campo:

    id = ANY(ARRAY(
        SELECT id FROM ot WHERE empresa_id = E AND numero ILIKE x
        UNION SELECT id FROM ot WHERE empresa_id = E AND cliente_id IN (
            SELECT id FROM cliente WHERE empresa_id = E AND nombre ILIKE x)
        UNION ...
    ))

Cada rama se resuelve por índice (trigram en la columna buscada, B-tree en la
FK). ``ANY(ARRAY(...))`` hace que Postgres calcule los ids una sola vez
(InitPlan) y los busque por PK antes de unir las relaciones del listado. Las
variantes que se descartaron, medidas con ``apps/core/bench_listados.py``:

* ``OR`` de ``IN``: Postgres no lo convierte en semi-join y evalúa cada fila
  del tenant contra cada subconsulta.
* ``id IN (UNION ...)``: con ``ORDER BY ... LIMIT`` el planificador recorre el
  índice por fecha uniendo todas las relaciones antes de cruzar los ids.

Tampoco hacen falta JOIN ni ``DISTINCT``. Solo Postgres (``ANY``/``ARRAY``).
"""
import operator
from functools import reduce

from django.db.models import F, Lookup, Q
from django.db.models.constants import LOOKUP_SEP
from rest_framework import filters

from apps.authentication.utils import get_empresa_id_desde_request


class EnArray(Lookup):
    """``campo = ANY(ARRAY(subconsulta))``: la subconsulta se evalúa una vez."""

    lookup_name = 'en_array'

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f'{lhs} = ANY(ARRAY{rhs})', (*lhs_params, *rhs_params)


class BusquedaIndexadaFilter(filters.SearchFilter):
    # Sin distinguir tildes ("perez" encuentra "Pérez"); ver apps/core/lookups.py.
    default_lookup = 'sintilde_icontains'
    # Longitud mínima para que un índice trigram sirva (un trigrama = 3 caracteres).
    MIN_TRIGRAM = 3

    def filter_queryset(self, request, queryset, view):
        search_fields = self.get_search_fields(view, request)
        terminos = self.get_search_terms(request)
        if not search_fields or not terminos:
            return queryset

        empresa_id = get_empresa_id_desde_request(request)
        modelo = queryset.model
        # Sin `order_by()` la UNIÓN arrastra el `ordering` del modelo, que no
        # existe en el resultado (`__in` lo limpiaba solo; `EnArray` no).
        base = self._manager_tenant(modelo, empresa_id).order_by()
        hay_indexable = any(len(t) >= self.MIN_TRIGRAM for t in terminos)
        for termino in terminos:
            condiciones = (
                [self._condicion(modelo, str(campo), termino, empresa_id) for campo in search_fields]
                + self.condiciones_extra(termino)
            )
            if hay_indexable and len(termino) < self.MIN_TRIGRAM:
                # Un término corto ("77") no usa trigram: su UNIÓN recorrería
                # todo el tenant. Se aplica como filtro sobre las filas que ya
                # acotan los demás términos.
                queryset = queryset.filter(reduce(operator.or_, condiciones))
                continue
            ramas = [base.filter(condicion).values('pk') for condicion in condiciones]
            # Unión plana: encadenar `.union()` reactiva el ordering en la intermedia.
            ids = ramas[0].union(*ramas[1:]).order_by()
            queryset = queryset.filter(EnArray(F('pk'), ids))
        return queryset

    def condiciones_extra(self, termino):
        """Gancho para subclases: condiciones ``Q`` adicionales por término (``OR``)."""
        return []

    def _lookup(self, termino):
        """
        Sin tildes para términos indexables. Un término corto (< 3 caracteres)
        no usa trigram y se evalúa fila por fila: con `icontains` simple se
        evita llamar a `unaccent` en cada fila (en esos términos casi nunca
        hay tildes).
        """
        return self.default_lookup if len(termino) >= self.MIN_TRIGRAM else 'icontains'

    def _condicion(self, modelo, campo, termino, empresa_id):
        if campo[0] in self.lookup_prefixes:
            # Prefijos de DRF (^ = @ $): lookup estándar, sin reescritura.
            lookup = self.construct_search(campo, modelo._base_manager.none())
            return Q(**{lookup: termino})
        return self._q_ruta(modelo, campo.split(LOOKUP_SEP), termino, empresa_id)

    def _q_ruta(self, modelo, partes, termino, empresa_id):
        nombre, resto = partes[0], partes[1:]
        if not resto:
            return Q(**{f'{nombre}__{self._lookup(termino)}': termino})

        campo = modelo._meta.get_field(nombre)
        if not campo.is_relation:
            # Transform sobre un campo propio (p. ej. `fecha__year`): lookup directo.
            return Q(**{f'{LOOKUP_SEP.join(partes)}__{self._lookup(termino)}': termino})

        relacionado = campo.related_model
        interior = self._q_ruta(relacionado, resto, termino, empresa_id)

        if campo.many_to_one or (campo.one_to_one and campo.concrete):
            # FK / OneToOne directa: `fk_id IN (SELECT id FROM relacionado WHERE ...)`.
            subconsulta = self._manager_tenant(relacionado, empresa_id).filter(interior).values('pk')
            return Q(**{f'{nombre}__in': subconsulta})

        if campo.one_to_many or campo.one_to_one:
            # Relación inversa: `id IN (SELECT fk_id FROM relacionado WHERE ...)`.
            subconsulta = relacionado._base_manager.filter(interior).values(campo.field.name)
            return Q(pk__in=subconsulta)

        # ManyToMany: subconsulta sobre el propio modelo (sin filas duplicadas).
        subconsulta = modelo._base_manager.filter(
            **{f'{LOOKUP_SEP.join(partes)}__{self._lookup(termino)}': termino}
        ).values('pk')
        return Q(pk__in=subconsulta)

    @staticmethod
    def _manager_tenant(modelo, empresa_id):
        """
        Queryset base del modelo relacionado. ``_base_manager`` incluye los
        eliminados lógicamente: un documento sigue encontrándose por el nombre
        de un cliente que se eliminó después. Si el modelo pertenece a una
        empresa se acota a la activa (misma regla que el listado) para que la
        subconsulta use el índice del tenant.
        """
        queryset = modelo._base_manager.all()
        if empresa_id and any(f.name == 'empresa' and f.concrete for f in modelo._meta.get_fields()):
            queryset = queryset.filter(empresa_id=empresa_id)
        return queryset
