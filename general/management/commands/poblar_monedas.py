from django.core.management.base import BaseCommand
from general.models import Moneda # Importamos tu modelo Moneda

class Command(BaseCommand):
    help = 'Pobla la base de datos con las monedas base iniciales (MXN, USD, EUR)'

    def handle(self, *args, **options):
        self.stdout.write('Iniciando la población de monedas...')

        # Definimos los datos exactos para MXN, USD y EUR [cite: 6]
        # Usamos diccionarios para get_or_create
        monedas_datos = [
            {'codigo': 'MXN', 'nombre': 'Peso Mexicano', 'simbolo': '$'},
            {'codigo': 'USD', 'nombre': 'Dólar Estadounidense', 'simbolo': '$'},
            {'codigo': 'EUR', 'nombre': 'Euro Zona', 'simbolo': '€'},
        ]

        # Iteramos e insertamos usando get_or_create para evitar duplicados
        # si el script se ejecuta más de una vez.
        for datos in monedas_datos:
            moneda, creada = Moneda.objects.get_or_create(
                codigo=datos['codigo'],
                defaults=datos # Usa el resto de datos si la crea
            )

            if creada:
                # Mensaje de éxito en español
                self.stdout.write(self.style.SUCCESS(f'Moneda "{moneda.codigo}" creada correctamente.'))
            else:
                # Mensaje informando que ya existe
                self.stdout.write(self.style.WARNING(f'La moneda "{moneda.codigo}" ya existe en la base de datos.'))

        # Mensaje final de éxito [cite: django_docs]
        self.stdout.write(self.style.SUCCESS('Población de monedas base completada con éxito.'))