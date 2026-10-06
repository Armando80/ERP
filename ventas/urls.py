# ERP/ventas/urls.py

from django.urls import path
from . import views

app_name = 'ventas'

urlpatterns = [
    # Catálogo y Directorio de Clientes
    path('clientes/', views.clientes_catalogo_view, name='clientes_catalogo'),
    path('clientes/nuevo/', views.guardar_cliente_view, name='cliente_crear'),
    path('clientes/<int:pk>/editar/', views.guardar_cliente_view, name='cliente_editar'),
    path('clientes/<int:pk>/detalle/', views.cliente_detalle_modal_view, name='cliente_detalle_modal'),
    path('clientes/<int:pk>/toggle-activo/', views.cambiar_estado_cliente_view, name='cliente_toggle_activo'),

    # Cotizaciones y Pedidos de Venta
    path('pedidos/', views.pedidos_catalogo_view, name='pedidos_catalogo'),
    path('pedidos/nuevo/', views.crear_pedido_view, name='pedido_crear'),
    path('pedidos/<int:pk>/', views.detalle_pedido_view, name='pedido_detalle'),
    path('pedidos/<int:pk>/cambiar-estado/', views.cambiar_estado_pedido_view, name='pedido_cambiar_estado'),
    path('pedidos/<int:pk>/convertir/', views.convertir_cotizacion_view, name='pedido_convertir'),

    # Integración con Almacén y Surtido (Fase 3)
    path('pedidos/<int:pk>/surtir/', views.surtir_pedido_view, name='pedido_surtir'),
    path('pedidos/<int:pk>/reservar-stock/', views.reservar_stock_pedido_view, name='pedido_reservar_stock'),
    path('pedidos/<int:pk>/liberar-reserva/', views.liberar_reserva_stock_pedido_view, name='pedido_liberar_reserva'),

    # Generación de PDF WeasyPrint (Fase 4)
    path('pedidos/<int:pk>/pdf/', views.descargar_pedido_pdf_view, name='pedido_pdf'),

    # Facturación Electrónica SAT CFDI 4.0 y Complemento de Pagos (Fase 5)
    path('facturas/', views.facturas_catalogo_view, name='facturas_catalogo'),
    path('facturas/<int:pk>/', views.factura_detalle_view, name='factura_detalle'),
    path('facturas/<int:pk>/xml/', views.descargar_factura_xml_view, name='factura_xml'),
    path('facturas/<int:pk>/pdf/', views.descargar_factura_pdf_view, name='factura_pdf'),
    path('facturas/<int:pk>/pago/', views.registrar_pago_factura_view, name='factura_pago'),
    path('pedidos/<int:pk>/facturar/', views.emitir_factura_pedido_view, name='pedido_facturar'),
]


