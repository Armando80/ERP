# ERP Decorlata - produccion/tests.py
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from general.models import Moneda
from inventario.models import UnidadMedida, Bodega, Producto, Stock, MovimientoInventario
from .models import ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo
from .services import (
    crear_orden_produccion,
    iniciar_orden_produccion,
    finalizar_orden_produccion,
    cancelar_orden_produccion
)


class ProduccionModuloTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='operador', password='password123')

        # Catálogos base
        self.moneda_mxn = Moneda.objects.create(
            codigo='MXN', nombre='Peso Mexicano', simbolo='$'
        )
        self.u_pza = UnidadMedida.objects.create(codigo='PZA', nombre='Pieza')
        self.u_kg = UnidadMedida.objects.create(codigo='KG', nombre='Kilogramo')

        self.bodega_mp = Bodega.objects.create(codigo='BOD-MP', nombre='Almacén Materia Prima')
        self.bodega_pt = Bodega.objects.create(codigo='BOD-PT', nombre='Almacén Producto Terminado')

        # Insumos / Materias Primas
        self.hojalata = Producto.objects.create(
            sku='MP-HOJ-01',
            nombre='Hojalata en Rollo T4',
            tipo=Producto.HOJALATA_ROLLO,
            unidad_medida=self.u_kg,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('25.000000')
        )
        self.valvula = Producto.objects.create(
            sku='CP-VAL-01',
            nombre='Válvula Aerosol 1 Pulgada',
            tipo=Producto.COMPONENTE,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('1.500000')
        )

        # Producto Terminado
        self.bote_aerosol = Producto.objects.create(
            sku='PT-AER-100',
            nombre='Bote de Aerosol 65x190mm',
            tipo=Producto.PRODUCTO_TERMINADO,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('0.000000')
        )

        # Inicializar Stock físico para los insumos en bodega de materia prima
        MovimientoInventario.objects.create(
            producto=self.hojalata,
            bodega_destino=self.bodega_mp,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('500.000000'),
            costo_unitario_original=Decimal('25.000000'),
            moneda_original=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            costo_unitario_mxn_capturado=Decimal('25.000000'),
            referencia_operacion='CARGA-INICIAL',
            usuario=self.user
        )
        MovimientoInventario.objects.create(
            producto=self.valvula,
            bodega_destino=self.bodega_mp,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('5000.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            referencia_operacion='CARGA-INICIAL',
            usuario=self.user
        )

        # Configurar Receta BOM: Para 1,000 botes requiere 50 kg hojalata y 1,000 válvulas
        self.bom = ListaMaterialesBOM.objects.create(
            producto_terminado=self.bote_aerosol,
            cantidad_base=Decimal('1000.0000'),
            descripcion_proceso='Corte, litografía, conformado y cerrado.'
        )
        self.insumo_hoj = InsumoBOM.objects.create(
            bom=self.bom,
            materia_prima=self.hojalata,
            cantidad_requerida=Decimal('50.000000'),
            porcentaje_merma=Decimal('0.00')
        )
        self.insumo_val = InsumoBOM.objects.create(
            bom=self.bom,
            materia_prima=self.valvula,
            cantidad_requerida=Decimal('1000.000000'),
            porcentaje_merma=Decimal('0.00')
        )

    def test_calculo_costo_teorico_bom(self):
        """Verifica que el BOM calcule correctamente el costo total y unitario proyectado."""
        # 50 kg * $25 = $1,250
        # 1000 pzas * $1.50 = $1,500
        # Total base = $2,750 para 1,000 botes => $2.75 / bote
        self.assertEqual(self.bom.costo_estimado_total_mxn, Decimal('2750.000000'))
        self.assertEqual(self.bom.costo_estimado_unitario_mxn, Decimal('2.750000'))

    def test_ciclo_completo_orden_produccion_kardex(self):
        """
        Prueba el flujo industrial completo:
        1. Creación de OP (2,000 botes)
        2. Iniciar OP y reservar stock
        3. Finalizar OP: Salidas de Kardex de insumos, Entrada de Kardex de PT y recálculo de costo promedio.
        """
        # 1. Crear Orden de Producción
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('2000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user,
            observaciones="Lote de prueba unitaria"
        )
        self.assertTrue(op.folio.startswith('OP-'))
        self.assertEqual(op.estado, OrdenProduccion.PLANEADA)

        # Insumos escalados para 2,000 botes (factor = 2)
        insumos = op.insumos_detalle.all()
        self.assertEqual(insumos.count(), 2)

        det_hoj = insumos.get(insumo=self.hojalata)
        det_val = insumos.get(insumo=self.valvula)
        self.assertEqual(det_hoj.cantidad_estimada, Decimal('100.000000'))
        self.assertEqual(det_val.cantidad_estimada, Decimal('2000.000000'))

        # 2. Iniciar Orden
        op_iniciada = iniciar_orden_produccion(op.id, self.user)
        self.assertEqual(op_iniciada.estado, OrdenProduccion.EN_PROCESO)

        # Verificar stock reservado
        stock_hoj = Stock.objects.get(producto=self.hojalata, bodega=self.bodega_mp)
        stock_val = Stock.objects.get(producto=self.valvula, bodega=self.bodega_mp)
        self.assertEqual(stock_hoj.cantidad_reservada, Decimal('100.000000'))
        self.assertEqual(stock_val.cantidad_reservada, Decimal('2000.000000'))

        # 3. Finalizar Orden con 2,000 botes reales fabricados
        op_finalizada = finalizar_orden_produccion(
            orden_id=op.id,
            cantidad_real=Decimal('2000.0000'),
            lote="LOT-TEST-AER-2026",
            usuario=self.user
        )

        self.assertEqual(op_finalizada.estado, OrdenProduccion.TERMINADA)
        self.assertEqual(op_finalizada.cantidad_producida, Decimal('2000.0000'))

        # Costos calculados: (100 kg * $25) + (2000 pzas * $1.50) = $2,500 + $3,000 = $5,500
        # Costo unitario = $5,500 / 2,000 = $2.75
        self.assertEqual(op_finalizada.costo_total_insumos_mxn, Decimal('5500.000000'))
        self.assertEqual(op_finalizada.costo_unitario_final_mxn, Decimal('2.750000'))

        # 4. Verificar Afectación de Stock
        stock_hoj.refresh_from_db()
        stock_val.refresh_from_db()
        # Stock inicial 500 - 100 = 400
        self.assertEqual(stock_hoj.cantidad, Decimal('400.000000'))
        self.assertEqual(stock_hoj.cantidad_reservada, Decimal('0.000000'))
        # Stock inicial 5000 - 2000 = 3000
        self.assertEqual(stock_val.cantidad, Decimal('3000.000000'))
        self.assertEqual(stock_val.cantidad_reservada, Decimal('0.000000'))

        # Stock del Producto Terminado en bodega destino
        stock_pt = Stock.objects.get(producto=self.bote_aerosol, bodega=self.bodega_pt)
        self.assertEqual(stock_pt.cantidad, Decimal('2000.000000'))

        # Costo Promedio en MXN del Producto Terminado recalculado
        self.bote_aerosol.refresh_from_db()
        self.assertEqual(self.bote_aerosol.costo_promedio_mxn, Decimal('2.750000'))

        # 5. Verificar Movimientos en Kardex
        movs = MovimientoInventario.objects.filter(referencia_operacion=f"OP-{op.folio}")
        # 2 salidas (hojalata y válvula) + 1 entrada (bote aerosol)
        self.assertEqual(movs.count(), 3)
        salidas = movs.filter(tipo_movimiento=MovimientoInventario.SALIDA)
        entradas = movs.filter(tipo_movimiento=MovimientoInventario.ENTRADA)
        self.assertEqual(salidas.count(), 2)
        self.assertEqual(entradas.count(), 1)

    def test_cancelar_orden_produccion_libera_reservas(self):
        """Verifica que al cancelar una orden en proceso se libere el stock apartado."""
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('1000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )
        iniciar_orden_produccion(op.id, self.user)

        stock_hoj = Stock.objects.get(producto=self.hojalata, bodega=self.bodega_mp)
        self.assertEqual(stock_hoj.cantidad_reservada, Decimal('50.000000'))

        cancelar_orden_produccion(op.id, self.user)
        op.refresh_from_db()
        stock_hoj.refresh_from_db()

        self.assertEqual(op.estado, OrdenProduccion.CANCELADA)
        self.assertEqual(stock_hoj.cantidad_reservada, Decimal('0.000000'))

    def test_endpoints_vistas_y_pdf(self):
        """Prueba que los endpoints principales y la generación de PDF respondan con HTTP 200."""
        client = Client()
        client.force_login(self.user)

        # Catálogo de BOMs
        resp_boms = client.get(reverse('produccion:lista_boms'))
        self.assertEqual(resp_boms.status_code, 200)

        # Detalle de BOM
        resp_det_bom = client.get(reverse('produccion:detalle_bom', args=[self.bom.id]))
        self.assertEqual(resp_det_bom.status_code, 200)

        # Catálogo de Órdenes
        resp_ordenes = client.get(reverse('produccion:lista_ordenes'))
        self.assertEqual(resp_ordenes.status_code, 200)

        # Formulario de Creación de OP
        resp_form = client.get(reverse('produccion:crear_orden'))
        self.assertEqual(resp_form.status_code, 200)

        # Preview HTMX de BOM
        resp_preview = client.get(reverse('produccion:previsualizar_bom'), {
            'producto_a_fabricar': self.bote_aerosol.id,
            'cantidad_a_producir': '1000',
            'bodega_origen_insumos': self.bodega_mp.id
        })
        self.assertEqual(resp_preview.status_code, 200)
        self.assertContains(resp_preview, 'Hojalata en Rollo T4')

        # Crear una orden para probar detalle y PDF
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('500.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )

        resp_det_op = client.get(reverse('produccion:detalle_orden', args=[op.id]))
        self.assertEqual(resp_det_op.status_code, 200)

        # Descargar Hoja de Viajero en PDF con WeasyPrint
        resp_pdf = client.get(reverse('produccion:descargar_pdf', args=[op.id]))
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf['Content-Type'], 'application/pdf')
        self.assertTrue(resp_pdf.content.startswith(b'%PDF'))
