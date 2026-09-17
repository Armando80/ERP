# ERP Decorlata - inventario/tests.py
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.exceptions import ValidationError, PermissionDenied

from general.models import Moneda
from .models import UnidadMedida, Bodega, Producto, Stock, MovimientoInventario, SolicitudAnulacionMovimiento
from .services import (
    solicitar_anulacion_movimiento,
    autorizar_anulacion_movimiento,
    rechazar_anulacion_movimiento
)


class AnulacionKardexTestCase(TestCase):
    def setUp(self):
        # Usuarios
        self.operador = User.objects.create_user(username='operador1', password='password123')
        self.admin_user = User.objects.create_user(username='admin_almacen', password='password123', is_staff=True)

        # Moneda y unidades
        self.moneda_mxn = Moneda.objects.create(codigo='MXN', nombre='Peso Mexicano', simbolo='$')
        self.u_pza = UnidadMedida.objects.create(codigo='PZA', nombre='Pieza')

        # Bodegas
        self.bodega_principal = Bodega.objects.create(codigo='BOD-PRI', nombre='Almacén Central')
        self.bodega_secundaria = Bodega.objects.create(codigo='BOD-SEC', nombre='Almacén de Tránsito')

        # Producto
        self.producto = Producto.objects.create(
            sku='CP-VAL-01',
            nombre='Válvula de Aerosol 1 Pulgada',
            tipo=Producto.COMPONENTE,
            unidad_medida=self.u_pza,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('1.500000')
        )

    def test_solicitar_anulacion_pendiente_no_altera_stock_ni_kardex(self):
        """
        Verifica que al solicitar la anulación de un movimiento:
        1. Se cree la SolicitudAnulacionMovimiento en estado PENDIENTE.
        2. El movimiento original NO se marque como anulado todavía.
        3. El stock físico permanezca inalterado.
        4. No se permita duplicar solicitudes sobre el mismo movimiento.
        """
        # Crear movimiento de Entrada
        mov = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('500.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            referencia_operacion='FAC-1001',
            usuario=self.operador
        )

        stock = Stock.objects.get(producto=self.producto, bodega=self.bodega_principal)
        self.assertEqual(stock.cantidad, Decimal('500.000000'))

        # Operador solicita anulación
        solicitud = solicitar_anulacion_movimiento(
            movimiento_id=mov.id,
            usuario_solicita=self.operador,
            motivo="Error en la factura de proveedor, cantidad incorrecta"
        )

        self.assertEqual(solicitud.estado, SolicitudAnulacionMovimiento.PENDIENTE)
        self.assertEqual(solicitud.usuario_solicita, self.operador)
        self.assertTrue(solicitud.folio.startswith('ANUL-'))

        # Estado del movimiento: NO anulado aún
        mov.refresh_from_db()
        self.assertFalse(mov.es_anulado)
        self.assertFalse(mov.es_contraasiento)
        self.assertTrue(mov.tiene_solicitud_pendiente)

        # Stock físico intacto
        stock.refresh_from_db()
        self.assertEqual(stock.cantidad, Decimal('500.000000'))

        # Intentar duplicar solicitud sobre el mismo movimiento debe fallar
        with self.assertRaises(ValueError):
            solicitar_anulacion_movimiento(mov.id, self.operador, "Otro motivo")

    def test_autorizar_anulacion_entrada_genera_contraasiento_y_descuenta_stock(self):
        """
        Verifica que cuando un Administrador autoriza la anulación de una ENTRADA:
        1. Se genere un contra-asiento de tipo SALIDA por la misma cantidad.
        2. Se descuente el stock de la bodega destino.
        3. El movimiento original quede es_anulado=True y enlazado con su contra-asiento.
        4. La solicitud quede en estado APROBADA.
        """
        mov = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('300.000000'),
            costo_unitario_original=Decimal('2.000000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('2.000000'),
            usuario=self.operador
        )

        solicitud = solicitar_anulacion_movimiento(
            movimiento_id=mov.id,
            usuario_solicita=self.operador,
            motivo="Entrada capturada por error"
        )

        # Administrador autoriza
        sol_aprobada, contraasiento = autorizar_anulacion_movimiento(
            solicitud_id=solicitud.id,
            usuario_admin=self.admin_user,
            notas_admin="Aprobado. Se procede a regularizar el stock."
        )

        self.assertEqual(sol_aprobada.estado, SolicitudAnulacionMovimiento.APROBADA)
        self.assertEqual(sol_aprobada.usuario_autoriza, self.admin_user)
        self.assertIsNotNone(sol_aprobada.fecha_resolucion)

        # Verificar Contra-asiento
        self.assertTrue(contraasiento.es_contraasiento)
        self.assertEqual(contraasiento.tipo_movimiento, MovimientoInventario.SALIDA)
        self.assertEqual(contraasiento.bodega_origen, self.bodega_principal)
        self.assertEqual(contraasiento.cantidad, Decimal('300.000000'))
        self.assertEqual(contraasiento.movimiento_relacionado, mov)
        self.assertTrue(contraasiento.referencia_operacion.startswith('ANUL-'))

        # Movimiento original
        mov.refresh_from_db()
        self.assertTrue(mov.es_anulado)
        self.assertEqual(mov.movimiento_relacionado, contraasiento)

        # Stock físico: 300 iniciales - 300 revertidos = 0
        stock = Stock.objects.get(producto=self.producto, bodega=self.bodega_principal)
        self.assertEqual(stock.cantidad, Decimal('0.000000'))

    def test_anulacion_entrada_bloqueada_por_stock_insuficiente(self):
        """
        Verifica la regla crítica de prevención de stock negativo:
        Si se dio entrada a 100 pzas, pero ya se consumieron/despacharon 80 (quedan 20),
        la anulación de la entrada de 100 debe ser bloqueada por el sistema.
        """
        mov_entrada = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('100.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        # Salida posterior de 80 piezas
        MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_origen=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.SALIDA,
            cantidad=Decimal('80.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        stock = Stock.objects.get(producto=self.producto, bodega=self.bodega_principal)
        self.assertEqual(stock.cantidad, Decimal('20.000000'))

        # Solicitar anulación de la entrada original de 100
        solicitud = solicitar_anulacion_movimiento(
            movimiento_id=mov_entrada.id,
            usuario_solicita=self.operador,
            motivo="Intento de anulación de entrada previa"
        )

        # El Administrador intenta autorizar pero debe ser rechazado por falta de stock
        with self.assertRaises(ValidationError) as ctx:
            autorizar_anulacion_movimiento(solicitud.id, self.admin_user)

        self.assertIn("insuficiente", str(ctx.exception).lower())

        # Verificar que el stock sigue en 20 y el movimiento no se anuló
        stock.refresh_from_db()
        self.assertEqual(stock.cantidad, Decimal('20.000000'))
        mov_entrada.refresh_from_db()
        self.assertFalse(mov_entrada.es_anulado)

    def test_autorizar_anulacion_salida_reintegra_stock(self):
        """
        Verifica que al anular una SALIDA:
        1. Se genere un contra-asiento de tipo ENTRADA.
        2. El stock se reintegre a la bodega de origen.
        """
        # Carga inicial: 100 piezas
        MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('100.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        # Salida por consumo: 40 piezas (quedan 60)
        mov_salida = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_origen=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.SALIDA,
            cantidad=Decimal('40.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        stock = Stock.objects.get(producto=self.producto, bodega=self.bodega_principal)
        self.assertEqual(stock.cantidad, Decimal('60.000000'))

        # Solicitar y autorizar anulación de la salida
        solicitud = solicitar_anulacion_movimiento(mov_salida.id, self.operador, "Salida duplicada por error")
        _, contraasiento = autorizar_anulacion_movimiento(solicitud.id, self.admin_user)

        self.assertEqual(contraasiento.tipo_movimiento, MovimientoInventario.ENTRADA)
        self.assertEqual(contraasiento.bodega_destino, self.bodega_principal)
        self.assertEqual(contraasiento.cantidad, Decimal('40.000000'))

        # Stock vuelve a 100
        stock.refresh_from_db()
        self.assertEqual(stock.cantidad, Decimal('100.000000'))

    def test_autorizar_anulacion_transferencia_reversa_ambas_bodegas(self):
        """
        Verifica que al anular una TRANSFERENCIA entre bodegas:
        1. Se genere una transferencia inversa (Destino -> Origen).
        2. Se retiren las existencias del destino y se reingresen al origen.
        """
        # Carga en Bodega Principal: 50 piezas
        MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('50.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        # Transferencia: 20 piezas de Principal a Secundaria
        mov_transf = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_origen=self.bodega_principal,
            bodega_destino=self.bodega_secundaria,
            tipo_movimiento=MovimientoInventario.TRANSFERENCIA,
            cantidad=Decimal('20.000000'),
            costo_unitario_original=Decimal('1.500000'),
            moneda_original=self.moneda_mxn,
            costo_unitario_mxn_capturado=Decimal('1.500000'),
            usuario=self.operador
        )

        stock_pri = Stock.objects.get(producto=self.producto, bodega=self.bodega_principal)
        stock_sec = Stock.objects.get(producto=self.producto, bodega=self.bodega_secundaria)
        self.assertEqual(stock_pri.cantidad, Decimal('30.000000'))
        self.assertEqual(stock_sec.cantidad, Decimal('20.000000'))

        # Anulación autorizada de la transferencia
        solicitud = solicitar_anulacion_movimiento(mov_transf.id, self.operador, "Traspaso enviado a bodega equivocada")
        _, contraasiento = autorizar_anulacion_movimiento(solicitud.id, self.admin_user)

        self.assertEqual(contraasiento.tipo_movimiento, MovimientoInventario.TRANSFERENCIA)
        self.assertEqual(contraasiento.bodega_origen, self.bodega_secundaria)
        self.assertEqual(contraasiento.bodega_destino, self.bodega_principal)

        # Bodega Principal recupera las 20 piezas (vuelve a 50) y Secundaria queda en 0
        stock_pri.refresh_from_db()
        stock_sec.refresh_from_db()
        self.assertEqual(stock_pri.cantidad, Decimal('50.000000'))
        self.assertEqual(stock_sec.cantidad, Decimal('0.000000'))

    def test_rechazo_anulacion_mantiene_movimiento_activo(self):
        """
        Verifica que al rechazar una solicitud de anulación:
        1. Quede en estado RECHAZADA con el motivo del administrador.
        2. El movimiento continúe activo sin alteraciones.
        """
        mov = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('100.000000'),
            usuario=self.operador
        )

        solicitud = solicitar_anulacion_movimiento(mov.id, self.operador, "Solicito anular")
        sol_rechazada = rechazar_anulacion_movimiento(
            solicitud_id=solicitud.id,
            usuario_admin=self.admin_user,
            motivo_rechazo="El movimiento corresponde a mercancía física ya ingresada y validada"
        )

        self.assertEqual(sol_rechazada.estado, SolicitudAnulacionMovimiento.RECHAZADA)
        self.assertEqual(sol_rechazada.usuario_autoriza, self.admin_user)
        self.assertIn("mercancía física", sol_rechazada.notas_administrador)

        mov.refresh_from_db()
        self.assertFalse(mov.es_anulado)
        self.assertFalse(mov.tiene_solicitud_pendiente)

    def test_seguridad_usuario_no_admin_no_puede_autorizar(self):
        """
        Verifica que un usuario sin privilegios de administrador reciba PermissionDenied / 403 Forbidden.
        """
        mov = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('10.000000'),
            usuario=self.operador
        )
        solicitud = solicitar_anulacion_movimiento(mov.id, self.operador, "Motivo de prueba")

        # Llamada de servicio directa con usuario normal debe lanzar PermissionDenied
        with self.assertRaises(PermissionDenied):
            autorizar_anulacion_movimiento(solicitud.id, self.operador)

        with self.assertRaises(PermissionDenied):
            rechazar_anulacion_movimiento(solicitud.id, self.operador, "Motivo")

        # Petición HTTP con cliente autenticado como operador (no admin)
        client = Client()
        client.force_login(self.operador)

        resp_auth = client.post(reverse('inventario:autorizar_anulacion', args=[solicitud.id]))
        self.assertEqual(resp_auth.status_code, 403)

        resp_rec = client.post(reverse('inventario:rechazar_anulacion', args=[solicitud.id]))
        self.assertEqual(resp_rec.status_code, 403)

    def test_endpoints_vistas_htmx_anulacion(self):
        """
        Prueba los endpoints HTTP y HTMX del flujo completo de anulación:
        1. Carga del modal de solicitud.
        2. Envío del formulario de solicitud (dispara HX-Trigger).
        3. Visualización de bandeja de solicitudes pendientes.
        4. Autorización por parte del administrador.
        """
        client = Client()
        client.force_login(self.operador)

        mov = MovimientoInventario.objects.create(
            producto=self.producto,
            bodega_destino=self.bodega_principal,
            tipo_movimiento=MovimientoInventario.ENTRADA,
            cantidad=Decimal('200.000000'),
            usuario=self.operador
        )

        # 1. Cargar Modal de Solicitud
        resp_modal = client.get(reverse('inventario:solicitar_anulacion_modal', args=[mov.id]))
        self.assertEqual(resp_modal.status_code, 200)
        self.assertContains(resp_modal, 'Solicitar Anulación de Movimiento')
        self.assertContains(resp_modal, self.producto.nombre)

        # 2. Enviar Solicitud POST
        resp_enviar = client.post(reverse('inventario:enviar_solicitud_anulacion', args=[mov.id]), {
            'motivo_solicitud': 'Error en la orden de compra capturada'
        })
        self.assertEqual(resp_enviar.status_code, 200)
        self.assertEqual(resp_enviar.headers.get('HX-Trigger'), 'anulacionSolicitada')

        solicitud = SolicitudAnulacionMovimiento.objects.get(movimiento=mov)
        self.assertEqual(solicitud.estado, SolicitudAnulacionMovimiento.PENDIENTE)

        # 3. Consultar Bandeja de Solicitudes (cambiar a admin)
        client.force_login(self.admin_user)
        resp_bandeja = client.get(reverse('inventario:solicitudes_anulacion'))
        self.assertEqual(resp_bandeja.status_code, 200)
        self.assertContains(resp_bandeja, solicitud.folio)
        self.assertContains(resp_bandeja, 'Disponible: 200.00 (Suficiente)')

        # 4. Autorizar vía POST por el Administrador
        resp_auth = client.post(reverse('inventario:autorizar_anulacion', args=[solicitud.id]), {
            'notas_administrador': 'Validado y autorizado por jefe de almacén'
        })
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.headers.get('HX-Trigger'), 'anulacionProcesada')

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudAnulacionMovimiento.APROBADA)
        mov.refresh_from_db()
        self.assertTrue(mov.es_anulado)
