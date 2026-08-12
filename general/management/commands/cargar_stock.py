import csv
import os
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.models import User
from inventario.models import Producto, Bodega, Stock, MovimientoInventario

class Command(BaseCommand):
    help = 'Registra saldos iniciales de inventario desde un archivo CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            required=True,
            help='Ruta al archivo CSV con los saldos iniciales.'
        )

    def handle(self, *args, **options):
        ruta_csv = options['csv']

        if not os.path.exists(ruta_csv):
            self.stdout.write(self.style.ERROR(f'El archivo {ruta_csv} no existe.'))
            return

        # Para un script automatizado, necesitamos un usuario que "firme" los movimientos.
        # Buscamos el primer superusuario (admin) disponible.
        usuario_sistema = User.objects.filter(is_superuser=True).first()
        if not usuario_sistema:
            self.stdout.write(self.style.ERROR('No hay un superusuario creado en el sistema. Crea uno con createsuperuser primero.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Iniciando carga de stock desde {ruta_csv} (Usuario: {usuario_sistema.username})...'))

        movimientos_creados = 0
        errores = 0

        with open(ruta_csv, mode='r', encoding='utf-8') as archivo_csv:
            lector = csv.DictReader(archivo_csv)

            columnas_req = ['sku', 'bodega_codigo', 'cantidad']
            if not all(col in lector.fieldnames for col in columnas_req):
                self.stdout.write(self.style.ERROR(f'El CSV debe contener: {", ".join(columnas_req)}'))
                return

            with transaction.atomic():
                for fila_num, fila in enumerate(lector, start=2):
                    try:
                        sku_raw = fila['sku'].strip().upper()
                        bodega_raw = fila['bodega_codigo'].strip().upper()

                        # Manejo seguro de la cantidad (convirtiendo a Decimal)
                        try:
                            cantidad = Decimal(fila['cantidad'].strip())
                            if cantidad <= 0:
                                raise ValueError("La cantidad debe ser mayor a 0 para un saldo inicial.")
                        except (ValueError, TypeError):
                            raise ValueError(f"Cantidad inválida: {fila.get('cantidad')}")

                        # 1. Buscar Producto
                        producto = Producto.objects.filter(sku=sku_raw).first()
                        if not producto:
                            raise ValueError(f"Producto SKU '{sku_raw}' no existe en el catálogo.")

                        # 2. Buscar o Crear Bodega (Si solo nos dan un código, la creamos para no frenar)
                        bodega, _ = Bodega.objects.get_or_create(
                            codigo=bodega_raw,
                            defaults={'nombre': f'Almacén {bodega_raw}'}
                        )

                        # 3. Leer campos opcionales
                        lote = fila.get('lote', '').strip() or None
                        ubicacion = fila.get('ubicacion', '').strip() or None

                        # 4. Registrar en el Kardex (MovimientoInventario)
                        MovimientoInventario.objects.create(
                            producto=producto,
                            bodega_destino=bodega,
                            tipo_movimiento=MovimientoInventario.ENTRADA,
                            cantidad=cantidad,
                            lote=lote,
                            referencia_operacion='SALDO_INICIAL_CSV',
                            usuario=usuario_sistema,
                            observaciones=f'Carga masiva inicial. Fila {fila_num}'
                        )

                        # 5. Actualizar la tabla de Stock Físico
                        # Utilizamos select_for_update() para evitar condiciones de carrera si alguien
                        # más está modificando el stock al mismo tiempo.
                        stock_record, created = Stock.objects.select_for_update().get_or_create(
                            producto=producto,
                            bodega=bodega,
                            defaults={'cantidad': Decimal('0.00'), 'ubicacion_especifica': ubicacion}
                        )

                        stock_record.cantidad += cantidad
                        if ubicacion and not stock_record.ubicacion_especifica:
                            stock_record.ubicacion_especifica = ubicacion

                        stock_record.save()

                        movimientos_creados += 1

                    except Exception as e:
                        errores += 1
                        self.stdout.write(self.style.WARNING(f'Fila {fila_num} (SKU: {fila.get("sku", "N/A")}): Error -> {str(e)}'))

        self.stdout.write(self.style.SUCCESS('\n--- Resumen de Carga de Stock ---'))
        self.stdout.write(f'Movimientos Registrados: {movimientos_creados}')
        if errores > 0:
            self.stdout.write(self.style.ERROR(f'Errores encontrados:     {errores}'))
        else:
            self.stdout.write(self.style.SUCCESS('Carga de stock completada sin errores.'))