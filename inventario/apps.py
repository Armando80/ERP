from django.apps import AppConfig


class InventarioConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventario'
    verbose_name = 'Gestión de Inventarios'

    def ready(self):
        # Es vital importar las señales aquí para que Django las registre
        # en el momento que arranca el servidor.
        import inventario.signals
