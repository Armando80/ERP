# ERP Decorlata - produccion/tests.py
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from general.models import Moneda, TipoCambio
from inventario.models import UnidadMedida, Bodega, Producto, Stock, MovimientoInventario
from .models import (
    ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo,
    EntregaParcialProduccion, EntregaParcial_Insumo, EtapaProduccionOP
)
from .services import (
    crear_orden_produccion,
    iniciar_orden_produccion,
    finalizar_orden_produccion,
    cancelar_orden_produccion,
    notificar_entrega_parcial,
    autorizar_entrega_parcial,
    rechazar_entrega_parcial,
    cerrar_orden_definitiva,
    inicializar_etapas_ensamble_op,
    crear_orden_desde_pedido,
    explosion_materiales_bom,
    actualizar_avance_etapa_ensamble
)
from ventas.models import Cliente, PedidoVenta_Maestro, PedidoVenta_Detalle


class ProduccionModuloTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='operador', password='password123')
        self.almacen_user = User.objects.create_user(username='almacenista', password='password123')

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

    def test_notificacion_parcial_congelada_sin_afectar_kardex(self):
        """
        Verifica que al registrar una entrega parcial de producción:
        - Se cree la entrega en estado PENDIENTE.
        - Se calculen los insumos proporcionales según el BOM.
        - NO se generen movimientos en el Kardex.
        - NO se afecte el stock físico en ninguna bodega (queda congelado).
        """
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('1000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )
        iniciar_orden_produccion(op.id, self.user)

        # Notificar entrega parcial de 300 botes
        entrega = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('300.0000'),
            lote="LOT-PARCIAL-01",
            usuario_produccion=self.user,
            observaciones="Primer tiraje parcial matutino"
        )

        self.assertEqual(entrega.estado, EntregaParcialProduccion.PENDIENTE)
        self.assertEqual(entrega.cantidad_notificada, Decimal('300.0000'))
        self.assertEqual(entrega.folio_entrega, f"ENT-{op.folio}-01")
        self.assertEqual(entrega.usuario_notifica, self.user)
        self.assertIsNone(entrega.usuario_autoriza)

        # Insumos proporcionales calculados: 300 / 1000 = 0.3
        # Hojalata: 50 * 0.3 = 15 kg
        # Válvula: 1000 * 0.3 = 300 pzas
        detalles = entrega.insumos_detalle.all()
        self.assertEqual(detalles.count(), 2)
        ins_hoj = detalles.get(insumo=self.hojalata)
        ins_val = detalles.get(insumo=self.valvula)
        self.assertEqual(ins_hoj.cantidad_estimada, Decimal('15.000000'))
        self.assertEqual(ins_val.cantidad_estimada, Decimal('300.000000'))

        # REGLA CRÍTICA: CERO movimientos en el Kardex para esta entrega
        movs = MovimientoInventario.objects.filter(referencia_operacion=entrega.folio_entrega)
        self.assertEqual(movs.count(), 0)

        # REGLA CRÍTICA: Stock físico intacto (no se ha descontado ni ingresado)
        stock_hoj = Stock.objects.get(producto=self.hojalata, bodega=self.bodega_mp)
        stock_val = Stock.objects.get(producto=self.valvula, bodega=self.bodega_mp)
        stock_pt = Stock.objects.filter(producto=self.bote_aerosol, bodega=self.bodega_pt).first()

        self.assertEqual(stock_hoj.cantidad, Decimal('500.000000'))
        self.assertEqual(stock_val.cantidad, Decimal('5000.000000'))
        self.assertTrue(stock_pt is None or stock_pt.cantidad == Decimal('0.000000'))

        # Propiedades de la OP
        op.refresh_from_db()
        self.assertEqual(op.entregas_pendientes_count, 1)
        self.assertEqual(op.cantidad_pendiente, Decimal('1000.0000'))
        self.assertEqual(op.porcentaje_avance, 0.0)

    def test_autorizacion_almacen_aplica_kardex_y_acumula_avance(self):
        """
        Verifica que al autorizar una entrega parcial desde el módulo de Inventario:
        - Pase a estado AUTORIZADA con el usuario de almacén registrado.
        - Se generen salidas de Kardex para insumos y entrada para PT.
        - Se descuente el stock físico de materia prima y se incremente el stock físico de PT.
        - Se recalcule el costo unitario de la entrega y el costo promedio del PT.
        - Se actualice el avance de la orden de producción acumulando cantidad_producida.
        """
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('1000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )
        iniciar_orden_produccion(op.id, self.user)

        entrega = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('400.0000'),
            lote="LOT-PARCIAL-02",
            usuario_produccion=self.user
        )

        # Almacén autoriza
        entrega_auth = autorizar_entrega_parcial(
            entrega_id=entrega.id,
            usuario_almacen=self.almacen_user,
            notas_almacen="Recibido conforme en tarima A-04"
        )

        self.assertEqual(entrega_auth.estado, EntregaParcialProduccion.AUTORIZADA)
        self.assertEqual(entrega_auth.usuario_autoriza, self.almacen_user)
        self.assertEqual(entrega_auth.notas_almacen, "Recibido conforme en tarima A-04")
        self.assertIsNotNone(entrega_auth.fecha_autorizacion)

        # Verificar costo: (20 kg hojalata * $25) + (400 valvulas * $1.50) = 500 + 600 = $1,100
        # Costo unitario = 1100 / 400 = $2.75
        self.assertEqual(entrega_auth.costo_total_insumos_mxn, Decimal('1100.000000'))
        self.assertEqual(entrega_auth.costo_unitario_final_mxn, Decimal('2.750000'))

        # Kardex: 2 salidas (hojalata y válvula) + 1 entrada (bote aerosol)
        movs = MovimientoInventario.objects.filter(referencia_operacion=entrega.folio_entrega)
        self.assertEqual(movs.count(), 3)
        salidas = movs.filter(tipo_movimiento=MovimientoInventario.SALIDA)
        entradas = movs.filter(tipo_movimiento=MovimientoInventario.ENTRADA)
        self.assertEqual(salidas.count(), 2)
        self.assertEqual(entradas.count(), 1)

        # Stock físico actualizado
        stock_hoj = Stock.objects.get(producto=self.hojalata, bodega=self.bodega_mp)
        stock_val = Stock.objects.get(producto=self.valvula, bodega=self.bodega_mp)
        stock_pt = Stock.objects.get(producto=self.bote_aerosol, bodega=self.bodega_pt)

        # 500 - 20 = 480 kg
        self.assertEqual(stock_hoj.cantidad, Decimal('480.000000'))
        # 5000 - 400 = 4600 pzas
        self.assertEqual(stock_val.cantidad, Decimal('4600.000000'))
        # 400 botes entraron a bodega PT
        self.assertEqual(stock_pt.cantidad, Decimal('400.000000'))

        # Avance de la OP
        op.refresh_from_db()
        self.assertEqual(op.cantidad_producida, Decimal('400.0000'))
        self.assertEqual(op.cantidad_pendiente, Decimal('600.0000'))
        self.assertEqual(op.porcentaje_avance, 40.0)
        self.assertEqual(op.entregas_pendientes_count, 0)
        self.assertEqual(op.estado, OrdenProduccion.EN_PROCESO)

        # Notificar la entrega restante de 600 y autorizarla -> OP debe pasar a TERMINADA
        entrega_fin = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('600.0000'),
            usuario_produccion=self.user
        )
        autorizar_entrega_parcial(entrega_fin.id, self.almacen_user)

        op.refresh_from_db()
        self.assertEqual(op.cantidad_producida, Decimal('1000.0000'))
        self.assertEqual(op.cantidad_pendiente, Decimal('0.0000'))
        self.assertEqual(op.porcentaje_avance, 100.0)
        self.assertEqual(op.estado, OrdenProduccion.TERMINADA)

    def test_rechazo_entrega_parcial_almacen(self):
        """
        Verifica que al rechazar una entrega:
        - Pase a estado RECHAZADA con el motivo registrado.
        - NO se generen movimientos en el Kardex.
        - Las existencias de insumos y PT se mantengan intactas.
        """
        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('500.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )
        entrega = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('100.0000'),
            usuario_produccion=self.user
        )

        # Rechazo por almacén
        entrega_rechazada = rechazar_entrega_parcial(
            entrega_id=entrega.id,
            usuario_almacen=self.almacen_user,
            motivo="Defecto en el engargolado del bote"
        )

        self.assertEqual(entrega_rechazada.estado, EntregaParcialProduccion.RECHAZADA)
        self.assertEqual(entrega_rechazada.notas_almacen, "Defecto en el engargolado del bote")
        self.assertEqual(entrega_rechazada.usuario_autoriza, self.almacen_user)

        # Cero movimientos en Kardex
        movs = MovimientoInventario.objects.filter(referencia_operacion=entrega.folio_entrega)
        self.assertEqual(movs.count(), 0)

        # La OP no suma cantidad_producida
        op.refresh_from_db()
        self.assertEqual(op.cantidad_producida, Decimal('0.0000'))

    def test_endpoints_gestion_y_notificacion_produccion(self):
        """Prueba los flujos HTTP de edición/gestión y notificación de entregas parciales."""
        client = Client()
        client.force_login(self.user)

        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('1000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )

        # 1. Vista de Gestión de la OP
        resp_gestion = client.get(reverse('produccion:gestionar_orden', args=[op.id]))
        self.assertEqual(resp_gestion.status_code, 200)
        self.assertContains(resp_gestion, op.folio)
        self.assertContains(resp_gestion, 'Notificar Entrega')
        self.assertContains(resp_gestion, 'Historial de Entregas Parciales')

        # 2. Notificar Entrega Parcial vía POST
        resp_notif = client.post(reverse('produccion:notificar_entrega', args=[op.id]), {
            'cantidad_notificada': '250',
            'lote_fabricacion': 'LOT-TEST-WEB-01',
            'observaciones_produccion': 'Turno 1'
        }, follow=True)
        self.assertEqual(resp_notif.status_code, 200)

        op.refresh_from_db()
        self.assertEqual(op.entregas_parciales.count(), 1)
        entrega = op.entregas_parciales.first()
        self.assertEqual(entrega.cantidad_notificada, Decimal('250.0000'))
        self.assertEqual(entrega.estado, EntregaParcialProduccion.PENDIENTE)

        # 3. Cerrar Orden Definitiva vía POST
        resp_cerrar = client.post(reverse('produccion:cerrar_orden_definitiva', args=[op.id]), follow=True)
        self.assertEqual(resp_cerrar.status_code, 200)
        op.refresh_from_db()
        self.assertEqual(op.estado, OrdenProduccion.TERMINADA)

    def test_endpoints_autorizacion_almacen_inventario(self):
        """Prueba los endpoints HTMX de autorización y rechazo en el módulo de Inventario."""
        client = Client()
        client.force_login(self.almacen_user)

        op = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('800.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )
        entrega = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('200.0000'),
            usuario_produccion=self.user
        )

        # 1. Listar entregas pendientes de autorización (HTMX)
        resp_lista = client.get(reverse('inventario:entregas_pendientes'))
        self.assertEqual(resp_lista.status_code, 200)
        self.assertContains(resp_lista, entrega.folio_entrega)
        self.assertContains(resp_lista, '200')

        # 2. Detalle modal de insumos a descontar
        resp_modal = client.get(reverse('inventario:detalle_insumos_entrega', args=[entrega.id]))
        self.assertEqual(resp_modal.status_code, 200)
        self.assertContains(resp_modal, 'Hojalata en Rollo T4')

        # 3. Autorizar entrega vía POST con HX-Trigger en la cabecera
        resp_auth = client.post(reverse('inventario:autorizar_entrega', args=[entrega.id]), {
            'notas_almacen': 'Aprobado sin novedades'
        })
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.headers.get('HX-Trigger'), 'entregaProcesada')

        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaParcialProduccion.AUTORIZADA)

        # 4. Crear otra entrega y rechazarla con cabecera HX-Prompt
        entrega_rechazar = notificar_entrega_parcial(
            orden_id=op.id,
            cantidad_notificada=Decimal('100.0000'),
            usuario_produccion=self.user
        )
        resp_rechazo = client.post(
            reverse('inventario:rechazar_entrega', args=[entrega_rechazar.id]),
            HTTP_HX_PROMPT='Material rayado'
        )
        self.assertEqual(resp_rechazo.status_code, 200)
        self.assertEqual(resp_rechazo.headers.get('HX-Trigger'), 'entregaProcesada')

        entrega_rechazar.refresh_from_db()
        self.assertEqual(entrega_rechazar.estado, EntregaParcialProduccion.RECHAZADA)
        self.assertEqual(entrega_rechazar.notas_almacen, 'Material rayado')


