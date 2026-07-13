from django.shortcuts import render
#from django.contrib.auth.decorators import login_required
from .models import TipoCambio # Importamos tu modelo
import datetime

#@login_required # Obliga a que inicien sesión antes de ver el ERP
#def dashboard_view(request):
    # En pasos posteriores, aquí consultaremos los modelos de Moneda y TipoCambio reales de la base de datos

def dashboard(request):
    # 1. Obtener la fecha de hoy
    hoy = datetime.date.today()
    # Simulamos el día anterior como exige el SAT en CFDI 4.0
    # En producción, esto vendría de una consulta de Banxico
    fecha_consulta = hoy - datetime.timedelta(days=1)

    # 2. Consultar el tipo de cambio del día para USD y EUR
    # Usamos .filter() para obtener una lista y .order_by('-fecha')
    # para asegurar que tomamos el más reciente si hay varios
    # En producción, filtraríamos estrictamente por fecha_consulta
    tipos_cambio = TipoCambio.objects.filter(
        moneda_origen__codigo__in=['USD', 'EUR'],
        # fecha=fecha_consulta # Comentado para simulaciones flexibles
    ).order_by('moneda_origen__codigo') # Asegura un orden fijo para el template

    # 3. Crear el contexto para pasar a la plantilla
    context = {
        'tipos_cambio': tipos_cambio,
        # Puedes agregar aquí otras métricas (órdenes, ventas, etc.)
    }

    return render(request, 'general/dashboard.html', context)
