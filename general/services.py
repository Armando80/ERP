"""
ERP Decorlata - Servicio de Integración con Banco de México (SIE API)
Permite consultar y sincronizar tipos de cambio oficiales (FIX USD y EUR)
para alimentar la arquitectura Multidivisa del sistema.
"""

import json
import logging
import datetime
from decimal import Decimal, InvalidOperation
import urllib.request
import urllib.error
import ssl

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import Moneda, TipoCambio

logger = logging.getLogger(__name__)


class BanxicoAPIError(Exception):
    """Excepción para errores al comunicar con el API SIE de Banxico."""
    pass


class TipoCambioNoDisponibleError(Exception):
    """Excepción cuando no existe un tipo de cambio aplicable para una fecha."""
    pass


class BanxicoClient:
    """
    Cliente HTTP nativo (sin dependencias externas) para consumir el
    API REST del Sistema de Información Económica (SIE) de Banxico.
    """

    DEFAULT_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/"

    def __init__(self, token=None, base_url=None, timeout=10):
        self.token = token or getattr(settings, 'BANXICO_TOKEN', '')
        self.base_url = (base_url or getattr(settings, 'BANXICO_API_BASE_URL', self.DEFAULT_BASE_URL)).rstrip('/') + '/'
        self.timeout = timeout

    def _crear_contexto_ssl(self):
        """Crea contexto SSL seguro compatible con TLS 1.3 exigido por Banxico."""
        ctx = ssl.create_default_context()
        return ctx

    def consultar_series(self, series_ids, fecha_inicio=None, fecha_fin=None, oportuno=True):
        """
        Consulta una o varias series en Banxico.

        :param series_ids: Lista o tupla de identificadores de serie (ej: ['SF43718', 'SF46410'])
        :param fecha_inicio: date o str YYYY-MM-DD (opcional)
        :param fecha_fin: date o str YYYY-MM-DD (opcional)
        :param oportuno: Si es True, obtiene el dato publicado más reciente
        :return: dict estructurado con los datos devueltos por Banxico
        """
        if not self.token:
            raise BanxicoAPIError(
                "No se ha configurado el Token de Banxico. "
                "Regístralo en BANXICO_TOKEN dentro de variables de entorno o settings.py."
            )

        if not series_ids:
            raise BanxicoAPIError("Se requiere al menos un identificador de serie para consultar Banxico.")

        series_param = ','.join(series_ids) if isinstance(series_ids, (list, tuple, set)) else str(series_ids)

        if oportuno or not fecha_inicio:
            url = f"{self.base_url}series/{series_param}/datos/oportuno"
        else:
            f_ini = fecha_inicio.strftime('%Y-%m-%d') if isinstance(fecha_inicio, (datetime.date, datetime.datetime)) else str(fecha_inicio)
            f_fin = fecha_fin.strftime('%Y-%m-%d') if isinstance(fecha_fin, (datetime.date, datetime.datetime)) else (f_ini if not fecha_fin else str(fecha_fin))
            url = f"{self.base_url}series/{series_param}/datos/{f_ini}/{f_fin}"

        req = urllib.request.Request(
            url,
            headers={
                'Bmx-Token': self.token,
                'Accept': 'application/json',
                'User-Agent': 'ERP-Decorlata/1.0 (+https://decorlata.com)',
            },
            method='GET'
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._crear_contexto_ssl()) as response:
                status_code = response.getcode()
                raw_data = response.read().decode('utf-8')
                return self._parsear_respuesta(raw_data)

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
                data = json.loads(error_body)
                if 'error' in data and 'mensaje' in data['error']:
                    msg = data['error']['mensaje']
                    det = data['error'].get('detalle', '')
                    raise BanxicoAPIError(f"Error de Banxico ({e.code}): {msg}. {det}".strip())
            except Exception:
                pass
            raise BanxicoAPIError(f"HTTP {e.code} al consultar Banxico: {e.reason}. {error_body}")

        except urllib.error.URLError as e:
            raise BanxicoAPIError(f"Falla de conectividad con Banxico: {str(e.reason)}")
        except Exception as e:
            raise BanxicoAPIError(f"Error inesperado al conectar con Banxico: {str(e)}")

    def _parsear_respuesta(self, raw_json):
        """Parsea la respuesta JSON oficial de Banxico y normaliza tipos de datos."""
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise BanxicoAPIError(f"Respuesta inválida de Banxico (JSON corrupto): {str(e)}")

        bmx = payload.get('bmx', {})
        series_list = bmx.get('series', [])

        resultado = {}
        for s in series_list:
            id_serie = s.get('idSerie')
            titulo = s.get('titulo', '')
            datos = s.get('datos', [])

            puntos_validos = []
            for d in datos:
                fecha_str = d.get('fecha')
                dato_str = d.get('dato')

                # Si es día inhábil / festivo, Banxico devuelve 'N/E'
                if not dato_str or dato_str.strip().upper() == 'N/E':
                    continue

                try:
                    fecha_obj = datetime.datetime.strptime(fecha_str, '%d/%m/%Y').date()
                    valor_num = Decimal(str(dato_str).replace(',', ''))
                    puntos_validos.append({
                        'fecha': fecha_obj,
                        'valor': valor_num,
                    })
                except (ValueError, InvalidOperation) as ex:
                    logger.warning("No se pudo parsear dato de Banxico serie %s (%s, %s): %s", id_serie, fecha_str, dato_str, ex)

            resultado[id_serie] = {
                'titulo': titulo,
                'datos': puntos_validos
            }

        return resultado


