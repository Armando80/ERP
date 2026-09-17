from django.contrib import admin
from .models import UnidadMedida, Bodega, Producto, Stock, MovimientoInventario, SolicitudAnulacionMovimiento

@admin.register(UnidadMedida)
class UnidadMedidaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre')
    search_fields = ('codigo', 'nombre')

@admin.register(Bodega)
class BodegaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'descripcion')
    search_fields = ('codigo', 'nombre')

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'nombre', 'tipo', 'unidad_medida', 'costo_promedio_mxn')
    list_filter = ('tipo', 'unidad_medida')
    search_fields = ('sku', 'nombre')
    readonly_fields = ('costo_promedio_mxn',) # El costo se calcula, no se edita a mano

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_select_related = ('producto', 'bodega')
    list_display = (
        'producto_sku',
        'producto_nombre',
        'bodega',
        'cantidad',
        'cantidad_reservada',
        'get_disponible',
        'ubicacion_especifica',
        'fecha_ultima_actualizacion'
    )
    list_filter = ('bodega', 'producto__tipo')
    search_fields = ('producto__sku', 'producto__nombre', 'ubicacion_especifica')

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return ('producto', 'bodega', 'cantidad', 'cantidad_reservada')

    @admin.display(ordering='producto__sku', description='SKU')
    def producto_sku(self, obj):
        return obj.producto.sku

    @admin.display(ordering='producto__nombre', description='Producto')
    def producto_nombre(self, obj):
        return obj.producto.nombre

    @admin.display(description='Disponible')
    def get_disponible(self, obj):
        return obj.cantidad_disponible


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = (
        'fecha',
        'producto',
        'tipo_movimiento',
        'cantidad',
        'bodega_destino',
        'bodega_origen',
        'referencia_operacion',
        'usuario',
        'es_anulado',
        'es_contraasiento'
    )
    list_filter = ('tipo_movimiento', 'es_anulado', 'es_contraasiento', 'fecha', 'bodega_destino', 'bodega_origen')
    search_fields = ('producto__sku', 'producto__nombre', 'referencia_operacion', 'lote')
    date_hierarchy = 'fecha'

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SolicitudAnulacionMovimiento)
class SolicitudAnulacionMovimientoAdmin(admin.ModelAdmin):
    list_display = (
        'folio',
        'movimiento',
        'estado',
        'usuario_solicita',
        'fecha_solicitud',
        'usuario_autoriza',
        'fecha_resolucion'
    )
    list_filter = ('estado', 'fecha_solicitud')
    search_fields = ('folio', 'motivo_solicitud', 'movimiento__producto__sku', 'movimiento__referencia_operacion')
    readonly_fields = ('fecha_solicitud',)