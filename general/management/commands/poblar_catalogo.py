import csv
import os
from django.core.management.base import BaseCommand
from django.db import transaction
from inventario.models import Producto, UnidadMedida
from general.models import Moneda

class Command(BaseCommand):
    help = 'Puebla el catálogo de Productos (MP, CP, PT) desde un archivo CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            required=True,
            help='Ruta absoluta o relativa al archivo CSV con los productos.'
        )

    def handle(self, *args, **options):
        ruta_csv = options['csv']

        if not os.path.exists(ruta_csv):
            self.stdout.write(self.style.ERROR(f'El archivo {ruta_csv} no existe.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Iniciando lectura de {ruta_csv}...'))

        # Contadores para el reporte final
        creados = 0
        actualizados = 0
        errores = 0

        # Mapeo de sinónimos comunes a los tipos definidos en el modelo
        mapa_tipos = {
            'materia prima': Producto.MATERIA_PRIMA,
            'mp': Producto.MATERIA_PRIMA,
            'quimico': Producto.MATERIA_PRIMA,
            'componente': Producto.COMPONENTE,
            'cp': Producto.COMPONENTE,
            'valvula': Producto.COMPONENTE,
            'producto terminado': Producto.PRODUCTO_TERMINADO,
            'pt': Producto.PRODUCTO_TERMINADO,
            'aerosol': Producto.PRODUCTO_TERMINADO
        }

        with open(ruta_csv, mode='r', encoding='utf-8') as archivo_csv:
            lector = csv.DictReader(archivo_csv)
            
            # Verificando columnas requeridas
            columnas_requeridas = ['sku', 'nombre', 'tipo', 'unidad_medida']
            if not all(col in lector.fieldnames for col in columnas_requeridas):
                self.stdout.write(self.style.ERROR(f'El CSV debe contener al menos estas columnas: {", ".join(columnas_requeridas)}'))
                return

            with transaction.atomic():
                for fila_num, fila in enumerate(lector, start=2):
                    try:
                        # 1. Limpieza de datos básicos
                        sku_limpio = fila['sku'].strip().upper()
                        nombre_limpio = fila['nombre'].strip()
                        descripcion_limpia = fila.get('descripcion', '').strip()
                        
                        # 2. Resolución del Tipo de Producto
                        tipo_raw = fila['tipo'].strip().lower()
                        tipo_producto = mapa_tipos.get(tipo_raw, Producto.PRODUCTO_TERMINADO) # PT por defecto

                        # 3. Resolución / Creación de Unidad de Medida
                        um_nombre = fila['unidad_medida'].strip()
                        um_codigo = um_nombre[:10].upper() # Fallback para código
                        unidad, _ = UnidadMedida.objects.get_or_create(
                            nombre__iexact=um_nombre,
                            defaults={'nombre': um_nombre.capitalize(), 'codigo': um_codigo}
                        )

                        # 4. Resolución de Monedas (Por defecto ID=1 (MXN) si está en blanco)
                        moneda_costo_raw = fila.get('moneda_costo', '').strip().upper()
                        moneda_venta_raw = fila.get('moneda_venta', '').strip().upper()

                        if moneda_costo_raw:
                            moneda_costo = Moneda.objects.filter(codigo=moneda_costo_raw).first()
                        else:
                            moneda_costo = Moneda.objects.get(id=1) # Fallback seguro
                            
                        if moneda_venta_raw:
                            moneda_venta = Moneda.objects.filter(codigo=moneda_venta_raw).first()
                        else:
                            moneda_venta = Moneda.objects.get(id=1) # Fallback seguro

                        if not moneda_costo or not moneda_venta:
                            raise ValueError(f"No se encontró la moneda {moneda_costo_raw} o {moneda_venta_raw} en el catálogo 'Moneda'.")

                        # 5. Creación o Actualización del Producto
                        producto, creado = Producto.objects.update_or_create(
                            sku=sku_limpio,
                            defaults={
                                'nombre': nombre_limpio,
                                'descripcion': descripcion_limpia,
                                'tipo': tipo_producto,
                                'unidad_medida': unidad,
                                'moneda_base_costo': moneda_costo,
                                'moneda_base_venta': moneda_venta,
                            }
                        )

                        if creado:
                            creados += 1
                        else:
                            actualizados += 1

                    except Exception as e:
                        errores += 1
                        self.stdout.write(self.style.WARNING(f'Fila {fila_num} (SKU: {fila.get("sku", "N/A")}): Error -> {str(e)}'))

        self.stdout.write(self.style.SUCCESS('\n--- Resumen de Carga ---'))
        self.stdout.write(f'Productos Creados:     {creados}')
        self.stdout.write(f'Productos Actualizados: {actualizados}')
        if errores > 0:
            self.stdout.write(self.style.ERROR(f'Errores encontrados:   {errores}'))
        else:
            self.stdout.write(self.style.SUCCESS('Carga completada sin errores.'))