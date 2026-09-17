# ERP Decorlata - produccion/urls.py
from django.urls import path
from . import views

app_name = 'produccion'

urlpatterns = [
    # Listas de Materiales / Fórmulas (BOM)
    path('boms/', views.boms_catalogo_view, name='lista_boms'),
    path('boms/crear/', views.crear_editar_bom_view, name='crear_bom'),
    path('boms/<int:pk>/editar/', views.crear_editar_bom_view, name='editar_bom'),
    path('boms/<int:pk>/', views.detalle_bom_view, name='detalle_bom'),
    path('boms/<int:bom_id>/agregar-insumo/', views.agregar_insumo_bom_view, name='agregar_insumo_bom'),
    path('boms/insumo/<int:pk>/eliminar/', views.eliminar_insumo_bom_view, name='eliminar_insumo_bom'),

    # Órdenes de Producción (OP)
    path('ordenes/', views.ordenes_catalogo_view, name='lista_ordenes'),
    path('ordenes/crear/', views.crear_orden_produccion_view, name='crear_orden'),
    path('ordenes/<int:pk>/', views.detalle_orden_produccion_view, name='detalle_orden'),
    path('ordenes/<int:pk>/iniciar/', views.iniciar_orden_view, name='iniciar_orden'),
    path('ordenes/<int:pk>/finalizar/', views.finalizar_orden_view, name='finalizar_orden'),
    path('ordenes/<int:pk>/cancelar/', views.cancelar_orden_view, name='cancelar_orden'),
    path('ordenes/<int:pk>/pdf/', views.descargar_hoja_viajero_pdf_view, name='descargar_pdf'),
    path('ordenes/<int:pk>/gestionar/', views.gestionar_orden_view, name='gestionar_orden'),
    path('ordenes/<int:pk>/notificar-entrega/', views.notificar_entrega_view, name='notificar_entrega'),
    path('ordenes/<int:pk>/cerrar-definitiva/', views.cerrar_orden_definitiva_view, name='cerrar_orden_definitiva'),


    # Endpoints dinámicos HTMX
    path('api/previsualizar-bom/', views.previsualizar_bom_op_view, name='previsualizar_bom'),
]
