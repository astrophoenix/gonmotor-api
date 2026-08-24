import random
from django.core.management.base import BaseCommand
from apps.clientes.models import Cliente
from apps.empresas.models import Empresa


NOMBRES = [
    "Juan", "María", "Carlos", "Ana", "Luis", "Patricia", "Roberto", "Carmen",
    "Diego", "Fernanda", "Jorge", "Lucía", "Andrés", "Valentina", "Ricardo",
    "Isabel", "Felipe", "Gabriela", "Sebastián", "Daniela", "Oscar", "Paula",
    "Raúl", "Mariana", "Hugo", "Alejandra", "Fernando", "Sofía", "Eduardo",
    "Camila", "Martín", "Natalia", "Pablo", "Verónica", "Antonio", "Teresa",
    "Miguel", "Rosa", "José", "Laura", "Manuel", "Elena", "Francisco", "Mónica",
    "David", "Silvia", "Cristian", "Diana", "Ángel", "Ruth", "Iván", "Lorena",
    "Guillermo", "Patricia", "Rafael", "Andrea", "Emilio", "Carolina", "Santiago",
    "Adriana", "Javier", "Marina", "Tomás", "Claudia", "Nicolás", "Jessica",
    "Esteban", "Karen", "Rodrigo", "Liliana", "Hernán", "Susana", "Gustavo",
    "Pamela", "Julio", "Nelly", "Armando", "Beatriz", "Fabián", "Martha",
    "César", "Gladys", "René", "Irene", "Orlando", "Mabel", "Wilson", "Rocío",
    "Mauricio", "Vanesa", "Luis Miguel", "Katherine", "Jaime", "Lourdes",
    "Víctor", "Patricia", "Ricardo", "Alejandra", "Juan Carlos", "Fernanda",
]

APELLIDOS = [
    "Pérez", "González", "Rodríguez", "Fernández", "López", "Martínez",
    "García", "Sánchez", "Romero", "Torres", "Flores", "Rivera", "Gómez",
    "Díaz", "Morales", "Reyes", "Cruz", "Ortiz", "Jiménez", "Castro",
    "Vargas", "Mendoza", "Guerrero", "Rojas", "Aguilar", "Delgado", "Silva",
    "Paredes", "Bravo", "Molina", "Vega", "Campos", "Mejía", "Núñez",
    "Andrade", "Paredes", "Tapia", "Carrasco", "León", "Navarro", "Ríos",
    "Benítez", "Herrera", "Soto", "Pacheco", "Vera", "Cabrera", "Luna",
    "Montero", "Espinoza", "Cevallos", "Arroyo", "Salazar", "Cordero",
]

CALLE_PREFIX = [
    "Av.", "Calle", "Pasaje", "Urbanización", "Sector", "Km.", "Blocker",
]

CALLE_NOMBRE = [
    "de los Shyris", "Amazonas", "Patria", "Eloy Alfaro", "6 de Diciembre",
    "Gran Colombia", "Bolívar", "Sucre", "Tarqui", "10 de Agosto",
    "Luis Cordero", "Miguel de Santiago", "General Egas", "Presidente Córdova",
    "Rocafuerte", "Ayacucho", "Pichincha", "Imbabura", "Cotopaxi", "Chimborazo",
    "Tungurahua", "Loja", "Azuay", "Manabí", "Esmeraldas", "Guayas",
    "Sangolquí", "Cumbayá", "Valle de los Chillos", "Pifo", "El Condado",
]

BARRIO = [
    "La Carolina", "La Mariscal", "La Floresta", "La Argelia", "Quito Norte",
    "Quito Sur", "Quito Centro", "El Batán", "Bellavista", "La Kennedy",
    "La Merced", "San Roque", "San Juan", "La Magdalena", "El Condado",
    "Cotocollao", "Calacalí", "San Miguel de los Bancos", "Machachi", "Sangolquí",
    "Cumbayá", "Valle de los Chillos", "Tumbaco", "Puembo", "Alóag",
    "Guayaquil Centro", "Urdesa", "Samborondón", "Kennedy Norte", "Sauces",
    "Mapasingue", "Trinitaria", "Flor de Bastión", "Guasmo", "Centenario",
    "Cuenca Centro", "El Vecino", "Miraflores", "Totoracocha", "Baños",
    "Girón", "Paute", "Azogues", "Biblián", "La Troncal", "Santa Isabel",
]

