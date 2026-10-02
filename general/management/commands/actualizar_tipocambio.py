from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from general.models import Moneda, TipoCambio
from general.services import sincronizar_tipos_cambio_banxico, BanxicoAPIError
from decimal import Decimal
import datetime

class Command(BaseCommand):
    help = 'Actualiza el tipo de cambio oficial diario para USD y EUR desde el API del Banco de México (SIE)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            help='Token de consulta para el API de Banxico (opcional, sobreescribe BANXICO_TOKEN)'
        )
        parser.add_argument(
            '--fecha',
            type=str,
            help='Fecha específica a consultar en formato YYYY-MM-DD (por defecto consulta el dato oportuno)'
        )
        parser.add_argument(
            '--simular',
            action='store_true',
            help='Si se especifica, aplica valores de contingencia/simulación en caso de falla de conexión'
        )

    def handle(self, *args, **options):
        token = options.get('token')
        fecha_str = options.get('fecha')
        simular = options.get('simular')

        fecha_obj = None
        if fecha_str:
            try:
                fecha_obj = datetime.datetime.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                raise CommandError("Formato de fecha inválido. Usa YYYY-MM-DD (ej: 2026-09-30).")

        self.stdout.write(self.style.HTTP_INFO('Consultando el API del Banco de México (SIE)...'))

        try:
            resultado = sincronizar_tipos_cambio_banxico(token=token, fecha=fecha_obj)
            for reg in resultado['registros']:
                accion = 'creado' if reg['creado'] else 'actualizado'
                self.stdout.write(self.style.SUCCESS(
                    f"[OK] 1 {reg['moneda']} = {reg['valor']} MXN ({reg['fecha']}) [{accion} exitosamente]"
                ))
            for adv in resultado['advertencias']:
                self.stdout.write(self.style.WARNING(f"[!] {adv}"))

            self.stdout.write(self.style.SUCCESS(f'Sincronización con Banxico completada exitosamente ({resultado["total"]} series).'))

        except BanxicoAPIError as e:
            if simular:
                self.stdout.write(self.style.WARNING(f'Fallo al conectar con Banxico ({e}). Aplicando valores de contingencia...'))
                usd, _ = Moneda.objects.get_or_create(codigo='USD', defaults={'nombre': 'Dólar Americano', 'simbolo': '$'})
                eur, _ = Moneda.objects.get_or_create(codigo='EUR', defaults={'nombre': 'Euro Zona', 'simbolo': '€'})
                fecha_sim = fecha_obj or (timezone.now().date() - datetime.timedelta(days=1))
                TipoCambio.objects.update_or_create(moneda_origen=usd, fecha=fecha_sim, defaults={'valor_en_mxn': Decimal('19.4200'), 'fuente': 'CONTINGENCIA_MANUAL'})
                TipoCambio.objects.update_or_create(moneda_origen=eur, fecha=fecha_sim, defaults={'valor_en_mxn': Decimal('21.1500'), 'fuente': 'CONTINGENCIA_MANUAL'})
                self.stdout.write(self.style.SUCCESS('Valores de contingencia registrados exitosamente.'))
            else:
                self.stdout.write(self.style.WARNING(f'Banxico SIE no disponible ({e}). Activando canal público oficial DOF / BCE...'))
                from general.services import sincronizar_tipos_cambio_dof_bce
                resultado = sincronizar_tipos_cambio_dof_bce(fecha=fecha_obj)
                for reg in resultado['registros']:
                    accion = 'creado' if reg['creado'] else 'actualizado'
                    self.stdout.write(self.style.SUCCESS(
                        f"[OK] 1 {reg['moneda']} = {reg['valor']} MXN ({reg['fecha']}) [{accion} exitosamente vía {reg['fuente']}]"
                    ))
                self.stdout.write(self.style.SUCCESS(f'Sincronización completada exitosamente ({resultado["fuente"]}).'))