# Mapeo por defecto de monedas del ERP a series de Banxico
MONEDAS_A_SERIES_BANXICO = {
    'USD': 'SF43718',  # Tipo de cambio FIX para solventar obligaciones
    'EUR': 'SF46410',  # Cotización Euro frente al peso mexicano
}


def sincronizar_tipos_cambio_banxico(token=None, fecha=None, cliente=None):
    """
    Sincroniza los tipos de cambio de USD y EUR desde Banxico hacia la BD local.
    Usa 'datos/oportuno' si no se especifica fecha, o la fecha puntual si se pasa.

    :param token: Token de Banxico (opcional, usa settings por defecto)
    :param fecha: datetime.date específica a consultar (opcional)
    :param cliente: Instancia de BanxicoClient (útil para inyección/tests)
    :return: dict con el resultado de la sincronización
    """
    series_config = getattr(settings, 'BANXICO_SERIES', MONEDAS_A_SERIES_BANXICO)
    series_a_consultar = list(series_config.values())

    client = cliente or BanxicoClient(token=token)

    if fecha:
        datos_series = client.consultar_series(series_a_consultar, fecha_inicio=fecha, fecha_fin=fecha, oportuno=False)
    else:
        datos_series = client.consultar_series(series_a_consultar, oportuno=True)

    # Invertir el mapeo serie -> código moneda
    serie_a_codigo = {v: k for k, v in series_config.items()}

    registros_actualizados = []
    advertencias = []

    with transaction.atomic():
        for id_serie, info in datos_series.items():
            codigo_moneda = serie_a_codigo.get(id_serie)
            if not codigo_moneda:
                continue

            # Garantizar que la moneda exista en el catálogo
            moneda, _ = Moneda.objects.get_or_create(
                codigo=codigo_moneda,
                defaults={
                    'nombre': 'Dólar Americano' if codigo_moneda == 'USD' else ('Euro Zona' if codigo_moneda == 'EUR' else codigo_moneda),
                    'simbolo': '$' if codigo_moneda == 'USD' else ('€' if codigo_moneda == 'EUR' else '$')
                }
            )

            puntos = info.get('datos', [])
            if not puntos:
                advertencias.append(f"No se obtuvieron observaciones válidas para {codigo_moneda} ({id_serie}).")
                continue

            # Tomar el dato más reciente del conjunto devuelto
            ultimo_punto = puntos[-1]
            fecha_publicacion = ultimo_punto['fecha']
            valor_decimal = ultimo_punto['valor']

            tc, created = TipoCambio.objects.update_or_create(
                moneda_origen=moneda,
                fecha=fecha_publicacion,
                defaults={
                    'valor_en_mxn': valor_decimal,
                    'fuente': 'BANXICO_FIX'
                }
            )

            registros_actualizados.append({
                'moneda': codigo_moneda,
                'fecha': fecha_publicacion,
                'valor': valor_decimal,
                'creado': created,
                'id_registro': tc.id
            })

    return {
        'exito': True,
        'registros': registros_actualizados,
        'advertencias': advertencias,
        'total': len(registros_actualizados),
        'fuente': 'BANXICO_FIX'
    }


