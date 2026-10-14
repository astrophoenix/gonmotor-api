# CLAUDE.md — gonmotor-api

## 1. Propósito

API REST multi-tenant del SaaS **GonMotor**, un sistema de gestión para talleres mecánicos. Una **Empresa** (tenant) tiene uno o varios **Talleres** (sucursales; en el código y en la BD el campo suele llamarse `sucursal`). La API cubre todo el flujo operativo del taller:

**Cita → Recepción del vehículo → Inspección → Cotización → Orden de trabajo**

Además gestiona clientes, vehículos, proveedores, inventario (repuestos y servicios), gastos, reportes de rentabilidad, usuarios/roles/permisos, recordatorios de mantenimiento por WhatsApp y mejora de texto con IA. El cliente principal es el frontend `../gonmotor-ui/` (repo aparte).

Despliegue: Render (`render.yaml`, `rootDir: backend`, gunicorn, health check `GET /api/core/health/`).

## 2. Mapa del repositorio

```
gonmotor-api/
├── render.yaml                 # Servicio web en Render (build: pip + migrate + collectstatic)
├── comandos                    # Notas personales de comandos (contiene credenciales locales: no copiar)
└── backend/                    # rootDir del proyecto Django
    ├── manage.py               # DJANGO_SETTINGS_MODULE por defecto: config.settings.local
    ├── requirements.txt
    ├── .env                    # OBLIGATORIO (SECRET_KEY, DB_*); no versionado
    ├── venv/                   # entorno virtual local
    ├── logs/whatsapp_mock.log  # salida del modo mock de WhatsApp
    ├── media/                  # uploads (fotos de recepción/inspección/orden, logos, repuestos)
    ├── templates/  static/
    ├── config/
    │   ├── settings/{base,local,production}.py
    │   ├── urls.py             # enrutado raíz de todas las apps
    │   └── wsgi.py / asgi.py
    └── apps/
        ├── core/               # BaseModel, mixins, health, dashboard, búsqueda global, IA de texto, exportes
        ├── authentication/     # login JWT, perfil, usuarios, empleados, roles y permisos, contexto tenant
        ├── empresas/           # Empresa (tenant) y Taller (sucursal)
        ├── configuracion/      # pantallas de config: empresa y sucursales (sin modelos propios)
        ├── clientes/           # Cliente + importación/exportación Excel
        ├── vehiculos/          # Vehiculo + VehiculoPropietario (historial de dueños)
        ├── proveedores/
        ├── citas/              # Cita + export .ics compartible
        ├── ordenes/            # RecepcionVehiculo, InspeccionVehiculo, OrdenTrabajo, detalles y fotos
        ├── cotizaciones/       # Cotizacion + detalles de servicio/repuesto + PDF
        ├── inventario/         # Repuesto, RepuestoImagen, Servicio
        ├── gastos/             # Gasto
        ├── reportes/           # rentabilidad por taller / empresa (sin modelos)
        └── notificaciones/     # recordatorios WhatsApp de mantenimiento
```

### Piezas transversales clave

| Archivo | Rol |
|---|---|
| `apps/core/models.py` | `BaseModel` abstracto: `is_active`, `created_at`, `updated_at`. Todos los modelos de negocio heredan de él. |
| `apps/core/mixins.py` | `FiltroTallerMixin` (recorte por taller activo) y `SoftDeleteDestroyMixin` (borrado lógico si hay historial, físico si no). |
| `apps/authentication/utils.py` | **Fuente única del contexto tenant**: empresa, rol y taller de la petición. |
| `apps/authentication/permissions.py` | `TienePermiso(recurso, accion)`, `TieneRecurso(recurso)`, `EsAdminDeEmpresa`. |
| `apps/authentication/permisos_repo.py` | `MATRIZ_ROLES` (semilla), jerarquía de roles, lectura/escritura de permisos en BD. |
| `apps/ordenes/relaciones_flujo.py` | Grafo de vínculos entre cita, recepción, inspección, cotización y orden (FKs existentes), compartido por los cuatro editables. |
| `apps/core/services/text_improver.py` | Mejora de texto vía LLM compatible con OpenAI (por defecto Groq). Descubre el modelo disponible si `AI_TEXT_MODEL` está vacío. |
| `apps/core/utils/{pdf_export,excel_export,images}.py` | Exportación PDF (reportlab), Excel (openpyxl) y procesado de imágenes (Pillow). |
| `apps/notificaciones/services/` | `whatsapp.py` (proveedores mock/Twilio/Meta), `recordatorios.py`, `mensajes.py`, `telefonos.py`. |

### Rutas (`config/urls.py`)

