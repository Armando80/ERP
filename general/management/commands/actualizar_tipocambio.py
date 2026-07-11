from django.core.management.base import BaseCommand
from general.models import Moneda, TipoCambio # Importamos tus modelos
from decimal import Decimal
import datetime

class Command(BaseCommand):
    help = 'Actualiza el tipo de cambio diario para USD y EUR con datos específicos'

    def handle(self, *args, **options):
        self.stdout.write('Iniciando la actualización del tipo de cambio diario...')

        try:
            # 1. Obtenemos las monedas extranjeras que ya poblamos (USD, EUR)
            usd = Moneda.objects.get(codigo='USD')
            eur = Moneda.objects.get(codigo='EUR')

            # 2. Definimos los tipos de cambio reales solicitados
            # Usamos DecimalField como especificaste para evitar errores
            tipos_cambio_hoy = [
                {'moneda': usd, 'valor': Decimal('19.42')}, # Valor real proporcionado
                {'moneda': eur, 'valor': Decimal('21.15')}, # Valor real proporcionado
            ]

            # 3. Iteramos e insertamos el tipo de cambio del día
            fecha_hoy = datetime.date.today()
            # Simulamos el día anterior como exige el SAT en CFDI 4.0
            # para USD/MXN y EUR/MXN si se visualizan como "del día"
            # En producción, usarías la fecha correcta de la API
            fecha_sat = fecha_hoy - datetime.timedelta(days=1)

            for tc in tipos_cambio_hoy:
                # Usamos update_or_create para actualizar si ya existe o crear si es nuevo
                # Esto cumple con el requisito unique_together = ('moneda_origen', 'fecha')
                obj, created = TipoCambio.objects.update_or_create(
                    moneda_origen=tc['moneda'],
                    fecha=fecha_sat, # O fecha_hoy
                    defaults={'valor_en_mxn': tc['valor']} # Actualiza el valor si existe
                )

                if created:
                    self.stdout.write(self.style.SUCCESS(f'Tipo de cambio para "{tc["moneda"].codigo}" creado exitosamente.'))
                else:
                    self.stdout.write(f'Tipo de cambio para "{tc["moneda"].codigo}" actualizado exitosamente.')

            self.stdout.write(self.style.SUCCESS('Actualización de tipo de cambio completada con éxito.'))

        except Moneda.DoesNotExist:
            self.stdout.write(self.style.ERROR('Error: Las monedas USD o EUR no existen. Ejecuta primero "poblar_monedas".'))