class CosteoMultidivisaBOMTestCase(TestCase):
    """Pruebas unitarias para la jerarquía de costeo inteligente y conversión multidivisa en Recetas (BOM)."""

    def setUp(self):
        self.moneda_mxn, _ = Moneda.objects.get_or_create(codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'})
        self.moneda_usd, _ = Moneda.objects.get_or_create(codigo='USD', defaults={'nombre': 'Dólar Americano', 'simbolo': '$'})
        self.moneda_eur, _ = Moneda.objects.get_or_create(codigo='EUR', defaults={'nombre': 'Euro Zona', 'simbolo': '€'})

        # Registrar tipos de cambio de prueba
        from django.utils import timezone
        hoy = timezone.now().date()
        TipoCambio.objects.update_or_create(moneda_origen=self.moneda_usd, fecha=hoy, defaults={'valor_en_mxn': Decimal('18.000000'), 'fuente': 'BANXICO_FIX'})
        TipoCambio.objects.update_or_create(moneda_origen=self.moneda_eur, fecha=hoy, defaults={'valor_en_mxn': Decimal('20.000000'), 'fuente': 'BANXICO_FIX'})

        self.u_pza, _ = UnidadMedida.objects.get_or_create(codigo='PZA', defaults={'nombre': 'Pieza'})
        self.u_kg, _ = UnidadMedida.objects.get_or_create(codigo='KG', defaults={'nombre': 'Kilogramo'})
        self.bodega, _ = Bodega.objects.get_or_create(codigo='BOD-TEST', defaults={'nombre': 'Almacén Prueba'})

        from compras.models import Proveedor
        self.proveedor = Proveedor.objects.create(rfc='PRV990101AA1', razon_social='Proveedor Internacional Metal')

    def test_costeo_insumo_comprado_en_usd_se_convierte_a_mxn(self):
        """Un insumo con costo promedio 0 pero comprado en USD se convierte a MXN usando el TC vigente."""
        # 1. Crear producto con costo 0 en USD
        tapa_usd = Producto.objects.create(
            sku='CP-TAPA-USD',
            nombre='Tapa Metálica 18L Importada',
            tipo=Producto.COMPONENTE,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_usd,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('0.000000')
        )

        # 2. Registrar Orden de Compra en USD con precio $0.25 USD
        from compras.models import OrdenCompra_Maestro, OrdenCompra_Detalle
        oc = OrdenCompra_Maestro.objects.create(
            folio='OC-TEST-USD',
            proveedor=self.proveedor,
            bodega_destino=self.bodega,
            moneda=self.moneda_usd,
            estado='BORRADOR'
        )
        OrdenCompra_Detalle.objects.create(
            orden=oc,
            producto=tapa_usd,
            cantidad=Decimal('1000.00'),
            precio_unitario=Decimal('0.250000')
        )

        # 3. Crear Producto Terminado y Receta
        envase_pt = Producto.objects.create(
            sku='PT-ENVASE-18L',
            nombre='Envase 18 Litros',
            tipo=Producto.PRODUCTO_TERMINADO,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn
        )
        bom = ListaMaterialesBOM.objects.create(
            producto_terminado=envase_pt,
            cantidad_base=Decimal('100.0000')
        )
        # 100 pzas requeridas con 0% merma
        insumo_bom = InsumoBOM.objects.create(
            bom=bom,
            materia_prima=tapa_usd,
            cantidad_requerida=Decimal('100.000000'),
            porcentaje_merma=Decimal('0.00')
        )

        # 4. Validar costo unitario efectivo: 0.25 USD * 18.00 MXN/USD = 4.50 MXN
        self.assertEqual(insumo_bom.costo_unitario_efectivo_mxn, Decimal('4.500000'))
        self.assertEqual(insumo_bom.costo_info['origen'], 'OC')
        self.assertEqual(insumo_bom.costo_info['moneda_origen'], 'USD')

        # Costo de línea: 100 * 4.50 = 450.00 MXN
        self.assertEqual(insumo_bom.costo_estimado_linea_mxn, Decimal('450.000000'))

        # Total BOM: 450.00 MXN, Costo Unitario BOM: 450 / 100 = 4.50 MXN
        self.assertEqual(bom.costo_estimado_total_mxn, Decimal('450.000000'))
        self.assertEqual(bom.costo_estimado_unitario_mxn, Decimal('4.500000'))

    def test_costeo_sub_ensamble_cascada(self):
        """Un producto intermedio (plantilla) sin stock en Kardex toma el costo de su sub-receta."""
        # 1. Materia prima base con costo en Kardex
        hojalata = Producto.objects.create(
            sku='MP-HOJ-PRUEBA',
            nombre='Hojalata Base',
            tipo=Producto.MATERIA_PRIMA,
            unidad_medida=self.u_kg,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('30.000000')
        )

        # 2. Sub-ensamble (Plantilla CC) con costo de almacén 0, pero con receta propia
        plantilla = Producto.objects.create(
            sku='CC-PLANTILLA-TEST',
            nombre='Plantilla Cortada',
            tipo=Producto.PLANTILLA,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('0.000000')
        )
        # Receta de la plantilla: Base 10 pzas requiere 2 kg hojalata (2 * 30 = 60 MXN -> 6 MXN/pza)
        bom_plantilla = ListaMaterialesBOM.objects.create(
            producto_terminado=plantilla,
            cantidad_base=Decimal('10.0000')
        )
        InsumoBOM.objects.create(
            bom=bom_plantilla,
            materia_prima=hojalata,
            cantidad_requerida=Decimal('2.000000'),
            porcentaje_merma=Decimal('0.00')
        )
        self.assertEqual(bom_plantilla.costo_estimado_unitario_mxn, Decimal('6.000000'))

        # 3. Producto Terminado que usa la plantilla
        bote = Producto.objects.create(
            sku='PT-BOTE-TEST',
            nombre='Bote Terminado Prueba',
            tipo=Producto.PRODUCTO_TERMINADO,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn
        )
        bom_bote = ListaMaterialesBOM.objects.create(
            producto_terminado=bote,
            cantidad_base=Decimal('1.0000')
        )
        insumo_bote = InsumoBOM.objects.create(
            bom=bom_bote,
            materia_prima=plantilla,
            cantidad_requerida=Decimal('1.000000'),
            porcentaje_merma=Decimal('0.00')
        )

        # Debe resolver el costo unitario de la plantilla a 6.00 MXN vía SUB_BOM
        self.assertEqual(insumo_bote.costo_unitario_efectivo_mxn, Decimal('6.000000'))
        self.assertEqual(insumo_bote.costo_info['origen'], 'SUB_BOM')
        self.assertEqual(bom_bote.costo_estimado_unitario_mxn, Decimal('6.000000'))


class ProduccionFase1TestCase(TestCase):
    """
    Pruebas unitarias e integrales para la Fase 1 del Módulo de Producción:
    1. Órdenes de Producción ligadas a Pedidos de Venta en firme.
    2. Explosión de materiales clasificada (Hojalata, MP química, Componentes).
    3. Inicialización y control de avance en líneas de ensamble (6 estaciones industriales).
    4. Vistas y endpoints HTMX.
    """
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='jefe_planta', password='password123')
        self.client.login(username='jefe_planta', password='password123')

        self.moneda_mxn = Moneda.objects.create(codigo='MXN', nombre='Peso Mexicano', simbolo='$')
        self.u_pza = UnidadMedida.objects.create(codigo='PZA', nombre='Pieza')
        self.u_kg = UnidadMedida.objects.create(codigo='KG', nombre='Kilogramo')
        self.u_lt = UnidadMedida.objects.create(codigo='LTS', nombre='Litro')

        self.bodega_mp = Bodega.objects.create(codigo='BOD-MP-01', nombre='Almacén Materia Prima y Componentes')
        self.bodega_pt = Bodega.objects.create(codigo='BOD-PT-01', nombre='Almacén Producto Terminado')

        # Familia 1: Hojalata
        self.hojalata = Producto.objects.create(
            sku='HR-BOB-01',
            nombre='Bobina Hojalata T4 0.22mm',
            tipo=Producto.HOJALATA_ROLLO,
            unidad_medida=self.u_kg,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('30.000000')
        )
        self.plantilla = Producto.objects.create(
            sku='CC-PLANT-01',
            nombre='Plantilla Cuerpo Litografiado',
            tipo=Producto.PLANTILLA,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('5.000000')
        )

        # Familia 2: Materia Prima Química
        self.barniz = Producto.objects.create(
            sku='MP-BAR-EPO',
            nombre='Barniz Epoxifenólico Sanitario',
            tipo=Producto.MATERIA_PRIMA,
            unidad_medida=self.u_lt,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('120.000000')
        )

        # Familia 3: Componentes Metálicos
        self.valvula = Producto.objects.create(
            sku='CP-VAL-01',
            nombre='Válvula Aerosol 1 Pulgada',
            tipo=Producto.COMPONENTE,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('1.800000')
        )
        self.fondo = Producto.objects.create(
            sku='CP-FON-01',
            nombre='Fondo Domo Estándar',
            tipo=Producto.COMPONENTE,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('0.900000')
        )

        # Producto Terminado
        self.bote_aerosol = Producto.objects.create(
            sku='PT-AER-200',
            nombre='Bote Aerosol Industrial 200ml',
            tipo=Producto.PRODUCTO_TERMINADO,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('0.000000')
        )

        # Receta BOM con las 3 familias de insumos
        self.bom = ListaMaterialesBOM.objects.create(
            producto_terminado=self.bote_aerosol,
            cantidad_base=Decimal('1000.0000'),
            descripcion_proceso='Corte primario, barnizado, soldadura y ensamble'
        )
        InsumoBOM.objects.create(
            bom=self.bom, materia_prima=self.hojalata,
            cantidad_requerida=Decimal('40.000000'), porcentaje_merma=Decimal('2.50')
        )
        InsumoBOM.objects.create(
            bom=self.bom, materia_prima=self.plantilla,
            cantidad_requerida=Decimal('1000.000000'), porcentaje_merma=Decimal('1.00')
        )
        InsumoBOM.objects.create(
            bom=self.bom, materia_prima=self.barniz,
            cantidad_requerida=Decimal('5.000000'), porcentaje_merma=Decimal('5.00')
        )
        InsumoBOM.objects.create(
            bom=self.bom, materia_prima=self.valvula,
            cantidad_requerida=Decimal('1000.000000'), porcentaje_merma=Decimal('0.50')
        )
        InsumoBOM.objects.create(
            bom=self.bom, materia_prima=self.fondo,
            cantidad_requerida=Decimal('1000.000000'), porcentaje_merma=Decimal('0.50')
        )

        # Cliente y Pedido de Venta en Firme
        self.cliente = Cliente.objects.create(
            razon_social='Pinturas Industriales S.A. de C.V.',
            nombre_comercial='Pinturas Ind',
            rfc='PIN950412AA1',
            codigo_postal='06700',
            correo='facturas@pinturasind.mx',
            dias_credito=30
        )
        self.pedido = PedidoVenta_Maestro.objects.create(
            folio='PED-2026-9001',
            tipo_documento=PedidoVenta_Maestro.PEDIDO,
            cliente=self.cliente,
            bodega_despacho=self.bodega_pt,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.CONFIRMADO,
            subtotal=Decimal('25000.0000'),
            impuestos=Decimal('4000.0000'),
            total=Decimal('29000.0000'),
            total_mxn=Decimal('29000.0000')
        )
        self.partida = PedidoVenta_Detalle.objects.create(
            pedido=self.pedido,
            producto=self.bote_aerosol,
            cantidad=Decimal('2000.000000'),
            precio_unitario=Decimal('12.500000')
        )

    def test_creacion_op_desde_pedido_en_firme(self):
        """Valida que una OP se genere correctamente ligada a una partida de pedido de venta."""
        orden = crear_orden_desde_pedido(
            pedido_detalle=self.partida,
            usuario=self.user,
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id
        )

        self.assertIsNotNone(orden)
        self.assertTrue(orden.folio.startswith('OP-'))
        self.assertEqual(orden.pedido_venta, self.pedido)
        self.assertEqual(orden.pedido_detalle, self.partida)
        self.assertEqual(orden.cantidad_a_producir, Decimal('2000.0000'))
        self.assertEqual(orden.bodega_origen_insumos, self.bodega_mp)
        self.assertEqual(orden.bodega_destino_pt, self.bodega_pt)
        self.assertEqual(orden.estado, OrdenProduccion.PLANEADA)

        # Relación inversa bidireccional
        self.assertEqual(self.pedido.ordenes_produccion.count(), 1)
        self.assertEqual(self.partida.ordenes_produccion.first(), orden)

        # Insumos congelados para 2,000 botes (factor = 2)
        self.assertEqual(orden.insumos_detalle.count(), 5)

        # Validación: error si el pedido está cancelado
        self.pedido.estado = PedidoVenta_Maestro.CANCELADO
        self.pedido.save()
        with self.assertRaises(ValueError):
            crear_orden_desde_pedido(self.partida, self.user)

    def test_explosion_materiales_bom_categorizada(self):
        """Verifica la explosión clasificada en las 3 familias industriales con scrap y stock."""
        # Cargar existencia física solo para la hojalata
        Stock.objects.create(
            producto=self.hojalata,
            bodega=self.bodega_mp,
            cantidad=Decimal('100.000000'),
            cantidad_reservada=Decimal('10.000000')
        )

        explosion = explosion_materiales_bom(
            producto=self.bote_aerosol,
            cantidad=Decimal('1000.0000'),
            bodega_origen=self.bodega_mp
        )

        self.assertTrue(explosion['tiene_bom'])
        self.assertEqual(len(explosion['hojalata']), 2)       # hojalata rollo + plantilla
        self.assertEqual(len(explosion['materia_prima']), 1)  # barniz
        self.assertEqual(len(explosion['componentes']), 2)    # valvula + fondo
        self.assertEqual(len(explosion['todos_insumos']), 5)

        # Comprobar cálculo de merma y stock en Hojalata
        item_hoj = next(item for item in explosion['hojalata'] if item['producto'] == self.hojalata)
        # 40 kg + 2.5% merma = 41 kg
        self.assertEqual(item_hoj['cantidad_requerida'], Decimal('41.000000'))
        self.assertEqual(item_hoj['stock_disponible'], Decimal('90.000000'))
        self.assertTrue(item_hoj['suficiente'])

        # Comprobar que hay insumos faltantes en total
        self.assertFalse(explosion['totales']['todos_disponibles'])
        self.assertGreater(explosion['totales']['items_faltantes'], 0)
        self.assertGreater(explosion['totales']['costo_total_mxn'], Decimal('0'))
        self.assertGreater(explosion['totales']['costo_unitario_mxn'], Decimal('0'))

    def test_inicializacion_y_avance_ensamble_6_estaciones(self):
        """
        Verifica que una OP inicialice sus 6 estaciones industriales y que
        las piezas conformes se transfieran automáticamente a la siguiente estación.
        """
        orden = crear_orden_produccion(
            producto_id=self.bote_aerosol.id,
            cantidad=Decimal('1000.0000'),
            bodega_origen_id=self.bodega_mp.id,
            bodega_destino_id=self.bodega_pt.id,
            usuario=self.user
        )

        etapas = list(orden.etapas_ensamble.all())
        self.assertEqual(len(etapas), 6)
        self.assertEqual(orden.etapas_total_count, 6)
        self.assertEqual(orden.etapas_completadas_count, 0)

        # Estación 1 arranca con la cantidad solicitada
        etapa_1 = etapas[0]
        self.assertEqual(etapa_1.codigo_etapa, EtapaProduccionOP.CORTE_HOJA)
        self.assertEqual(etapa_1.cantidad_entrada, Decimal('1000.0000'))
        self.assertEqual(etapa_1.estado, EtapaProduccionOP.ESTADO_PENDIENTE)

        # Avanzar Estación 1: 980 conformes, 20 scrap -> COMPLETADA
        etapa_1_up = actualizar_avance_etapa_ensamble(
            etapa_id=etapa_1.id,
            cantidad_buena=Decimal('980.0000'),
            cantidad_scrap=Decimal('20.0000'),
            nuevo_estado=EtapaProduccionOP.ESTADO_COMPLETADA,
            operador="Roberto Cizallador",
            notas="Cizallado de bobinas T4 sin desajustes"
        )
        self.assertEqual(etapa_1_up.estado, EtapaProduccionOP.ESTADO_COMPLETADA)
        self.assertIsNotNone(etapa_1_up.fecha_fin)

        # Cascada: Estación 2 (Litografía) ahora debe tener cantidad_entrada = 980
        etapa_2 = orden.etapas_ensamble.get(secuencia=2)
        self.assertEqual(etapa_2.codigo_etapa, EtapaProduccionOP.LITOGRAFIA)
        self.assertEqual(etapa_2.cantidad_entrada, Decimal('980.0000'))

        # Orden transicionó a EN_PROCESO
        orden.refresh_from_db()
        self.assertEqual(orden.estado, OrdenProduccion.EN_PROCESO)

        # Avanzar Estaciones 2, 3, 4, 5 secuencialmente
        for sec in [2, 3, 4, 5]:
            et = orden.etapas_ensamble.get(secuencia=sec)
            actualizar_avance_etapa_ensamble(
                etapa_id=et.id,
                cantidad_buena=Decimal('970.0000'),
                cantidad_scrap=Decimal('10.0000'),
                nuevo_estado=EtapaProduccionOP.ESTADO_COMPLETADA,
                operador="Líder Línea"
            )

        # Última Estación 6: Prueba de Hermeticidad y Paletizado
        etapa_6 = orden.etapas_ensamble.get(secuencia=6)
        self.assertEqual(etapa_6.codigo_etapa, EtapaProduccionOP.PRUEBA_EMPAQUE)
        self.assertEqual(etapa_6.cantidad_entrada, Decimal('970.0000'))

        actualizar_avance_etapa_ensamble(
            etapa_id=etapa_6.id,
            cantidad_buena=Decimal('965.0000'),
            cantidad_scrap=Decimal('5.0000'),
            nuevo_estado=EtapaProduccionOP.ESTADO_COMPLETADA,
            operador="Control de Calidad"
        )

        orden.refresh_from_db()
        self.assertEqual(orden.etapas_completadas_count, 6)
        self.assertEqual(orden.cantidad_producida, Decimal('965.0000'))
        self.assertGreater(orden.porcentaje_avance_ensamble, Decimal('90.0'))

    def test_vistas_fase1_integracion(self):
        """Comprueba los endpoints de interfaz HTMX y vistas de Fase 1."""
        # 1. GET modal crear OP desde pedido
        url_modal_pedido = reverse('produccion:crear_op_desde_pedido', kwargs={'detalle_id': self.partida.id})
        resp = self.client.get(url_modal_pedido)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Lanzar Orden de Producción')
        self.assertContains(resp, self.bote_aerosol.sku)

        # 2. POST crear OP desde pedido
        resp_post = self.client.post(url_modal_pedido, {
            'cantidad': '2000.0000',
            'bodega_origen_insumos': self.bodega_mp.id,
            'bodega_destino_pt': self.bodega_pt.id,
            'observaciones': 'Lanzamiento para cliente'
        })
        self.assertEqual(resp_post.status_code, 302)
        op_creada = OrdenProduccion.objects.filter(pedido_detalle=self.partida).first()
        self.assertIsNotNone(op_creada)

        # 3. Vista detalle de orden contiene la información de pedido vinculado y etapas
        url_detalle_op = reverse('produccion:detalle_orden', kwargs={'pk': op_creada.id})
        resp_det = self.client.get(url_detalle_op)
        self.assertEqual(resp_det.status_code, 200)
        self.assertContains(resp_det, 'Pedido de Venta Vinculado')
        self.assertContains(resp_det, self.pedido.folio)
        self.assertContains(resp_det, 'Control de Avance en Línea de Ensamble')

        # 4. POST HTMX actualizar etapa
        etapa_1 = op_creada.etapas_ensamble.first()
        url_up_etapa = reverse('produccion:actualizar_etapa_avance', kwargs={'pk': etapa_1.id})
        resp_htmx = self.client.post(url_up_etapa, {
            'cantidad_buena': '1950',
            'cantidad_scrap': '50',
            'nuevo_estado': 'COMPLETADA',
            'operador': 'Operador HTMX'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(resp_htmx.status_code, 200)
        self.assertContains(resp_htmx, 'Operador HTMX')

