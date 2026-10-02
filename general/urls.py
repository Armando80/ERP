from django.urls import path
from .views import dashboard_view, sincronizar_tipo_cambio_view

app_name = 'general'

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    path('tipo-cambio/sincronizar/', sincronizar_tipo_cambio_view, name='sincronizar_tipo_cambio'),
]