| Prefijo | App |
|---|---|
| `api/core/` | health, `mejorar-texto/`, `buscar/`, `dashboard/`, `dashboard-v2/` |
| `api/auth/` | `login/`, `select-company/`, `register/`, `refresh/`, `me/`, `talleres/`, password reset/change, `roles/`, `recursos/`, `usuarios/`, `empleados/` |
| `api/token/refresh/` | refresco JWT (alias) |
| `api/clientes/`, `api/proveedores/`, `api/vehiculos/`, `api/empresas/`, `api/citas/`, `api/gastos/` | CRUD de cada app |
| `api/cotizaciones/` | cotizaciones + `servicios/` + `repuestos/` |
| `api/ordenes/` | `ordenes-trabajo/`, `inspecciones/`, `inspeccion-*`, `orden-fotos/`, `ot-servicios/`, `ot-repuestos/` |
| `api/recepciones/` | **vive en `apps.ordenes`** (`apps/ordenes/recepciones_urls.py`) |
| `api/repuestos/`, `api/repuestos-imagenes/`, `api/servicios/` | `apps.inventario.urls` montado directamente en `api/` |
| `api/reportes/` | `rentabilidad-taller/`, `rentabilidad-empresa/` |
| `api/notificaciones/` | estado, preferencias, recordatorios (listar/enviar/probar/ejecutar) |
| `api/configuracion/` | `empresa/`, `sucursales/` |

La mayoría de los módulos de listado exponen además `exportar-pdf/` y `export-excel/`.

## 3. Tecnologías y dependencias

- **Python / Django 6.1** + **Django REST Framework 3.18**
- **djangorestframework-simplejwt** (access 60 min, refresh 30 días, rotación + blacklist)
- **PostgreSQL** vía `psycopg2-binary` (también en local; el `db.sqlite3` del directorio es residual)
- `python-decouple` (config por `.env`), `django-cors-headers`, `django-countries`
- `reportlab` (PDF), `openpyxl` (Excel), `Pillow` (imágenes), `requests` (LLM, WhatsApp)
- Producción: `gunicorn`, `whitenoise` (estáticos comprimidos), SMTP para correo
- Idioma `es-ec`, zona horaria `America/Guayaquil`

## 4. Reglas de arquitectura y flujos

### Configuración y entorno
- Activar el venv antes de cualquier comando: `cd backend && source venv/bin/activate`.
- `backend/.env` es obligatorio: `SECRET_KEY` y `DB_NAME/USER/PASSWORD/HOST/PORT` no tienen valor por defecto. Opcionales: `DB_SSLMODE`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, `WHATSAPP_*`, `AI_TEXT_*`, `THROTTLE_*`.
- `local.py`: `DEBUG=True`, `ALLOWED_HOSTS=['*']`, email por consola. `production.py`: HTTPS/HSTS, whitenoise, SMTP, logging a consola.
- CORS permite las cabeceras `x-empresa-id` y `x-taller-id`; si se añade una cabecera nueva desde el frontend hay que agregarla a `CORS_ALLOW_HEADERS`.

### Multi-tenant (empresa → taller)
Todo el filtrado por tenant pasa por `apps/authentication/utils.py`; **no reimplementarlo en otro archivo**.

1. **Empresa activa**: se toma de la claim `empresa_id` del JWT y, si no hay, de la cabecera `X-Empresa-ID`. Se valida contra las relaciones activas `UsuarioEmpresa` del usuario. Si pide una empresa a la que no pertenece, el contexto es vacío (sin fallback). Con una sola empresa asignada entra directo. El superusuario puede usar cualquier empresa activa y actúa como `ADMIN_SISTEMA`.
2. **Rol**: se lee SIEMPRE de la BD (`UsuarioEmpresa.rol`), nunca de la claim `rol` del token (que es solo informativa).
3. **Taller activo**: precedencia cabecera `X-Taller-ID` → `UserProfile.taller_activo` → primer taller permitido, siempre validado contra la empresa y el alcance del rol.
4. **Alcance de talleres**: `ADMIN_SISTEMA` y `ADMIN_EMPRESA` (`ROLES_TODOS_TALLERES`) ven todos los talleres activos y pueden operar en «Todos» (sin cabecera = sin recorte). El resto solo ve los asignados en `UsuarioEmpresa.talleres` (vacío = ningún dato) y nunca queda sin recorte.
5. El resultado se cachea por petición (`request._contexto_gonmotor`).

Funciones a usar: `get_empresa_id_desde_request`, `get_rol_desde_request`, `get_contexto_desde_request`, `get_taller_desde_request`, `talleres_filtro_de_request`, `talleres_permitidos`, `validar_talleres_asignables` (antes de cualquier `M2M.set()` de talleres).

