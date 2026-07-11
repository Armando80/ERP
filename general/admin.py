from django.contrib import admin
from .models import Moneda, TipoCambio # Importa tus modelos

@admin.register(Moneda)
class MonedaAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'simbolo')
    search_fields = ('codigo', 'nombre')

@admin.register(TipoCambio)
class TipoCambioAdmin(admin.ModelAdmin):
    # list_display debe coincidir con los campos de tu modelo 'TipoCambio'
    list_display = ('moneda_origen', 'valor_en_mxn', 'fecha')
    list_filter = ('moneda_origen', 'fecha')
    date_hierarchy = 'fecha' # Facilita la navegación por fecha