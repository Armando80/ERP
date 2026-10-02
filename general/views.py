from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from decimal import Decimal
import datetime

import logging
from .models import TipoCambio, Moneda
from .services import (
    sincronizar_tipos_cambio_banxico,
    sincronizar_tipos_cambio_automatico,
    BanxicoAPIError,
)

logger = logging.getLogger(__name__)


def obtener_tarjetas_moneda(auto_sincronizar=True):
    """
    Construye la lista estructurada de divisas para el Dashboard
    obteniendo el tipo de cambio oficial más reciente de cada divisa extranjera.
    Si auto_sincronizar=True y no hay cotización para hoy, sincroniza de forma desatendida.
    """
    hoy = timezone.now().date()

    if auto_sincronizar:
        hay_usd_hoy = TipoCambio.objects.filter(moneda_origen__codigo='USD', fecha=hoy).exists()
        if not hay_usd_hoy:
            try:
                sincronizar_tipos_cambio_automatico()
            except Exception as e:
                logger.warning("Auto-sincronización automática de divisas en Dashboard: %s", e)

    currency_cards = []

    # 1. Dólar Americano (FIX)
    tc_usd = TipoCambio.objects.filter(moneda_origen__codigo='USD').order_by('-fecha').first()
    currency_cards.append({
        'code': 'USD',
        'name': 'DÓLAR AMERICANO (FIX)',
        'rate': tc_usd.valor_en_mxn if tc_usd else Decimal('0.0000'),
        'style': 'primary',
        'icon': 'currency-dollar',
        'update_date': tc_usd.fecha if tc_usd else hoy,
        'fuente': tc_usd.fuente if tc_usd else 'Sin registro',
        'mxn': False,
    })

    # 2. Euro Zona (DEG / Banxico / BCE)
    tc_eur = TipoCambio.objects.filter(moneda_origen__codigo='EUR').order_by('-fecha').first()
    currency_cards.append({
        'code': 'EUR',
        'name': 'EURO ZONA',
        'rate': tc_eur.valor_en_mxn if tc_eur else Decimal('0.0000'),
        'style': 'warning',
        'icon': 'currency-euro',
        'update_date': tc_eur.fecha if tc_eur else hoy,
        'fuente': tc_eur.fuente if tc_eur else 'Sin registro',
        'mxn': False,
    })

    # 3. Moneda Local (MXN)
    currency_cards.append({
        'code': 'MXN',
        'name': 'MONEDA LOCAL',
        'rate': Decimal('1.0000'),
        'style': 'success',
        'icon': 'cash-stack',
        'update_date': hoy,
        'fuente': 'BASE',
        'mxn': True,
    })

    return currency_cards


def dashboard_view(request):
    """
    Vista principal para el Dashboard General de ERP México (Decorlata S.A. de C.V.).
    Muestra los tipos de cambio oficiales más recientes y métricas globales.
    """
    context = {
        'currencies': obtener_tarjetas_moneda(auto_sincronizar=True),
    }
    return render(request, 'general/dashboard.html', context)


@login_required
def sincronizar_tipo_cambio_view(request):
    """
    Endpoint HTMX / POST para sincronizar tipos de cambio oficiales.
    Actualiza la BD de forma automática (Banxico o canal DOF/BCE) y retorna
    el fragmento de tarjetas actualizado sin recargar la página.
    """
    if request.method == 'POST':
        try:
            try:
                resultado = sincronizar_tipos_cambio_banxico()
                fuente = 'Banxico SIE'
            except BanxicoAPIError:
                resultado = sincronizar_tipos_cambio_automatico()
                fuente = resultado.get('fuente', 'DOF / Banxico Oficial')

            total = resultado.get('total', 0)
            messages.success(
                request,
                f"¡Tipos de cambio oficiales actualizados! ({total} divisas sincronizadas vía {fuente})."
            )
        except Exception as e:
            messages.error(request, f"Error durante la sincronización: {str(e)}")

        currencies = obtener_tarjetas_moneda(auto_sincronizar=False)

        if request.headers.get('HX-Request'):
            return render(request, 'general/partials/_tarjetas_divisas.html', {
                'currencies': currencies
            })

    return redirect('general:dashboard')