"""Provider-neutral execution boundary. Not wired to scanner/Telegram effects.

Quantities are base-asset units, converted by explicit instrument bindings.
Submit acknowledgements are NEVER evidence of a fill. Never retry submissions
after an ambiguous failure; reconcile using the original client_id first.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import ssl
import time
import certifi
import urllib.error
import urllib.parse
import urllib.request


class ExecutionError(RuntimeError):
    """Sanitized provider failure."""


class SubmissionUnknown(ExecutionError):
    """A write may have reached the provider. Reconcile before another write."""


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid decimal quantity/price') from None
    if not result.is_finite() or result <= 0:
        raise ValueError('Quantity/price must be finite and positive')
    return result


@dataclass(frozen=True)
class Instrument:
    """Mapping must come from the selected provider's instrument discovery.

    provider_units_per_base is 1 for verified Bybit linear base quantities.
    DXtrade FX, CFDs and crypto can have different units; never infer a scale.
    """
    symbol: str
    provider_units_per_base: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    max_market_quantity: Decimal
    price_tick: Decimal

    def __post_init__(self):
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError('Provider symbol required')
        for name in ('provider_units_per_base', 'quantity_step', 'min_quantity',
                     'max_market_quantity', 'price_tick'):
            object.__setattr__(self, name, number(getattr(self, name)))
        if self.min_quantity > self.max_market_quantity:
            raise ValueError('Invalid instrument limits')

    def quantity(self, base_quantity):
        quantity = number(base_quantity) * self.provider_units_per_base
        if not self.min_quantity <= quantity <= self.max_market_quantity:
            raise ValueError('Quantity outside verified instrument limits')
        if quantity % self.quantity_step:
            raise ValueError('Quantity does not match provider step; no silent rounding')
        return format(quantity, 'f')

    def price(self, value):
        price = number(value)
        if price % self.price_tick:
            raise ValueError('Price does not match provider tick')
        return format(price, 'f')


@dataclass(frozen=True)
class OrderReceipt:
    provider: str
    client_id: str
    order_id: str
    state: str = 'acknowledged'  # never "filled" based on submit response


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not forward authentication to a redirected host


def _send(request):
    write = request.get_method() != 'GET'
    try:
        tls = urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))
        with urllib.request.build_opener(_NoRedirect(), tls).open(request, timeout=15) as response:
            raw = response.read()
        return json.loads(raw) if raw else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        error = SubmissionUnknown if write else ExecutionError
        raise error('Provider request failed; no retry was attempted') from None


class BybitDemo:
    """USDT/USDC linear contracts on the hardcoded DEMO host only."""
    name = 'bybit_demo'

    def __init__(self, key, secret):
        if not key or not secret:
            raise ValueError('Bybit demo credentials required')
        self._key, self._secret = key, secret

    def _request(self, path, params=None, body=None):
        query = urllib.parse.urlencode(params or {})
        payload = json.dumps(body, separators=(',', ':')) if body is not None else query
        timestamp, window = str(int(time.time() * 1000)), '5000'
        signature = hmac.new(self._secret.encode(),
                             (timestamp + self._key + window + payload).encode(),
                             hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            'https://api-demo.bybit.com' + path + ('?' + query if query else ''),
            data=payload.encode() if body is not None else None,
            method='POST' if body is not None else 'GET',
            headers={'Content-Type': 'application/json', 'X-BAPI-API-KEY': self._key,
                     'X-BAPI-SIGN': signature, 'X-BAPI-TIMESTAMP': timestamp,
                     'X-BAPI-RECV-WINDOW': window})
        response = _send(request)
        if not isinstance(response, dict) or response.get('retCode') != 0:
            error = SubmissionUnknown if body is not None else ExecutionError
            raise error('Bybit did not return success; reconcile writes before retrying')
        if not isinstance(response.get('result'), dict):
            error = SubmissionUnknown if body is not None else ExecutionError
            raise error('Unexpected Bybit response')
        return response['result']

    def snapshot(self, instrument):
        params = dict(category='linear', symbol=instrument.symbol)
        return dict(provider=self.name,
                    account=self._request('/v5/account/wallet-balance', {'accountType': 'UNIFIED'}),
                    positions=self._request('/v5/position/list', params),
                    orders=self._request('/v5/order/realtime', params))

    def submit(self, instrument, side, quantity, client_id, *, close=False,
               position_id=None):
        body = dict(category='linear', symbol=instrument.symbol,
                    side=side.title(), orderType='Market', qty=quantity,
                    orderLinkId=client_id, reduceOnly=close, positionIdx=0)
        # The shared interface deliberately supports one-way positions only.
        if position_id not in (None, '0'):
            raise ValueError('Only one-way Bybit positions are supported')
        result = self._request('/v5/order/create', body=body)
        order_id = result.get('orderId')
        if not isinstance(order_id, str) or not order_id:
            raise SubmissionUnknown('Bybit acknowledgement has no order ID')
        return OrderReceipt(self.name, client_id, order_id)

    def lookup(self, instrument, client_id):
        params = dict(category='linear', symbol=instrument.symbol, orderLinkId=client_id)
        # REST snapshots retain provider status/fills. An empty result is not
        # proof of rejection, particularly immediately after a timeout.
        return dict(provider=self.name,
                    current=self._request('/v5/order/realtime', params),
                    history=self._request('/v5/order/history', params))


class Velotrade:
    """DXtrade adapter using Velotrade's documented order wire format.

    Existing DXtradeReadOnly stays read-only. This separate adapter requires
    explicit account selection and instrument mappings before any submission.
    """
    name = 'velotrade'

    def __init__(self, username, password, account):
        if not username or not password or not re.fullmatch(r'default:[^/\s]+', account):
            raise ValueError('Velotrade credentials and full default:<account> required')
        self._username, self._password, self._account = username, password, account
        self._token = None

    def _request(self, path, body=None):
        headers = {'Content-Type': 'application/json'}
        if self._token:
            headers['Authorization'] = 'DXAPI ' + self._token
        return _send(urllib.request.Request(
            'https://dx.velotrade.com/dxsca-web' + path, headers=headers,
            method='POST' if body is not None else 'GET',
            data=json.dumps(body).encode() if body is not None else None))

    def _login(self):
        if self._token:
            return
        result = self._request('/login', dict(username=self._username,
                               password=self._password, domain='default'))
        if not isinstance(result, dict) or not result.get('sessionToken'):
            raise ExecutionError('DXtrade login returned no token')
        self._token = result['sessionToken']

    def _path(self):
        return '/accounts/' + urllib.parse.quote(self._account, safe='')

    def snapshot(self, instrument):
        self._login()
        prefix = self._path()
        return dict(provider=self.name,
                    account=self._request(prefix + '/metrics'),
                    positions=self._request(prefix + '/positions'),
                    orders=self._request(prefix + '/orders'))

    def submit(self, instrument, side, quantity, client_id, *, close=False,
               position_id=None):
        if close and not position_id:
            raise ValueError('DXtrade closing requires the discovered position code')
        self._login()
        body = dict(account=self._account, orderCode=client_id, type='MARKET',
                    instrument=instrument.symbol, quantity=quantity,
                    positionEffect='CLOSE' if close else 'OPEN', side=side.upper(), tif='GTC')
        if close:
            body['positionCode'] = position_id
        result = self._request(self._path() + '/orders', body)
        order_id = result.get('orderId') if isinstance(result, dict) else None
        if not order_id:
            raise SubmissionUnknown('DXtrade acknowledgement has no order ID')
        return OrderReceipt(self.name, client_id, str(order_id))

    def lookup(self, instrument, client_id):
        self._login()
        # Return both snapshots, including all pages/fields supplied by DXtrade.
        # Caller reconciles orderCode against client_id; absence is inconclusive.
        prefix = self._path()
        return dict(provider=self.name, client_id=client_id,
                    current=self._request(prefix + '/orders'),
                    history=self._request(prefix + '/orders/history'))


@dataclass
class ExecutionGateway:
    """Strategy-facing interface; no provider branching in strategy code.

    Writes default off. The caller owns sizing, stop policy, durable client IDs,
    account locking, ambiguous-write recovery and fill/protection verification.
    This is a transport boundary, not an autonomous execution/risk engine.
    """
    adapter: object = field(repr=False)
    instruments: dict
    orders_enabled: bool = False

    def snapshot(self, symbol):
        return self.adapter.snapshot(self.instruments[symbol])

    def lookup(self, symbol, client_id):
        self._identity(client_id)
        return self.adapter.lookup(self.instruments[symbol], client_id)

    @staticmethod
    def _identity(client_id):
        if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,36}', client_id):
            raise ValueError('Use a persisted unique client ID of 1–36 safe characters')

    def _submit(self, symbol, side, quantity, client_id, *, close=False, position_id=None):
        if self.orders_enabled is not True:
            raise ExecutionError('Order submission is disabled')
        self._identity(client_id)
        if side not in ('buy', 'sell'):
            raise ValueError('Side must be buy or sell')
        instrument = self.instruments[symbol]
        return self.adapter.submit(instrument, side, instrument.quantity(quantity),
                                   client_id, close=close, position_id=position_id)

    def open_position(self, symbol, side, quantity, client_id):
        return self._submit(symbol, side, quantity, client_id)

    def close_position(self, symbol, position_side, quantity, client_id, *, position_id=None):
        if position_side not in ('buy', 'sell'):
            raise ValueError('Position side must be buy or sell')
        side = 'sell' if position_side == 'buy' else 'buy'
        return self._submit(symbol, side, quantity, client_id,
                            close=True, position_id=position_id)


def from_config(config, environment):
    """No implicit .env loading; pass the project's privately loaded environment.

    Provider selection and instrument bindings are configuration, not URLs.
    There is intentionally no Bybit mainnet selector or arbitrary host option.
    """
    provider = config['provider']
    if provider == 'bybit_demo':
        adapter = BybitDemo(environment['family_trading_bot_bybit_demo_api_key'],
                            environment['family_trading_bot_bybit_demo_api_secret'])
    elif provider == 'velotrade':
        adapter = Velotrade(environment['VELOTRADE_USERNAME'],
                            environment['VELOTRADE_PASSWORD'], config['account'])
    else:
        raise ValueError('Provider must be bybit_demo or velotrade')
    instruments = {alias: Instrument(**binding)
                   for alias, binding in config['instruments'].items()}
    return ExecutionGateway(adapter, instruments, config.get('orders_enabled', False))
