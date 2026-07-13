from django.shortcuts import render
#from django.contrib.auth.decorators import login_required
from .models import TipoCambio # Importamos tu modelo
from decimal import Decimal
import datetime

#@login_required # Obliga a que inicien sesión antes de ver el ERP
def dashboard_view(request):
    """
    Vista principal para el Dashboard General de ERP México (Decorlata S.A. de C.V.).
    Esta vista consulta los modelos de Moneda y TipoCambio dinámicamente.
    """
    
    # --- Lógica de Negocio: Fase 1 (Base Multidivisa) ---
    
    # 1. Obtener la fecha de hoy
    hoy = datetime.date.today()
    
    # Simulamos el día anterior como exige el SAT en CFDI 4.0.
    # En producción, esto vendría de una consulta automática a Banxico.
    fecha_consulta = hoy - datetime.timedelta(days=1)

    # 2. Consultar el tipo de cambio del día para USD y EUR.
    # Usamos .filter() para obtener una lista y .order_by('moneda_origen__codigo')
    # para asegurar un orden fijo (USD, luego EUR) en la plantilla.
    tipos_cambio = TipoCambio.objects.filter(
        moneda_origen__codigo__in=['USD', 'EUR'],
        # fecha=fecha_consulta # Comentado para simulaciones flexibles
    ).order_by('moneda_origen__codigo')

    # 3. Construimos la lista de tarjetas de moneda para el template
    # Replicamos el diseño exacto de image_8.png y general_dashboard.
    currency_cards = []
    
    # Agregamos dinámicamente las tarjetas de USD y EUR de la base de datos
    for tc in tipos_cambio:
        style = 'primary' if tc.moneda_origen.codigo == 'USD' else 'warning'
        icon = 'dollar' if tc.moneda_origen.codigo == 'USD' else 'euro'
        name = 'DÓLAR AMERICANO' if tc.moneda_origen.codigo == 'USD' else 'EURO ZONA'
        
        currency_cards.append({
            'code': tc.moneda_origen.codigo,
            'name': name,
            'rate': tc.valor_en_mxn, # Valor real poblado
            'style': style,
            'icon': icon,
            'update_date': tc.fecha, # Fecha real de actualización
        })

    # Agregamos la tarjeta de la moneda local (MXN) como base
    currency_cards.append({
        'code': 'MXN',
        'name': 'MONEDA LOCAL',
        'rate': Decimal('1.0000'), # MXN siempre es 1.0000 frente a sí mismo
        'style': 'success',
        'icon': 'cash-stack',
        'update_date': hoy,
        'mxn': True, # Marca especial para el template
    })

    # 4. Crear el contexto para pasar a la plantilla
    context = {
        'currencies': currency_cards, # Nueva lista dinámica de tarjetas
        # Puedes agregar aquí otras métricas (órdenes, ventas, etc.)
    }

    return render(request, 'general/dashboard.html', context)