def sincronizar_tipos_cambio_dof_bce(fecha=None):
    """
    Sincroniza los tipos de cambio oficiales de USD y EUR sin requerir token personal:
    - USD: Consulta el Diario Oficial de la Federación (DOF) vía endpoint oficial abierto (dolardof.com),
           que refleja el tipo de cambio FIX determinado por Banxico (Art. 20 CFF).
    - EUR: Consulta el Banco Central Europeo (BCE) vía api.frankfurter.dev.
    """
    registros_actualizados = []
    advertencias = []

    # 1. Moneda USD (Diario Oficial de la Federación / Banxico FIX)
    try:
        url_dof = 'https://dolardof.com/api/v1/dof/today'
        req_usd = urllib.request.Request(url_dof, headers={'User-Agent': 'ERP-Decorlata/1.0 (+https://decorlata.com)'})
        with urllib.request.urlopen(req_usd, timeout=8) as resp:
            data_dof = json.loads(resp.read().decode('utf-8'))
            valor_usd = Decimal(str(data_dof['value']))
            fecha_usd_str = data_dof.get('date')
            fecha_usd = datetime.datetime.strptime(fecha_usd_str, '%Y-%m-%d').date() if fecha_usd_str else timezone.now().date()

            moneda_usd, _ = Moneda.objects.get_or_create(
                codigo='USD',
                defaults={'nombre': 'Dólar Americano', 'simbolo': '$'}
            )

            tc_usd, created_usd = TipoCambio.objects.update_or_create(
                moneda_origen=moneda_usd,
                fecha=fecha_usd,
                defaults={
                    'valor_en_mxn': valor_usd,
                    'fuente': 'DOF_OFICIAL'
                }
            )
            registros_actualizados.append({
                'moneda': 'USD',
                'fecha': fecha_usd,
                'valor': valor_usd,
                'creado': created_usd,
                'id_registro': tc_usd.id,
                'fuente': 'DOF_OFICIAL'
            })
    except Exception as e:
        logger.warning("No se pudo obtener el tipo de cambio USD desde DOF: %s", e)
        advertencias.append(f"USD DOF: {str(e)}")

    # 2. Moneda EUR (Banco Central Europeo / Frankfurter)
    try:
        url_eur = 'https://api.frankfurter.dev/v1/latest?from=EUR&to=MXN'
        req_eur = urllib.request.Request(url_eur, headers={'User-Agent': 'ERP-Decorlata/1.0 (+https://decorlata.com)'})
        with urllib.request.urlopen(req_eur, timeout=8) as resp:
            data_eur = json.loads(resp.read().decode('utf-8'))
            valor_eur = Decimal(str(data_eur['rates']['MXN']))
            fecha_eur_str = data_eur.get('date')
            fecha_eur = datetime.datetime.strptime(fecha_eur_str, '%Y-%m-%d').date() if fecha_eur_str else timezone.now().date()

            moneda_eur, _ = Moneda.objects.get_or_create(
                codigo='EUR',
                defaults={'nombre': 'Euro Zona', 'simbolo': '€'}
            )

            tc_eur, created_eur = TipoCambio.objects.update_or_create(
                moneda_origen=moneda_eur,
                fecha=fecha_eur,
                defaults={
                    'valor_en_mxn': valor_eur,
                    'fuente': 'BCE_OFICIAL'
                }
            )
            registros_actualizados.append({
                'moneda': 'EUR',
                'fecha': fecha_eur,
                'valor': valor_eur,
                'creado': created_eur,
                'id_registro': tc_eur.id,
                'fuente': 'BCE_OFICIAL'
            })
    except Exception as e:
        logger.warning("No se pudo obtener el tipo de cambio EUR desde BCE: %s", e)
        advertencias.append(f"EUR BCE: {str(e)}")

    if not registros_actualizados:
        raise BanxicoAPIError("No fue posible obtener tipos de cambio oficiales de ninguna fuente en línea.")

    return {
        'exito': True,
        'registros': registros_actualizados,
        'advertencias': advertencias,
        'total': len(registros_actualizados),
        'fuente': 'DOF / BCE Oficial'
    }


