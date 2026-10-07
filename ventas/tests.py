from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .models import (
    Cliente, PedidoVenta_Maestro, PedidoVenta_Detalle,
    Factura_Maestro, Factura_Detalle, ComplementoPago_Detalle
)
from .forms import ClienteForm
from .services import (
    generar_folio_venta,
    convertir_cotizacion_a_pedido,
    verificar_disponibilidad_stock,
    reservar_stock_pedido,
    liberar_reserva_stock_pedido,
    surtir_pedido,
    numero_a_letras,
)
from .cfdi_services import (
    emitir_factura_desde_pedido,
    registrar_complemento_pago,
    generar_qr_sat_base64,
    construir_xml_cfdi40,
)
from general.models import Moneda
from inventario.models import Bodega, Producto, UnidadMedida, Stock, MovimientoInventario



class ClienteModelTestCase(TestCase):
    def test_creacion_cliente_moral_rfc_12(self):
        """Verifica la creación de un cliente Persona Moral con RFC de 12 posiciones."""
        cliente = Cliente(
            rfc='dec010203ab1',
            razon_social='decoraciones y latas industriales sa de cv',
            nombre_comercial='Decorlata Clientes',
            regimen_fiscal='601',
            codigo_postal='06000',
            correo='facturas@decorlata.com'
        )
        cliente.full_clean()
        cliente.save()

        cliente.refresh_from_db()
        self.assertEqual(cliente.rfc, 'DEC010203AB1')
        self.assertEqual(cliente.razon_social, 'DECORACIONES Y LATAS INDUSTRIALES SA DE CV')
        self.assertTrue(cliente.activo)
        self.assertEqual(cliente.dias_credito, 0)
        self.assertIn('DEC010203AB1', str(cliente))

    def test_creacion_cliente_fisica_rfc_13(self):
        """Verifica la creación de un cliente Persona Física con RFC de 13 posiciones."""
        cliente = Cliente(
            rfc='gocj850101h23',
            razon_social='juan gonzalez cruz',
            regimen_fiscal='612',
            codigo_postal='64000',
            correo='juan@ejemplo.com',
            dias_credito=15,
            limite_credito=Decimal('50000.00')
        )
        cliente.full_clean()
        cliente.save()

        cliente.refresh_from_db()
        self.assertEqual(cliente.rfc, 'GOCJ850101H23')
        self.assertEqual(cliente.dias_credito, 15)
        self.assertEqual(cliente.limite_credito, Decimal('50000.00'))

    def test_validacion_rfc_longitud_invalida(self):
        """Un RFC con longitud distinta a 12 o 13 debe arrojar ValidationError."""
        cliente = Cliente(
            rfc='INVALID',
            razon_social='EMPRESA INVALIDA',
            regimen_fiscal='601',
            codigo_postal='06000',
            correo='test@invalido.com'
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_validacion_codigo_postal_invalido(self):
        """Un Código Postal que no tenga exactamente 5 dígitos debe arrojar ValidationError."""
        cliente = Cliente(
            rfc='DEC010203AB1',
            razon_social='EMPRESA PRUEBA',
            regimen_fiscal='601',
            codigo_postal='060',  # Solo 3 dígitos
            correo='test@invalido.com'
        )
        with self.assertRaises(ValidationError):
            cliente.full_clean()


class ClienteViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='vendedor1', password='password123')
        self.client_http = Client()
        self.client_http.force_login(self.user)

        self.cliente_activo = Cliente.objects.create(
            rfc='DEC010203AB1',
            razon_social='AEROSOLES DECORATIVOS SA DE CV',
            nombre_comercial='Aerosoles Decora',
            regimen_fiscal='601',
            codigo_postal='06000',
            nombre_contacto='Rodrigo Salinas',
            correo='contacto@decora.com',
            telefono='5512345678',
            activo=True
        )
        self.cliente_inactivo = Cliente.objects.create(
            rfc='INAC990101XYZ',
            razon_social='DISTRIBUIDORA INACTIVA SA DE CV',
            nombre_comercial='Inactiva MX',
            regimen_fiscal='601',
            codigo_postal='64000',
            correo='baja@inactiva.com',
            activo=False
        )

    def test_catalogo_clientes_vista_estandar(self):
        """Verifica que la vista devuelva la página completa de catálogo."""
        response = self.client_http.get(reverse('ventas:clientes_catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Directorio de Clientes')
        self.assertContains(response, 'AEROSOLES DECORATIVOS SA DE CV')
        self.assertContains(response, 'DISTRIBUIDORA INACTIVA SA DE CV')

    def test_catalogo_clientes_vista_htmx(self):
        """Verifica que con encabezado HX-Request devuelva solo el fragmento de la tabla."""
        response = self.client_http.get(reverse('ventas:clientes_catalogo'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'ventas/partials/_tabla_clientes.html')
        self.assertNotContains(response, '<html')

    def test_catalogo_clientes_busqueda_filtrada(self):
        """Verifica el filtrado por texto (RFC / Razón Social / Nombre Comercial)."""
        response = self.client_http.get(reverse('ventas:clientes_catalogo') + '?q=Decora', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'DEC010203AB1')
        self.assertNotContains(response, 'INAC990101XYZ')

    def test_catalogo_clientes_filtro_estado(self):
        """Verifica el filtrado por clientes activos e inactivos."""
        resp_activos = self.client_http.get(reverse('ventas:clientes_catalogo') + '?estado=activos', HTTP_HX_REQUEST='true')
        self.assertContains(resp_activos, 'DEC010203AB1')
        self.assertNotContains(resp_activos, 'INAC990101XYZ')

        resp_inactivos = self.client_http.get(reverse('ventas:clientes_catalogo') + '?estado=inactivos', HTTP_HX_REQUEST='true')
        self.assertNotContains(resp_inactivos, 'DEC010203AB1')
        self.assertContains(resp_inactivos, 'INAC990101XYZ')

    def test_crear_cliente_via_htmx(self):
        """Verifica el alta exitosa de un cliente mediante POST en el Offcanvas con HX-Trigger."""
        payload = {
            'rfc': 'NOVA010203CD4',
            'razon_social': 'INDUSTRIAS QUIMICAS NOVA SA DE CV',
            'nombre_comercial': 'Nova Quimica',
            'regimen_fiscal': '601',
            'codigo_postal': '03100',
            'uso_cfdi': 'G01',
            'correo': 'facturacion@nova.com',
            'telefono': '5598765432',
            'direccion': 'Av. Principal 123, Benito Juárez, CDMX',
            'dias_credito': 30,
            'limite_credito': '150000.00',
            'activo': 'on'
        }
        response = self.client_http.post(reverse('ventas:cliente_crear'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('HX-Trigger'), 'clienteGuardado')

        nuevo = Cliente.objects.filter(rfc='NOVA010203CD4').first()
        self.assertIsNotNone(nuevo)
        self.assertEqual(nuevo.dias_credito, 30)
        self.assertEqual(nuevo.limite_credito, Decimal('150000.00'))

    def test_editar_cliente_via_htmx(self):
        """Verifica la edición de un cliente existente."""
        payload = {
            'rfc': self.cliente_activo.rfc,
            'razon_social': 'AEROSOLES DECORATIVOS RENOVADOS SA DE CV',
            'nombre_comercial': 'Aerosoles Decora Plus',
            'regimen_fiscal': '601',
            'codigo_postal': '06000',
            'uso_cfdi': 'G03',
            'correo': 'nuevo_contacto@decora.com',
            'dias_credito': 45,
            'limite_credito': '200000.00',
            'activo': 'on'
        }
        response = self.client_http.post(reverse('ventas:cliente_editar', args=[self.cliente_activo.id]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('HX-Trigger'), 'clienteGuardado')

        self.cliente_activo.refresh_from_db()
        self.assertEqual(self.cliente_activo.razon_social, 'AEROSOLES DECORATIVOS RENOVADOS SA DE CV')
        self.assertEqual(self.cliente_activo.dias_credito, 45)

    def test_modal_detalle_cliente(self):
        """Verifica la carga del modal con la ficha del cliente."""
        response = self.client_http.get(reverse('ventas:cliente_detalle_modal', args=[self.cliente_activo.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.cliente_activo.razon_social)
        self.assertContains(response, self.cliente_activo.rfc)

    def test_crear_cliente_con_nombre_contacto(self):
        """El contacto se guarda normalizado (espacios) desde el Offcanvas HTMX."""
        payload = {
            'rfc': 'CONT010203AB5',
            'razon_social': 'LACAS Y RECUBRIMIENTOS SA DE CV',
            'regimen_fiscal': '601',
            'codigo_postal': '44100',
            'uso_cfdi': 'G01',
            'nombre_contacto': '  Ing.   Laura   Martínez ',
            'correo': 'compras@lacas.com',
            'dias_credito': 0,
            'limite_credito': '0.00',
            'activo': 'on'
        }
        response = self.client_http.post(reverse('ventas:cliente_crear'), payload)
        self.assertEqual(response.headers.get('HX-Trigger'), 'clienteGuardado')
        nuevo = Cliente.objects.get(rfc='CONT010203AB5')
        self.assertEqual(nuevo.nombre_contacto, 'Ing. Laura Martínez')

    def test_cliente_sin_contacto_es_valido(self):
        """El contacto es opcional: si se omite se guarda como NULL."""
        payload = {
            'rfc': 'SINC010203AB6',
            'razon_social': 'SIN CONTACTO SA DE CV',
            'regimen_fiscal': '601',
            'codigo_postal': '44100',
            'uso_cfdi': 'G03',
            'nombre_contacto': '   ',
            'correo': 'x@sincontacto.com',
            'dias_credito': 0,
            'limite_credito': '0.00',
        }
        self.client_http.post(reverse('ventas:cliente_crear'), payload)
        self.assertIsNone(Cliente.objects.get(rfc='SINC010203AB6').nombre_contacto)

    def test_busqueda_por_nombre_contacto(self):
        """El buscador HTMX encuentra clientes por el nombre de su contacto."""
        response = self.client_http.get(reverse('ventas:clientes_catalogo') + '?q=Rodrigo', HTTP_HX_REQUEST='true')
        self.assertContains(response, 'DEC010203AB1')
        self.assertContains(response, 'Rodrigo Salinas')
        self.assertNotContains(response, 'INAC990101XYZ')

    def test_modal_detalle_muestra_contacto(self):
        """La ficha del cliente incluye la persona de contacto."""
        response = self.client_http.get(reverse('ventas:cliente_detalle_modal', args=[self.cliente_activo.id]))
        self.assertContains(response, 'Persona de Contacto')
        self.assertContains(response, 'Rodrigo Salinas')

    def test_toggle_activo_cliente(self):
        """Verifica la activación/desactivación rápida del cliente."""
        self.assertTrue(self.cliente_activo.activo)
        response = self.client_http.post(reverse('ventas:cliente_toggle_activo', args=[self.cliente_activo.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('HX-Trigger'), 'clienteGuardado')

        self.cliente_activo.refresh_from_db()
        self.assertFalse(self.cliente_activo.activo)


# ==============================================================================
# PRUEBAS UNITARIAS FASE 2: COTIZACIONES Y PEDIDOS DE VENTA (MULTIDIVISA)
# ==============================================================================

class PedidoVentaModelTestCase(TestCase):
    def setUp(self):
        self.moneda_mxn, _ = Moneda.objects.get_or_create(
            codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'}
        )
        self.moneda_usd, _ = Moneda.objects.get_or_create(
            codigo='USD', defaults={'nombre': 'Dólar Estadounidense', 'simbolo': '$'}
        )
        self.unidad, _ = UnidadMedida.objects.get_or_create(codigo='PZA', nombre='Pieza')
        self.bodega, _ = Bodega.objects.get_or_create(codigo='BOD-PT', nombre='Bodega Producto Terminado')
        self.producto = Producto.objects.create(
            nombre='Bote Aerosol 250ml Blanco',
            sku='AE-250-BLA',
            tipo='PT',
            unidad_medida=self.unidad,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn
        )
        self.cliente = Cliente.objects.create(
            rfc='DEC010203AB1',
            razon_social='AEROSOLES DECORATIVOS SA DE CV',
            regimen_fiscal='601',
            codigo_postal='06000',
            correo='compras@decora.com',
            dias_credito=30
        )

    def test_crear_cotizacion_y_recalcular_totales_mxn(self):
        """Verifica la generación de una cotización en MXN con desglose de impuestos y totales."""
        folio = generar_folio_venta(PedidoVenta_Maestro.COTIZACION)
        cotizacion = PedidoVenta_Maestro.objects.create(
            folio=folio,
            tipo_documento=PedidoVenta_Maestro.COTIZACION,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.COTIZADO
        )
        self.assertTrue(cotizacion.folio.startswith('COT-'))

        PedidoVenta_Detalle.objects.create(
            pedido=cotizacion,
            producto=self.producto,
            cantidad=Decimal('100.00'),
            precio_unitario=Decimal('25.00')
        )
        cotizacion.recalcular_totales()
        cotizacion.refresh_from_db()

        self.assertEqual(cotizacion.subtotal, Decimal('2500.00'))
        self.assertEqual(cotizacion.impuestos, Decimal('400.00'))  # 16% IVA
        self.assertEqual(cotizacion.total, Decimal('2900.00'))
        self.assertEqual(cotizacion.total_mxn, Decimal('2900.00'))

    def test_crear_pedido_usd_conversion_multidivisa(self):
        """Verifica la valuación contable en MXN de un pedido registrado en USD."""
        tc_usd = Decimal('18.500000')
        pedido = PedidoVenta_Maestro.objects.create(
            folio=generar_folio_venta(PedidoVenta_Maestro.PEDIDO),
            tipo_documento=PedidoVenta_Maestro.PEDIDO,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_usd,
            tipo_cambio_aplicado=tc_usd,
            estado=PedidoVenta_Maestro.CONFIRMADO
        )
        self.assertTrue(pedido.folio.startswith('PED-'))

        PedidoVenta_Detalle.objects.create(
            pedido=pedido,
            producto=self.producto,
            cantidad=Decimal('10.00'),
            precio_unitario=Decimal('10.00')
        )
        pedido.recalcular_totales()
        pedido.refresh_from_db()

        self.assertEqual(pedido.subtotal, Decimal('100.00'))
        self.assertEqual(pedido.impuestos, Decimal('16.00'))
        self.assertEqual(pedido.total, Decimal('116.00'))
        # 116 * 18.50 = 2146.00 MXN
        self.assertEqual(pedido.total_mxn, Decimal('2146.00'))

    def test_generar_folio_secuencial_diferenciado(self):
        """Verifica que los folios COT y PED sigan secuencias independientes."""
        f_cot1 = generar_folio_venta('COT')
        PedidoVenta_Maestro.objects.create(
            folio=f_cot1,
            tipo_documento='COT',
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000')
        )
        f_cot2 = generar_folio_venta('COT')
        self.assertNotEqual(f_cot1, f_cot2)

        f_ped1 = generar_folio_venta('PED')
        self.assertTrue(f_ped1.startswith('PED-'))

    def test_convertir_cotizacion_a_pedido_service(self):
        """Verifica el flujo atómico de promoción de Cotización a Pedido en Firme."""
        cotizacion = PedidoVenta_Maestro.objects.create(
            folio=generar_folio_venta('COT'),
            tipo_documento='COT',
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.COTIZADO
        )
        PedidoVenta_Detalle.objects.create(
            pedido=cotizacion,
            producto=self.producto,
            cantidad=Decimal('50.00'),
            precio_unitario=Decimal('20.00')
        )
        cotizacion.recalcular_totales()
        folio_original = cotizacion.folio

        pedido_convertido = convertir_cotizacion_a_pedido(cotizacion.id)
        self.assertEqual(pedido_convertido.tipo_documento, PedidoVenta_Maestro.PEDIDO)
        self.assertTrue(pedido_convertido.folio.startswith('PED-'))
        self.assertEqual(pedido_convertido.estado, PedidoVenta_Maestro.CONFIRMADO)
        self.assertIn(folio_original, pedido_convertido.observaciones)
        self.assertEqual(pedido_convertido.subtotal, Decimal('1000.00'))
        self.assertEqual(pedido_convertido.total, Decimal('1160.00'))


class PedidoVentaViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='vendedor_pedidos', password='password123')
        self.client_http = Client()
        self.client_http.force_login(self.user)

        self.moneda_mxn, _ = Moneda.objects.get_or_create(
            codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'}
        )
        self.moneda_usd, _ = Moneda.objects.get_or_create(
            codigo='USD', defaults={'nombre': 'Dólar Estadounidense', 'simbolo': '$'}
        )
        self.unidad, _ = UnidadMedida.objects.get_or_create(codigo='PZA', nombre='Pieza')
        self.bodega, _ = Bodega.objects.get_or_create(codigo='BOD-PT', nombre='Bodega Producto Terminado')
        self.producto = Producto.objects.create(
            nombre='Lata Litografiada 500ml',
            sku='LAT-500-LIT',
            tipo='PT',
            unidad_medida=self.unidad,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn
        )
        self.cliente = Cliente.objects.create(
            rfc='CLI900101XYZ',
            razon_social='ENVASES DEL NORTE SA DE CV',
            nombre_comercial='Envases Norte',
            regimen_fiscal='601',
            codigo_postal='64000',
            correo='pedidos@norte.com',
            dias_credito=15
        )

        self.pedido = PedidoVenta_Maestro.objects.create(
            folio='PED-2026-0001',
            tipo_documento='PED',
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.CONFIRMADO,
            subtotal=Decimal('5000.00'),
            impuestos=Decimal('800.00'),
            total=Decimal('5800.00'),
            total_mxn=Decimal('5800.00')
        )
        PedidoVenta_Detalle.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=Decimal('200.00'),
            precio_unitario=Decimal('25.00'),
            subtotal_linea=Decimal('5000.00')
        )

        self.cotizacion = PedidoVenta_Maestro.objects.create(
            folio='COT-2026-0001',
            tipo_documento='COT',
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_usd,
            tipo_cambio_aplicado=Decimal('18.000000'),
            estado=PedidoVenta_Maestro.COTIZADO,
            subtotal=Decimal('100.00'),
            impuestos=Decimal('16.00'),
            total=Decimal('116.00'),
            total_mxn=Decimal('2088.00')
        )

    def test_pedidos_catalogo_vista_estandar(self):
        """Verifica la carga del listado completo de pedidos y cotizaciones."""
        response = self.client_http.get(reverse('ventas:pedidos_catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cotizaciones y Pedidos')
        self.assertContains(response, 'PED-2026-0001')
        self.assertContains(response, 'COT-2026-0001')

    def test_pedidos_catalogo_vista_htmx_filtro_tipo(self):
        """Verifica el filtrado reactivo HTMX por tipo de documento."""
        response = self.client_http.get(reverse('ventas:pedidos_catalogo') + '?tipo=COT', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'ventas/partials/_tabla_pedidos.html')
        self.assertContains(response, 'COT-2026-0001')
        self.assertNotContains(response, 'PED-2026-0001')

    def test_pedidos_catalogo_busqueda_texto(self):
        """Verifica la búsqueda por texto en folio o cliente."""
        response = self.client_http.get(reverse('ventas:pedidos_catalogo') + '?q=PED-2026', HTTP_HX_REQUEST='true')
        self.assertContains(response, 'PED-2026-0001')
        self.assertNotContains(response, 'COT-2026-0001')

    def test_crear_pedido_form_get(self):
        """Verifica la carga del formulario de captura con catálogos y tipos de cambio."""
        response = self.client_http.get(reverse('ventas:pedido_crear'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nueva Cotización o Pedido de Venta')
        self.assertContains(response, self.cliente.razon_social)
        self.assertContains(response, self.producto.nombre)

    def test_crear_pedido_post_exitoso(self):
        """Verifica la captura y cálculo completo de un nuevo pedido de venta vía POST."""
        payload = {
            'tipo_documento': 'PED',
            'cliente': self.cliente.id,
            'bodega_despacho': self.bodega.id,
            'moneda': self.moneda_mxn.id,
            'tipo_cambio_aplicado': '1.000000',
            'metodo_pago': 'PUE',
            'forma_pago': '03',
            'uso_cfdi': 'G03',
            'observaciones': 'Pedido urgente para entrega en bodega norte.',
            'producto[]': [str(self.producto.id)],
            'cantidad[]': ['150'],
            'precio_unitario[]': ['30.00'],
            'notas_linea[]': ['Lote especial litografiado']
        }
        response = self.client_http.post(reverse('ventas:pedido_crear'), payload)
        self.assertEqual(response.status_code, 302)

        nuevo_pedido = PedidoVenta_Maestro.objects.filter(observaciones__icontains='urgente').first()
        self.assertIsNotNone(nuevo_pedido)
        self.assertEqual(nuevo_pedido.tipo_documento, 'PED')
        self.assertEqual(nuevo_pedido.subtotal, Decimal('4500.00'))
        self.assertEqual(nuevo_pedido.impuestos, Decimal('720.00'))
        self.assertEqual(nuevo_pedido.total, Decimal('5220.00'))
        self.assertEqual(nuevo_pedido.detalles.count(), 1)

    def test_detalle_pedido_vista(self):
        """Verifica la visualización de la vista de detalle del pedido con sus partidas."""
        response = self.client_http.get(reverse('ventas:pedido_detalle', args=[self.pedido.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pedido.folio)
        self.assertContains(response, self.cliente.razon_social)
        self.assertContains(response, self.producto.nombre)
        self.assertContains(response, '5800.00')

    def test_detalle_pedido_muestra_contacto_actual(self):
        """El expediente del cliente en el pedido muestra contacto, correo y dirección reales del modelo."""
        self.cliente.nombre_contacto = 'Lic. Marta Garza'
        self.cliente.direccion = 'Av. Constitución 500, Monterrey, NL'
        self.cliente.save()
        response = self.client_http.get(reverse('ventas:pedido_detalle', args=[self.pedido.id]))
        self.assertContains(response, 'Lic. Marta Garza')
        self.assertContains(response, 'mailto:pedidos@norte.com')
        self.assertContains(response, 'Av. Constitución 500, Monterrey, NL')
        self.assertNotContains(response, 'Sin correo')

    def test_detalle_pedido_contacto_vacio(self):
        """Sin contacto registrado se muestra el texto por defecto."""
        response = self.client_http.get(reverse('ventas:pedido_detalle', args=[self.pedido.id]))
        self.assertContains(response, 'No especificado')

    def test_form_pedido_expone_contacto_en_selector(self):
        """El selector de cliente lleva el contacto en data-* para la ficha rápida."""
        self.cliente.nombre_contacto = 'Lic. Marta Garza'
        self.cliente.save()
        response = self.client_http.get(reverse('ventas:pedido_crear'))
        self.assertContains(response, 'data-contacto="Lic. Marta Garza"')
        self.assertContains(response, 'id="ficha-contacto-cliente"')

    def test_cambiar_estado_pedido(self):
        """Verifica la actualización de estado de un pedido."""
        response = self.client_http.post(
            reverse('ventas:pedido_cambiar_estado', args=[self.pedido.id]),
            {'nuevo_estado': 'CANCELADO'}
        )
        self.assertEqual(response.status_code, 302)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, 'CANCELADO')

    def test_convertir_cotizacion_view(self):
        """Verifica la conversión de cotización a pedido desde la vista POST."""
        response = self.client_http.post(reverse('ventas:pedido_convertir', args=[self.cotizacion.id]))
        self.assertEqual(response.status_code, 302)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.tipo_documento, 'PED')
        self.assertEqual(self.cotizacion.estado, 'CONFIRMADO')
        self.assertTrue(self.cotizacion.folio.startswith('PED-'))


# ==============================================================================
# PRUEBAS UNITARIAS FASE 3: INTEGRACIÓN ATÓMICA CON ALMACÉN Y KARDEX
# ==============================================================================

class PedidoVentaAlmacenTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='almacenista1', password='password123')
        self.client_http = Client()
        self.client_http.force_login(self.user)

        self.moneda_mxn, _ = Moneda.objects.get_or_create(
            codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'}
        )
        self.unidad, _ = UnidadMedida.objects.get_or_create(codigo='PZA', nombre='Pieza')
        self.bodega, _ = Bodega.objects.get_or_create(codigo='BOD-PT', nombre='Bodega Producto Terminado')

        self.producto = Producto.objects.create(
            nombre='Bote Aerosol 250ml Blanco',
            sku='AE-250-BLA',
            tipo='PT',
            unidad_medida=self.unidad,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('15.500000')
        )

        self.stock = Stock.objects.create(
            producto=self.producto,
            bodega=self.bodega,
            cantidad=Decimal('100.000000'),
            cantidad_reservada=Decimal('0.000000')
        )

        self.cliente = Cliente.objects.create(
            rfc='DEC010203AB1',
            razon_social='AEROSOLES DECORATIVOS SA DE CV',
            regimen_fiscal='601',
            codigo_postal='06000',
            correo='compras@decora.com'
        )

        self.pedido = PedidoVenta_Maestro.objects.create(
            folio='PED-2026-0099',
            tipo_documento=PedidoVenta_Maestro.PEDIDO,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.CONFIRMADO,
            subtotal=Decimal('1500.00'),
            impuestos=Decimal('240.00'),
            total=Decimal('1740.00'),
            total_mxn=Decimal('1740.00')
        )
        self.detalle = PedidoVenta_Detalle.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=Decimal('50.000000'),
            precio_unitario=Decimal('30.000000'),
            subtotal_linea=Decimal('1500.000000')
        )

    def test_verificar_disponibilidad_stock_completa(self):
        """Verifica que el servicio reporte stock físico y disponible completo."""
        disp = verificar_disponibilidad_stock(self.pedido)
        self.assertTrue(disp['hay_stock_fisico_completo'])
        self.assertTrue(disp['hay_disponible_completo'])
        self.assertEqual(len(disp['partidas']), 1)
        partida = disp['partidas'][0]
        self.assertEqual(partida['stock_fisico'], Decimal('100.000000'))
        self.assertEqual(partida['stock_disponible'], Decimal('100.000000'))
        self.assertEqual(partida['faltante_fisico'], Decimal('0.000000'))

    def test_verificar_disponibilidad_stock_insuficiente(self):
        """Verifica que el servicio identifique faltantes de inventario."""
        self.stock.cantidad = Decimal('20.000000')
        self.stock.save()

        disp = verificar_disponibilidad_stock(self.pedido)
        self.assertFalse(disp['hay_stock_fisico_completo'])
        self.assertFalse(disp['hay_disponible_completo'])
        partida = disp['partidas'][0]
        self.assertEqual(partida['faltante_fisico'], Decimal('30.000000'))

    def test_reservar_stock_pedido_exitoso_y_liberar(self):
        """Verifica el apartado y posterior liberación formal de existencias."""
        self.assertFalse(self.pedido.stock_reservado)
        self.assertEqual(self.stock.cantidad_reservada, Decimal('0.000000'))

        # 1. Reservar
        reservar_stock_pedido(self.pedido)
        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()

        self.assertTrue(self.pedido.stock_reservado)
        self.assertEqual(self.stock.cantidad_reservada, Decimal('50.000000'))
        self.assertEqual(self.stock.cantidad_disponible, Decimal('50.000000'))

        # 2. Liberar
        liberar_reserva_stock_pedido(self.pedido)
        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()

        self.assertFalse(self.pedido.stock_reservado)
        self.assertEqual(self.stock.cantidad_reservada, Decimal('0.000000'))
        self.assertEqual(self.stock.cantidad_disponible, Decimal('100.000000'))

    def test_reservar_stock_insuficiente_lanza_error(self):
        """No permite apartar más stock del físicamente disponible."""
        self.stock.cantidad = Decimal('30.000000')
        self.stock.save()

        with self.assertRaises(ValidationError):
            reservar_stock_pedido(self.pedido)

        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertFalse(self.pedido.stock_reservado)
        self.assertEqual(self.stock.cantidad_reservada, Decimal('0.000000'))

    def test_surtir_pedido_exitoso_afectacion_kardex_y_stock(self):
        """Verifica el surtido completo: descuento de stock, creación en Kardex y liberación de reserva."""
        # Apartamos primero el stock
        reservar_stock_pedido(self.pedido)
        self.pedido.refresh_from_db()
        self.assertTrue(self.pedido.stock_reservado)

        # Surtir pedido
        surtir_pedido(self.pedido, self.user)
        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()
        self.detalle.refresh_from_db()

        # Validaciones de Maestro y Detalle
        self.assertEqual(self.pedido.estado, PedidoVenta_Maestro.SURTIDO)
        self.assertFalse(self.pedido.stock_reservado)
        self.assertIsNotNone(self.pedido.fecha_surtido)
        self.assertEqual(self.pedido.usuario_surtio, self.user)
        self.assertEqual(self.detalle.cantidad_surtida, Decimal('50.000000'))

        # Validaciones de Stock físico en bodega
        # Tenía 100 y surtió 50 -> quedan 50 físicos y 0 reservados
        self.assertEqual(self.stock.cantidad, Decimal('50.000000'))
        self.assertEqual(self.stock.cantidad_reservada, Decimal('0.000000'))
        self.assertEqual(self.stock.cantidad_disponible, Decimal('50.000000'))

        # Validaciones de Movimiento en Kardex
        mov = MovimientoInventario.objects.filter(referencia_operacion=f"PED-{self.pedido.folio}").first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.tipo_movimiento, MovimientoInventario.SALIDA)
        self.assertEqual(mov.cantidad, Decimal('50.000000'))
        self.assertEqual(mov.bodega_origen, self.bodega)
        self.assertEqual(mov.costo_unitario_mxn_capturado, Decimal('15.500000'))
        self.assertEqual(mov.usuario, self.user)

    def test_surtir_pedido_stock_insuficiente_rollback_atomico(self):
        """Verifica que si no hay stock físico suficiente se cancele atómicamente la operación."""
        self.stock.cantidad = Decimal('10.000000')
        self.stock.save()

        with self.assertRaises(ValidationError):
            surtir_pedido(self.pedido, self.user)

        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()

        self.assertEqual(self.pedido.estado, PedidoVenta_Maestro.CONFIRMADO)
        self.assertEqual(self.stock.cantidad, Decimal('10.000000'))
        self.assertFalse(MovimientoInventario.objects.filter(referencia_operacion=f"PED-{self.pedido.folio}").exists())

    def test_cancelar_pedido_con_reserva_libera_automaticamente(self):
        """Al cancelar un pedido desde la vista, cualquier stock apartado se libera en automático."""
        reservar_stock_pedido(self.pedido)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cantidad_reservada, Decimal('50.000000'))

        response = self.client_http.post(
            reverse('ventas:pedido_cambiar_estado', args=[self.pedido.id]),
            {'nuevo_estado': 'CANCELADO'}
        )
        self.assertEqual(response.status_code, 302)

        self.pedido.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(self.pedido.estado, 'CANCELADO')
        self.assertFalse(self.pedido.stock_reservado)
        self.assertEqual(self.stock.cantidad_reservada, Decimal('0.000000'))

    def test_vistas_acciones_almacen_http(self):
        """Prueba los endpoints HTTP para apartar, liberar y surtir el pedido."""
        # 1. Reservar vía HTTP
        resp_reserva = self.client_http.post(reverse('ventas:pedido_reservar_stock', args=[self.pedido.id]))
        self.assertEqual(resp_reserva.status_code, 302)
        self.pedido.refresh_from_db()
        self.assertTrue(self.pedido.stock_reservado)

        # 2. Liberar vía HTTP
        resp_libera = self.client_http.post(reverse('ventas:pedido_liberar_reserva', args=[self.pedido.id]))
        self.assertEqual(resp_libera.status_code, 302)
        self.pedido.refresh_from_db()
        self.assertFalse(self.pedido.stock_reservado)

        # 3. Surtir vía HTTP
        resp_surtir = self.client_http.post(reverse('ventas:pedido_surtir', args=[self.pedido.id]))
        self.assertEqual(resp_surtir.status_code, 302)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, PedidoVenta_Maestro.SURTIDO)

        # 4. Detalle muestra trazabilidad de Kardex
        resp_det = self.client_http.get(reverse('ventas:pedido_detalle', args=[self.pedido.id]))
        self.assertEqual(resp_det.status_code, 200)
        self.assertContains(resp_det, 'Movimientos de Salida Generados en el Kardex')
        self.assertContains(resp_det, '100% Surtido')


# ==============================================================================
# PRUEBAS UNITARIAS FASE 4: DOCUMENTACIÓN FORMAL EN PDF CON WEASYPRINT
# ==============================================================================

class PedidoVentaPDFTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ventas_pdf', password='password123')
        self.client_http = Client()
        self.client_http.force_login(self.user)

        self.moneda_mxn, _ = Moneda.objects.get_or_create(
            codigo='MXN', defaults={'nombre': 'Peso Mexicano', 'simbolo': '$'}
        )
        self.moneda_usd, _ = Moneda.objects.get_or_create(
            codigo='USD', defaults={'nombre': 'Dólar Estadounidense', 'simbolo': '$'}
        )
        self.unidad, _ = UnidadMedida.objects.get_or_create(codigo='PZA', nombre='Pieza')
        self.bodega, _ = Bodega.objects.get_or_create(codigo='BOD-PT', nombre='Bodega Producto Terminado')

        self.producto = Producto.objects.create(
            nombre='Lata Litografiada 500ml',
            sku='LAT-500-LIT',
            tipo='PT',
            unidad_medida=self.unidad,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            costo_promedio_mxn=Decimal('18.000000')
        )

        self.cliente = Cliente.objects.create(
            rfc='DEC010203AB1',
            razon_social='DECORACIONES Y LATAS INDUSTRIALES SA DE CV',
            nombre_comercial='Decorlata Clientes',
            regimen_fiscal='601',
            codigo_postal='06000',
            correo='facturas@decorlata.com',
            dias_credito=30
        )

        self.pedido = PedidoVenta_Maestro.objects.create(
            folio='PED-2026-0500',
            tipo_documento=PedidoVenta_Maestro.PEDIDO,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.CONFIRMADO,
            subtotal=Decimal('5000.00'),
            impuestos=Decimal('800.00'),
            total=Decimal('5800.00'),
            total_mxn=Decimal('5800.00'),
            observaciones='Entrega prioritaria con certificado de calidad.'
        )
        PedidoVenta_Detalle.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=Decimal('200.00'),
            precio_unitario=Decimal('25.00'),
            subtotal_linea=Decimal('5000.00'),
            notas_linea='Barniz interior grado alimenticio'
        )

        self.cotizacion = PedidoVenta_Maestro.objects.create(
            folio='COT-2026-0500',
            tipo_documento=PedidoVenta_Maestro.COTIZACION,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_usd,
            tipo_cambio_aplicado=Decimal('18.500000'),
            estado=PedidoVenta_Maestro.COTIZADO,
            subtotal=Decimal('100.00'),
            impuestos=Decimal('16.00'),
            total=Decimal('116.00'),
            total_mxn=Decimal('2146.00')
        )
        PedidoVenta_Detalle.objects.create(
            pedido=self.cotizacion,
            producto=self.producto,
            cantidad=Decimal('10.00'),
            precio_unitario=Decimal('10.00'),
            subtotal_linea=Decimal('100.00')
        )

    def test_numero_a_letras_mxn(self):
        """Verifica la conversión a letras en moneda nacional mexicana."""
        letras = numero_a_letras(Decimal('5800.00'), 'MXN')
        self.assertEqual(letras, 'CINCO MIL OCHOCIENTOS PESOS 00/100 M.N.')

        letras_centavos = numero_a_letras(Decimal('1250.75'), 'MXN')
        self.assertEqual(letras_centavos, 'UN MIL DOSCIENTOS CINCUENTA PESOS 75/100 M.N.')

    def test_numero_a_letras_usd(self):
        """Verifica la conversión a letras para montos en Dólares USD."""
        letras = numero_a_letras(Decimal('116.00'), 'USD')
        self.assertEqual(letras, 'CIENTO DIECISÉIS DÓLARES 00/100 USD')

    def test_descargar_pedido_pdf_view_exitoso(self):
        """Verifica la generación y compilación binaria de un Pedido de Venta formal con WeasyPrint."""
        response = self.client_http.get(reverse('ventas:pedido_pdf', args=[self.pedido.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline; filename="PED-2026-0500.pdf"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))

    def test_descargar_cotizacion_pdf_view_exitoso(self):
        """Verifica la generación de una Cotización comercial formal en PDF con divisa extranjera."""
        response = self.client_http.get(reverse('ventas:pedido_pdf', args=[self.cotizacion.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline; filename="COT-2026-0500.pdf"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))


class FacturacionCFDITestCase(TestCase):
    """
    Batería de pruebas unitarias para Fase 5: Facturación Electrónica SAT (CFDI 4.0),
    sellado digital, timbrado PAC/SAT, representación impresa PDF y Complemento de Pagos 2.0.
    """
    def setUp(self):
        self.user = User.objects.create_user(username='facturista', password='password123')
        self.client_http = Client()
        self.client_http.login(username='facturista', password='password123')

        self.moneda_mxn = Moneda.objects.create(codigo='MXN', nombre='Peso Mexicano', simbolo='$')
        self.moneda_usd = Moneda.objects.create(codigo='USD', nombre='Dólar Estadounidense', simbolo='US$')

        self.unidad_h87 = UnidadMedida.objects.create(codigo='H87', nombre='Pieza')
        self.bodega = Bodega.objects.create(codigo='BOD-PLANTA', nombre='Almacén General Planta')

        # Producto industrial (ej. Lata alcoholera como en la factura muestra)
        self.producto = Producto.objects.create(
            sku='LA-327',
            nombre='LATA ALCOHOLERA 32.7 CMS DISEÑO SOLDER',
            tipo=Producto.PRODUCTO_TERMINADO,
            unidad_medida=self.unidad_h87,
            moneda_base_costo=self.moneda_mxn,
            moneda_base_venta=self.moneda_mxn,
            clave_sat='24121800',
            clave_unidad_sat='H87',
            costo_promedio_mxn=Decimal('45.00')
        )

        # Cliente receptor con datos fiscales del anexo
        self.cliente = Cliente.objects.create(
            rfc='SME9105021R0',
            razon_social='SOLDER DE MEXICO',
            nombre_comercial='Solder',
            regimen_fiscal='601',
            codigo_postal='37545',
            uso_cfdi='G01',
            correo='facturacion@solder.com.mx',
            direccion='Calle: GAMMA Núm. exterior: 210 Colonia: FRACC INDUSTRIAL DELTA C.P.: 37545 LEON, GTO',
            dias_credito=30,
            limite_credito=Decimal('500000.00')
        )

        # Pedido surtido listo para facturación
        self.pedido_surtido = PedidoVenta_Maestro.objects.create(
            folio='PED-2026-5752',
            tipo_documento=PedidoVenta_Maestro.PEDIDO,
            cliente=self.cliente,
            bodega_despacho=self.bodega,
            moneda=self.moneda_mxn,
            tipo_cambio_aplicado=Decimal('1.000000'),
            estado=PedidoVenta_Maestro.ESTADO_SURTIDO,
            metodo_pago='PPD',
            forma_pago='99',
            uso_cfdi='G01',
            referencia_cliente='OC: 5752',
            subtotal=Decimal('83160.00'),
            impuestos=Decimal('13305.60'),
            total=Decimal('96465.60'),
            total_mxn=Decimal('96465.60')
        )
        PedidoVenta_Detalle.objects.create(
            pedido=self.pedido_surtido,
            producto=self.producto,
            cantidad=Decimal('990.00'),
            precio_unitario=Decimal('84.00'),
            subtotal_linea=Decimal('83160.00'),
            cantidad_surtida=Decimal('990.00'),
            notas_linea='O C: 5752'
        )

    def test_emitir_factura_desde_pedido_surtido_exitoso(self):
        """Verifica la emisión completa y timbrado del CFDI 4.0 a partir del pedido surtido."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)

        self.assertIsNotNone(factura)
        self.assertEqual(factura.tipo_comprobante, 'I')
        self.assertEqual(factura.serie, 'F')
        self.assertEqual(factura.estado, Factura_Maestro.ESTADO_TIMBRADA)
        self.assertIsNotNone(factura.uuid)
        self.assertEqual(factura.subtotal, Decimal('83160.00'))
        self.assertEqual(factura.impuestos_trasladados, Decimal('13305.60'))
        self.assertEqual(factura.total, Decimal('96465.60'))
        self.assertEqual(factura.saldo_insoluto, Decimal('96465.60'))  # Al ser PPD
        self.assertFalse(factura.pagada)

        # Partidas del CFDI
        self.assertEqual(factura.detalles.count(), 1)
        partida = factura.detalles.first()
        self.assertEqual(partida.clave_prod_serv, '24121800')
        self.assertEqual(partida.clave_unidad, 'H87')
        self.assertEqual(partida.cantidad, Decimal('990.00'))
        self.assertEqual(partida.valor_unitario, Decimal('84.00'))
        self.assertEqual(partida.importe, Decimal('83160.00'))
        self.assertEqual(partida.importe_iva, Decimal('13305.60'))

        # El pedido de venta debe pasar a FACTURADO
        self.pedido_surtido.refresh_from_db()
        self.assertEqual(self.pedido_surtido.estado, PedidoVenta_Maestro.ESTADO_FACTURADO)
        self.assertEqual(self.pedido_surtido.cfdi_uuid, factura.uuid)

    def test_estructura_xml_cfdi40_anexo20(self):
        """Verifica la generación estricta del XML según los estándares del SAT y Anexo 20."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)
        xml_str = construir_xml_cfdi40(factura)

        # Validaciones de namespaces y atributos raíz
        self.assertIn('xmlns:cfdi="http://www.sat.gob.mx/cfd/4"', xml_str)
        self.assertIn('Version="4.0"', xml_str)
        self.assertIn('Serie="F"', xml_str)
        self.assertIn('Folio="', xml_str)
        self.assertIn('SubTotal="83160.00"', xml_str)
        self.assertIn('Total="96465.60"', xml_str)
        self.assertIn('Moneda="MXN"', xml_str)
        self.assertIn('MetodoPago="PPD"', xml_str)
        self.assertIn('LugarExpedicion="56255"', xml_str)

        # Validaciones Emisor Decorlata
        self.assertIn('Rfc="DEC150115B42"', xml_str)
        self.assertIn('Nombre="DECORLATA S.A. DE C.V."', xml_str)
        self.assertIn('RegimenFiscal="601"', xml_str)

        # Validaciones Receptor
        self.assertIn(f'Rfc="{self.cliente.rfc}"', xml_str)
        self.assertIn(f'Nombre="{self.cliente.razon_social}"', xml_str)
        self.assertIn(f'DomicilioFiscalReceptor="{self.cliente.codigo_postal}"', xml_str)
        self.assertIn('UsoCFDI="G01"', xml_str)

        # Validaciones Conceptos e Impuestos
        self.assertIn('ClaveProdServ="24121800"', xml_str)
        self.assertIn('ClaveUnidad="H87"', xml_str)
        self.assertIn('Impuesto="002"', xml_str)
        self.assertIn('TasaOCuota="0.160000"', xml_str)
        self.assertIn('Importe="13305.60"', xml_str)

        # Timbre Fiscal Digital
        self.assertIn('xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"', xml_str)
        self.assertIn('Version="1.1"', xml_str)
        self.assertIn(f'UUID="{factura.uuid}"', xml_str)
        self.assertIn('RfcProvCertif="TSP080724QW6"', xml_str)

    def test_generar_qr_sat_oficial_base64(self):
        """Verifica la generación del código QR bidimensional en base64 con el estándar oficial del SAT."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)
        qr_b64 = generar_qr_sat_base64(factura)

        self.assertTrue(qr_b64.startswith('data:image/png;base64,'))
        self.assertGreater(len(qr_b64), 100)

    def test_descargar_factura_xml_view_http(self):
        """Verifica la descarga del archivo XML CFDI 4.0 vía endpoint HTTP."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)
        response = self.client_http.get(reverse('ventas:factura_xml', args=[factura.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(f'attachment; filename="CFDI_F_{factura.folio}_{factura.uuid}.xml"', response['Content-Disposition'])
        self.assertIn(b'cfdi:Comprobante', response.content)
        self.assertIn(b'DEC150115B42', response.content)

    def test_descargar_factura_pdf_view_http(self):
        """Verifica la generación y descarga en PDF oficial con WeasyPrint en el formato Decorlata."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)
        response = self.client_http.get(reverse('ventas:factura_pdf', args=[factura.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(f'inline; filename="CFDI_F_{factura.folio}.pdf"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))

    def test_complemento_de_pagos_rep20_flujo_completo(self):
        """Verifica el ciclo completo de cobranza PPD: abono parcial, emisión de REP 2.0 y liquidación total."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)
        self.assertEqual(factura.saldo_insoluto, Decimal('96465.60'))
        self.assertFalse(factura.pagada)

        # 1. Primer Abono Parcial de $50,000.00
        pago_1 = registrar_complemento_pago(
            factura_origen=factura,
            monto_pago=Decimal('50000.00'),
            forma_pago='03',
            num_operacion='SPEI-12345',
            usuario=self.user
        )

        self.assertEqual(pago_1.tipo_comprobante, 'P')
        self.assertEqual(pago_1.serie, 'P')
        self.assertEqual(pago_1.estado, Factura_Maestro.ESTADO_TIMBRADA)
        self.assertIsNotNone(pago_1.uuid)

        # Documento relacionado 1
        dr_1 = pago_1.doctos_relacionados.first()
        self.assertEqual(dr_1.num_parcialidad, 1)
        self.assertEqual(dr_1.imp_saldo_ant, Decimal('96465.60'))
        self.assertEqual(dr_1.imp_pagado, Decimal('50000.00'))
        self.assertEqual(dr_1.imp_saldo_insoluto, Decimal('46465.60'))

        factura.refresh_from_db()
        self.assertEqual(factura.saldo_insoluto, Decimal('46465.60'))
        self.assertFalse(factura.pagada)

        # Comprobar PDF de pago
        response_pdf_rep = self.client_http.get(reverse('ventas:factura_pdf', args=[pago_1.id]))
        self.assertEqual(response_pdf_rep.status_code, 200)
        self.assertTrue(response_pdf_rep.content.startswith(b'%PDF-'))

        # 2. Segundo Abono Liquidando el Saldo Restante de $46,465.60
        pago_2 = registrar_complemento_pago(
            factura_origen=factura,
            monto_pago=Decimal('46465.60'),
            forma_pago='03',
            num_operacion='SPEI-67890',
            usuario=self.user
        )

        dr_2 = pago_2.doctos_relacionados.first()
        self.assertEqual(dr_2.num_parcialidad, 2)
        self.assertEqual(dr_2.imp_saldo_ant, Decimal('46465.60'))
        self.assertEqual(dr_2.imp_pagado, Decimal('46465.60'))
        self.assertEqual(dr_2.imp_saldo_insoluto, Decimal('0.00'))

        factura.refresh_from_db()
        self.assertEqual(factura.saldo_insoluto, Decimal('0.00'))
        self.assertTrue(factura.pagada)

    def test_catalogo_facturas_y_filtros_htmx(self):
        """Verifica la vista del catálogo de facturas y los filtros interactivos HTMX."""
        factura = emitir_factura_desde_pedido(self.pedido_surtido, usuario=self.user)

        # GET estándar
        response = self.client_http.get(reverse('ventas:facturas_catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Facturación Fiscal SAT (CFDI 4.0)')
        self.assertContains(response, factura.folio_completo)

        # GET HTMX con filtro de tipo 'I'
        response_htmx = self.client_http.get(
            reverse('ventas:facturas_catalogo') + '?tipo=I',
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response_htmx.status_code, 200)
        self.assertTemplateUsed(response_htmx, 'ventas/partials/_tabla_facturas.html')
        self.assertContains(response_htmx, factura.folio_completo)

        # GET con búsqueda de texto
        response_busqueda = self.client_http.get(
            reverse('ventas:facturas_catalogo') + f'?q={factura.cliente.rfc}',
            HTTP_HX_REQUEST='true'
        )
        self.assertContains(response_busqueda, factura.cliente.rfc)

    def test_emitir_factura_pedido_view_post(self):
        """Verifica la acción web POST para facturar un pedido surtido desde la interfaz."""
        url = reverse('ventas:pedido_facturar', args=[self.pedido_surtido.id])
        payload = {
            'metodo_pago': 'PPD',
            'forma_pago': '99',
            'uso_cfdi': 'G01',
            'notas': 'Facturación electrónica de prueba'
        }
        response = self.client_http.post(url, payload)
        self.assertEqual(response.status_code, 302)

        self.pedido_surtido.refresh_from_db()
        self.assertEqual(self.pedido_surtido.estado, PedidoVenta_Maestro.ESTADO_FACTURADO)
        self.assertTrue(self.pedido_surtido.facturas.filter(estado=Factura_Maestro.ESTADO_TIMBRADA).exists())

