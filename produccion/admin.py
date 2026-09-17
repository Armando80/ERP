# ERP Decorlata - produccion/admin.py
from django.contrib import admin
from .models import (
    ListaMaterialesBOM, InsumoBOM, OrdenProduccion, OrdenProduccion_Insumo,
    EntregaParcialProduccion, EntregaParcial_Insumo
)


class InsumoBOMInline(admin.TabularInline):
    model = InsumoBOM
    extra = 1
    autocomplete_fields = ['materia_prima']


@admin.register(ListaMaterialesBOM)
class ListaMaterialesBOMAdmin(admin.ModelAdmin):
    list_display = ('producto_terminado', 'cantidad_base', 'costo_estimado_unitario_mxn', 'activo', 'fecha_actualizacion')
    list_filter = ('activo', 'fecha_actualizacion')
    search_fields = ('producto_terminado__nombre', 'producto_terminado__sku')
    autocomplete_fields = ['producto_terminado']
    inlines = [InsumoBOMInline]


class OrdenProduccion_InsumoInline(admin.TabularInline):
    model = OrdenProduccion_Insumo
    extra = 0
    readonly_fields = ('costo_unitario_mxn', 'costo_total_mxn')


class EntregaParcialInline(admin.TabularInline):
    model = EntregaParcialProduccion
    extra = 0
    fields = ('folio_entrega', 'cantidad_notificada', 'lote_fabricacion', 'estado', 'fecha_notificacion')
    readonly_fields = ('folio_entrega', 'fecha_notificacion')


@admin.register(OrdenProduccion)
class OrdenProduccionAdmin(admin.ModelAdmin):
    list_display = (
        'folio', 'producto_a_fabricar', 'cantidad_a_producir', 'cantidad_producida',
        'estado', 'bodega_origen_insumos', 'bodega_destino_pt', 'costo_unitario_final_mxn', 'fecha_inicio'
    )
    list_filter = ('estado', 'fecha_inicio', 'bodega_origen_insumos', 'bodega_destino_pt')
    search_fields = ('folio', 'producto_a_fabricar__nombre', 'producto_a_fabricar__sku', 'lote_fabricacion')
    readonly_fields = ('folio', 'costo_total_insumos_mxn', 'costo_unitario_final_mxn', 'fecha_inicio', 'fecha_finalizacion')
    autocomplete_fields = ['producto_a_fabricar', 'bodega_origen_insumos', 'bodega_destino_pt']
    inlines = [OrdenProduccion_InsumoInline, EntregaParcialInline]


class EntregaParcial_InsumoInline(admin.TabularInline):
    model = EntregaParcial_Insumo
    extra = 0
    readonly_fields = ('costo_unitario_mxn', 'costo_total_mxn')


@admin.register(EntregaParcialProduccion)
class EntregaParcialProduccionAdmin(admin.ModelAdmin):
    list_display = ('folio_entrega', 'orden', 'cantidad_notificada', 'lote_fabricacion', 'estado', 'fecha_notificacion', 'usuario_notifica', 'usuario_autoriza')
    list_filter = ('estado', 'fecha_notificacion')
    search_fields = ('folio_entrega', 'orden__folio', 'lote_fabricacion')
    readonly_fields = ('folio_entrega', 'fecha_notificacion', 'fecha_autorizacion', 'costo_total_insumos_mxn', 'costo_unitario_final_mxn')
    inlines = [EntregaParcial_InsumoInline]

