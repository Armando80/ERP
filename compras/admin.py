from django.contrib import admin
from .models import Proveedor, OrdenCompra_Maestro, OrdenCompra_Detalle

@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('razon_social', 'rfc', 'telefono', 'dias_credito', 'activo')
    search_fields = ('razon_social', 'rfc', 'nombre_comercial')
    list_filter = ('activo',)
    list_editable = ('activo',)

# Opcional: Esto permite ver los productos de la compra directamente dentro de la orden
class OrdenCompra_DetalleInline(admin.TabularInline):
    model = OrdenCompra_Detalle
    extra = 0
    readonly_fields = ('subtotal_linea',)

@admin.register(OrdenCompra_Maestro)
class OrdenCompra_MaestroAdmin(admin.ModelAdmin):
    list_display = ('folio', 'proveedor', 'fecha_emision', 'estado', 'moneda', 'total')
    list_filter = ('estado', 'moneda', 'fecha_emision')
    search_fields = ('folio', 'proveedor__razon_social')
    readonly_fields = ('folio', 'subtotal', 'impuestos', 'total')
    inlines = [OrdenCompra_DetalleInline]