from django.contrib import admin

from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    """Administración del directorio de clientes (datos fiscales, contacto y crédito)."""
    list_display = ('rfc', 'razon_social', 'nombre_contacto', 'correo', 'telefono', 'dias_credito', 'limite_credito', 'activo')
    list_filter = ('activo', 'regimen_fiscal')
    search_fields = ('rfc', 'razon_social', 'nombre_comercial', 'nombre_contacto', 'correo')