CONTIFICO_PREFIX = ["CTF", "CNT", "INT", "EXT", "SUP", "ADM", "VEN", "PRO"]


def generar_identificacion(empresa_id, tipo, existentes):
    intentos = 0
    while intentos < 1000:
        if tipo == "C":
            numero = f"{random.randint(1, 24):02d}{random.randint(100000, 999999)}"
            identificacion = numero[:10]
        elif tipo == "R":
            numero = f"{random.randint(1, 24):02d}{random.randint(100000, 999999)}"
            identificacion = numero + f"{random.randint(1, 999):03d}"
        else:
            identificacion = f"P{random.randint(100000, 999999)}"
        
        clave = (empresa_id, identificacion)
        if clave not in existentes:
            existentes.add(clave)
            return identificacion
        intentos += 1
    raise RuntimeError("No se pudo generar identificación única después de muchos intentos")


def generar_telefono():
    if random.random() < 0.3:
        return ""
    if random.random() < 0.7:
        return f"09{random.randint(10000000, 99999999)}"
    return f"02{random.randint(100000, 999999)}"


def generar_email(nombre, apellido):
    if random.random() < 0.35:
        return ""
    dominios = ["gmail.com", "outlook.com", "hotmail.com", "yahoo.es", "empresa.ec"]
    nombre_clean = nombre.lower().replace(" ", "").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    apellido_clean = apellido.lower().replace(" ", "").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    return f"{nombre_clean}.{apellido_clean}@{random.choice(dominios)}"


def generar_direccion():
    partes = []
    partes.append(random.choice(CALLE_PREFIX))
    partes.append(random.choice(CALLE_NOMBRE))
    if random.random() < 0.6:
        partes.append(f"N° {random.randint(1, 999)}")
    if random.random() < 0.5:
        partes.append(f"y {random.choice(CALLE_NOMBRE)}")
    if random.random() < 0.4:
        partes.append(f"({random.choice(BARRIO)})")
    return ", ".join(partes)


def generar_contifico_id():
    if random.random() < 0.7:
        return ""
    return f"{random.choice(CONTIFICO_PREFIX)}-{random.randint(1000, 9999)}"


class Command(BaseCommand):
    help = "Genera 100 clientes de prueba distribuidos en las 3 empresas existentes"

    def handle(self, *args, **options):
        empresas = list(Empresa.objects.all().order_by("id")[:3])
        if len(empresas) < 3:
            self.stderr.write(self.style.ERROR("Se requieren al menos 3 empresas en la base de datos."))
            return

        if Cliente.objects.exists():
            self.stdout.write(self.style.WARNING("Ya existen clientes. Se agregarán solo los nuevos."))

        existentes = set(
            Cliente.objects.values_list("empresa_id", "identificacion")
        )

        tipos = ["C", "C", "C", "R", "R", "P"]
        total = 100
        por_empresa = [34, 33, 33]

        creados = 0

        for empresa, cantidad in zip(empresas, por_empresa):
            self.stdout.write(f"Empresa: {empresa.nombre_comercial} ({cantidad} clientes)")
            for _ in range(cantidad):
                tipo = random.choice(tipos)
                nombre = random.choice(NOMBRES)
                apellido = random.choice(APELLIDOS)
                identificacion = generar_identificacion(empresa.id, tipo, existentes)

                cliente = Cliente(
                    empresa=empresa,
                    tipo_identificacion=tipo,
                    identificacion=identificacion,
                    nombre=f"{nombre} {apellido}",
                    email=generar_email(nombre, apellido),
                    telefono=generar_telefono(),
                    direccion=generar_direccion(),
                    contifico_id=generar_contifico_id(),
                )
                cliente.save()
                creados += 1

        self.stdout.write(self.style.SUCCESS(f"Se crearon {creados} clientes exitosamente."))
