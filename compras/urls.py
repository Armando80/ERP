# ERP/compras/urls.py

from django.urls import path
from . import views

# Definimos el 'namespace' para evitar conflictos con otras apps (ej. inventario)
app_name = 'compras'

urlpatterns = [
    # Ruta para el formulario de Nueva Orden de Compra
    path('nueva-orden/', views.crear_orden_compra_view, name='crear_orden'),

    # Aquí iremos agregando el listado de órdenes, catálogo de proveedores, etc.
    # NUEVA RUTA PARA EL HISTORIAL
    path('historial/', views.historial_ordenes_view, name='historial_ordenes'),

    # NUEVA RUTA PARA RECIBIR LA ORDEN
    path('recibir/<int:pk>/', views.recibir_orden_view, name='recibir_orden'),

    # NUEVA RUTA PARA EL CATÁLOGO DE PROVEEDORES
    path('proveedores/', views.proveedores_catalogo_view, name='proveedores_catalogo'),
    path('proveedores/nuevo/', views.guardar_proveedor_view, name='proveedor_crear'),
    path('proveedores/editar/<int:pk>/', views.guardar_proveedor_view, name='proveedor_editar'),
]