### Patrón de un ViewSet
```python
class XViewSet(SoftDeleteDestroyMixin, FiltroTallerMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, TieneRecurso('<recurso>')]
    taller_filter_field = 'sucursal'          # solo si el modelo cuelga de un taller
    delete_relation_fields = [...]            # relaciones que fuerzan soft delete

    def get_queryset(self):
        empresa_id = get_empresa_id_desde_request(self.request)
        if not empresa_id:
            return X.objects.none()
        qs = X.objects.filter(empresa_id=empresa_id)
        return self.filtrar_por_taller(qs)
```
- Sin empresa en contexto → `queryset.none()`; nunca devolver datos sin filtrar.
- Al crear, `empresa` (y el taller activo) se asignan desde el contexto del request, normalmente en el `create()` del serializer, con `empresa` en `read_only_fields`; nunca desde el payload.
- `FiltroTallerMixin` lo usan hoy los viewsets del flujo: citas, recepciones, inspecciones, cotizaciones y órdenes.
- Defaults DRF: `IsAuthenticated`, `PageNumberPagination` con `PAGE_SIZE=10`, throttling `300/min` usuario y `60/min` anónimo.
- Mensajes de error y respuestas en español.

### Roles y permisos
- Un permiso es `(recurso, acción)` con acciones `ver` / `modificar`. Los recursos están en `permisos_repo.RECURSOS` (empresa, talleres, usuarios, empleados, clientes, vehiculos, proveedores, citas, recepciones, inspecciones, cotizaciones, ordenes, inventario, facturacion, reportes).
- Roles de sistema: `ADMIN_SISTEMA`, `ADMIN_EMPRESA`, `ADMIN_TALLER`, `ASESOR`, `MECANICO`, `CAJERO`. Los roles personalizados son posibles y pueden tener el flag `ver_todos_talleres`.
- En tiempo de ejecución la fuente de verdad son las tablas `Rol`/`Recurso`/`Permiso`/`PermisoEspecial`, con overrides por empresa (`PermisoEmpresa`, `PermisoEspecialEmpresa`). `MATRIZ_ROLES` es la semilla: se siembra con una señal `post_migrate` (`authentication/signals.py`) o con `python manage.py seed_permisos`.
- Jerarquía `NIVELES_ROLES`: un usuario no puede otorgar un rol igual o superior al suyo (`puede_otorgar`).
- Si añades un recurso nuevo: agrégalo a `RECURSOS`, a `MATRIZ_ROLES`, a `NOMBRES_RECURSOS` y protege el viewset con `TieneRecurso`.

### Borrado
- `SoftDeleteDestroyMixin`: si el registro tiene historial (`delete_relation_fields`) se marca `is_active=False`; si no, se borra. Responde 200 con `{status, action, entity, identifier, message}`.
- Los listados suelen aceptar `?estado=activo|inactivo`; los selectores de búsqueda envían `estado=activo`.

### Flujo del taller
- Recepción, inspección, cotización y orden están enlazadas por FKs; `relaciones_flujo.py` centraliza su lectura y edición. Cualquier cambio de vínculos entre estas entidades va ahí, no en cada serializer.
- `cotizaciones/signals.py` recalcula los totales de la cotización al guardar/borrar sus detalles de servicio o repuesto; `recepciones` tiene la acción `firmar` (firma del cliente → estado `ACEPTADA`).
- Los documentos sin taller se rellenaron con un taller por defecto mediante migraciones de backfill; el filtro por taller es estricto (los registros con `sucursal` NULL quedan fuera).

### Integraciones externas
- **WhatsApp**: `WHATSAPP_MODE=mock` en local (sin llamadas externas, escribe en `logs/whatsapp_mock.log`). Proveedores Twilio/Meta documentados en `apps/notificaciones/services/whatsapp.py`. Comando programable: `python manage.py enviar_recordatorios_mantenimiento`.
- **IA de texto**: `POST /api/core/mejorar-texto/` `{texto, contexto?}` → `{mejorado}`. Máx. 500 caracteres. Variables `AI_TEXT_API_KEY`, `AI_TEXT_BASE_URL`, `AI_TEXT_MODEL` (opcional). No enviar datos personales.
- **Correo**: reset de contraseña con token de 1 hora; enlaces hacia `FRONTEND_URL`.

## Comandos habituales

```bash
cd backend && source venv/bin/activate
python manage.py runserver 0.0.0.0:8000
python manage.py makemigrations && python manage.py migrate
python manage.py test apps.<app>          # por app; requiere Postgres levantado
python manage.py seed_permisos | seed_clientes | seed_vehiculos
```

Hay tests en casi todas las apps (los más extensos: `authentication`, `ordenes`, `citas`, `cotizaciones`, `notificaciones`, `core`). Al tocar el contexto tenant o los permisos, corre `apps.authentication` y `apps.core`.
