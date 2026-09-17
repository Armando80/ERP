# ERP/inventario/urls.py

from django.urls import path
from . import views

# El app_name es crucial para que Django entienda la etiqueta 'inventario:catalogo'
app_name = 'inventario'

urlpatterns = [
    # Ruta para ver el catálogo principal
    path('catalogo/', views.catalogo_view, name='catalogo'),

    # Cambiamos 'nuevo_producto' por 'producto_crear'
    path('catalogo/nuevo/', views.guardar_producto_view, name='producto_crear'),

    # Por precaución, aseguramos que el botón de editar en la tabla también coincida
    path('catalogo/editar/<int:pk>/', views.guardar_producto_view, name='producto_editar'),

    # NUEVA LÍNEA: Ruta para el botón de "Ver detalles" (Ojito)
    path('catalogo/detalle/<int:pk>/', views.guardar_producto_view, name='producto_detalle'),

    # NUEVA RUTA PARA EL MODAL DE EXISTENCIAS
    path('catalogo/<int:pk>/stock/', views.producto_stock_view, name='producto_stock'),

    # NUEVA RUTA PARA EXISTENCIAS GLOBALES
    path('existencias/', views.existencias_view, name='existencias'),

    # Rutas para el Kardex y Movimientos
    path('movimientos/', views.movimientos_view, name='movimientos'),
    path('movimientos/registrar/', views.registrar_movimiento_view, name='registrar_movimiento'),
    path('movimientos/autorizaciones/', views.entregas_pendientes_view, name='entregas_pendientes'),
    path('movimientos/autorizaciones/<int:pk>/insumos/', views.detalle_insumos_entrega_view, name='detalle_insumos_entrega'),
    path('movimientos/autorizaciones/<int:pk>/autorizar/', views.autorizar_entrega_view, name='autorizar_entrega'),
    path('movimientos/autorizaciones/<int:pk>/rechazar/', views.rechazar_entrega_view, name='rechazar_entrega'),

    # Rutas para Anulaciones de Movimientos de Kardex (Autorización Administrativa)
    path('movimientos/<int:pk>/solicitar-anulacion/', views.solicitar_anulacion_modal_view, name='solicitar_anulacion_modal'),
    path('movimientos/<int:pk>/enviar-anulacion/', views.enviar_solicitud_anulacion_view, name='enviar_solicitud_anulacion'),
    path('movimientos/solicitudes-anulacion/', views.solicitudes_anulacion_view, name='solicitudes_anulacion'),
    path('movimientos/solicitudes-anulacion/<int:pk>/autorizar/', views.autorizar_anulacion_view, name='autorizar_anulacion'),
    path('movimientos/solicitudes-anulacion/<int:pk>/rechazar/', views.rechazar_anulacion_view, name='rechazar_anulacion'),
]