def sincronizar_tipos_cambio_automatico(token=None, fecha=None):
    """
    Sincroniza los tipos de cambio de divisas extranjeras hacia la BD local.
    Estrategia de resiliencia:
    1. Si hay un Token de Banxico configurado (vía settings.py, argumento o variable de entorno),
       consulta prioritariamente el API SIE de Banxico (series SF43718 y SF46410).
    2. Si no hay token de Banxico configurado o la consulta a Banxico falla (ej. sin token o error de servicio),
       conmuta automáticamente al canal público oficial (Diario Oficial de la Federación DOF y Banco Central Europeo BCE).
    Esto garantiza operación ininterrumpida y 100% automatizada sin requerir intervención manual.
    """
    token_disponible = token or getattr(settings, 'BANXICO_TOKEN', '')
    if token_disponible:
        try:
            res = sincronizar_tipos_cambio_banxico(token=token_disponible, fecha=fecha)
            res['fuente'] = 'Banxico SIE (FIX)'
            return res
        except BanxicoAPIError as e:
            logger.warning("Fallo al consultar Banxico SIE con token (%s). Activando fallback DOF/BCE...", e)

    # Fallback automático sin necesidad de token
    return sincronizar_tipos_cambio_dof_bce(fecha=fecha)


def obtener_tipo_cambio_vigente(moneda_o_codigo, fecha=None):
    """
    Retorna el tipo de cambio oficial vigente en MXN para una moneda y fecha dadas.
    Aplica la regla de negocio y legal mexicana (Art. 20 CFF):
    - Si la moneda es MXN, retorna Decimal('1.000000').
    - Si la moneda es extranjera (USD/EUR):
      1. Busca el tipo de cambio exacto registrado para esa fecha.
      2. Si no existe (fin de semana, día inhábil bancario o antes de publicación FIX),
         retorna el último tipo de cambio oficial publicado inmediato anterior (fecha__lte).
      3. Si no existe ningún registro previo en la BD, intenta sincronizar de forma automática al vuelo.

    :param moneda_o_codigo: Instancia de Moneda o string con código ISO ('MXN', 'USD', 'EUR')
    :param fecha: datetime.date de la operación (default: hoy)
    :return: Decimal con el tipo de cambio frente a MXN
    """
    codigo = moneda_o_codigo.codigo if isinstance(moneda_o_codigo, Moneda) else str(moneda_o_codigo).upper().strip()

    # 1. Moneda local base
    if codigo == 'MXN':
        return Decimal('1.000000')

    fecha_consulta = fecha or timezone.now().date()
    if isinstance(fecha_consulta, datetime.datetime):
        fecha_consulta = fecha_consulta.date()

    # 2. Buscar en BD local para la fecha exacta
    moneda_obj = Moneda.objects.filter(codigo=codigo).first()
    if moneda_obj:
        tc_exacto = TipoCambio.objects.filter(moneda_origen=moneda_obj, fecha=fecha_consulta).first()
        if tc_exacto:
            return tc_exacto.valor_en_mxn

        # 3. Fallback legal: Último tipo de cambio publicado inmediato anterior
        tc_anterior = TipoCambio.objects.filter(
            moneda_origen=moneda_obj,
            fecha__lte=fecha_consulta
        ).order_by('-fecha').first()

        if tc_anterior:
            return tc_anterior.valor_en_mxn

    # 4. Rescate: No hay registros en BD para esta moneda, intentar sincronización al vuelo
    try:
        try:
            sincronizar_tipos_cambio_banxico(fecha=fecha_consulta)
        except BanxicoAPIError:
            sincronizar_tipos_cambio_automatico(fecha=fecha_consulta)

        moneda_obj = Moneda.objects.filter(codigo=codigo).first()
        if moneda_obj:
            tc_rescate = TipoCambio.objects.filter(
                moneda_origen=moneda_obj,
                fecha__lte=fecha_consulta
            ).order_by('-fecha').first()
            if tc_rescate:
                return tc_rescate.valor_en_mxn
    except Exception as e:
        logger.error("Error al intentar sincronización al vuelo para %s: %s", codigo, e)

    raise TipoCambioNoDisponibleError(
        f"No se encontró un tipo de cambio vigente para la moneda {codigo} en la fecha {fecha_consulta} "
        "y no fue posible obtenerlo automáticamente."
    )
