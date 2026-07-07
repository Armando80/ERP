from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required # Obliga a que inicien sesión antes de ver el ERP
def dashboard_view(request):
    # En pasos posteriores, aquí consultaremos los modelos de Moneda y TipoCambio reales de la base de datos
    return render(request, 'general/dashboard.html')
