# ERP/compras/urls.py

from django.urls import path
from . import views

# Definimos el 'namespace' para evitar conflictos con otras apps (ej. inventario)
app_name = 'compras'

urlpatterns = [
    # Ruta para el formulario de Nueva Orden de Compra
    path('nueva-orden/', views.crear_orden_compra_view, name='crear_orden'),

    # Aquí iremos agregando el listado de órdenes, catálogo de proveedores, etc.
]