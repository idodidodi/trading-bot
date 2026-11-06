import hashlib
import hmac
import json
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

from execution_gateway import (BybitDemo, ExecutionError, ExecutionGateway,
                               Instrument, SubmissionUnknown, Velotrade, from_config)


def instrument(symbol='BTCUSDT', scale='1'):
    # Test fixture, deliberately not current exchange metadata.
    return Instrument(symbol, scale, '0.001', '0.001', '100', '0.1')


class GatewayTests(unittest.TestCase):
    def test_provider_switch_keeps_identical_strategy_calls(self):
        for adapter, symbol in [(BybitDemo('key', 'secret'), 'BTCUSDT'),
                                (Velotrade('user', 'pass', 'default:123'), 'BTC/USD')]:
            gateway = ExecutionGateway(adapter, {'BTC': instrument(symbol)}, True)
            adapter._request = MagicMock(return_value={'orderId': '123'})
            if isinstance(adapter, Velotrade):
                adapter._token = 'token'
            receipt = gateway.open_position('BTC', 'buy', '0.01', 'signal-1')
            self.assertEqual(receipt.state, 'acknowledged')
            call = adapter._request.call_args
            body = call.kwargs['body'] if 'body' in call.kwargs else call.args[1]
            self.assertEqual(body.get('symbol', body.get('instrument')), symbol)
            self.assertEqual(body.get('qty', body.get('quantity')), '0.01')
            gateway.close_position('BTC', 'buy', '0.01', 'signal-1-close',
                                   position_id='0' if isinstance(adapter, BybitDemo) else 'pos-1')
            call = adapter._request.call_args
            body = call.kwargs['body'] if 'body' in call.kwargs else call.args[1]
            self.assertEqual(body['side'].lower(), 'sell')
            if isinstance(adapter, BybitDemo):
                self.assertIs(body['reduceOnly'], True)
            else:
                self.assertEqual(body['positionEffect'], 'CLOSE')
                self.assertEqual(body['positionCode'], 'pos-1')

    def test_disabled_writes_and_invalid_inputs_do_not_submit(self):
        adapter = MagicMock()
        gateway = ExecutionGateway(adapter, {'BTC': instrument()})
        with self.assertRaises(ExecutionError):
            gateway.open_position('BTC', 'buy', '.01', 's1')
        gateway.orders_enabled = True
        for quantity in ('NaN', 'Infinity', '0', '-1', '.0001', '101', '.0015'):
            with self.assertRaises(ValueError):
                gateway.open_position('BTC', 'buy', quantity, 's1')
        for identity in ('', '../bad', 'x' * 37):
            with self.assertRaises(ValueError):
                gateway.open_position('BTC', 'buy', '.01', identity)
        with self.assertRaises(ValueError):
            gateway.open_position('BTC', 'long', '.01', 's1')
        adapter.submit.assert_not_called()

    def test_explicit_quantity_conversion(self):
        binding = Instrument('EUR/USD', '100000', '1000', '1000', '1000000', '.00001')
        self.assertEqual(binding.quantity('.01'), '1000.00')
        with self.assertRaises(ValueError):
            binding.quantity('.015')

    def test_factory_uses_saved_env_names_and_rejects_unknown_provider(self):
        config = {'provider': 'bybit_demo', 'instruments': {}}
        gateway = from_config(config, {'family_trading_bot_bybit_demo_api_key': 'key',
                                       'family_trading_bot_bybit_demo_api_secret': 'secret'})
        self.assertIsInstance(gateway.adapter, BybitDemo)
        self.assertFalse(gateway.orders_enabled)
        with self.assertRaises(ValueError):
            from_config({'provider': 'bybit_mainnet'}, {})
        dx = from_config({'provider': 'velotrade', 'account': 'default:1', 'instruments': {}},
                         {'VELOTRADE_USERNAME': 'user', 'VELOTRADE_PASSWORD': 'pass'})
        self.assertIsInstance(dx.adapter, Velotrade)

    @patch('execution_gateway._send')
    @patch('execution_gateway.time.time', return_value=1700000000)
    def test_bybit_signs_exact_query_and_body_on_demo_host(self, clock, send):
        send.return_value = {'retCode': 0, 'result': {'orderId': '1'}}
        adapter = BybitDemo('key', 'secret')
        adapter._request('/v5/position/list', {'category': 'linear', 'symbol': 'BTCUSDT'})
        req = send.call_args.args[0]
        self.assertTrue(req.full_url.startswith('https://api-demo.bybit.com/'))
        query = 'category=linear&symbol=BTCUSDT'
        expected = hmac.new(b'secret', ('1700000000000key5000' + query).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header('X-bapi-sign'), expected)
        adapter.submit(instrument(), 'buy', '.01', 's1')
        req = send.call_args.args[0]
        expected = hmac.new(b'secret', b'1700000000000key5000' + req.data, hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header('X-bapi-sign'), expected)
        self.assertEqual(json.loads(req.data)['positionIdx'], 0)

    @patch('execution_gateway._send')
    def test_dx_login_account_encoding_and_order_format(self, send):
        send.side_effect = [{'sessionToken': 'private'}, {'orderId': 'order1'}]
        gateway = ExecutionGateway(Velotrade('user', 'password', 'default:123'),
                                   {'BTC': instrument('BTC/USD')}, True)
        gateway.open_position('BTC', 'buy', '.01', 'signal1')
        login, order = [call.args[0] for call in send.call_args_list]
        self.assertEqual(json.loads(login.data)['domain'], 'default')
        self.assertIn('/accounts/default%3A123/orders', order.full_url)
        self.assertEqual(order.get_header('Authorization'), 'DXAPI private')
        self.assertEqual(json.loads(order.data)['positionEffect'], 'OPEN')

    def test_unsupported_close_is_rejected_before_network(self):
        for adapter, position in [(BybitDemo('key', 'secret'), '1'),
                                  (Velotrade('u', 'p', 'default:1'), None)]:
            adapter._request = MagicMock()
            with self.assertRaises(ValueError):
                adapter.submit(instrument(), 'sell', '.01', 's1', close=True, position_id=position)
            adapter._request.assert_not_called()

    @patch('execution_gateway._send')
    def test_no_success_without_order_id_or_with_error_payload(self, send):
        for result in ({'retCode': 0, 'result': {}}, {'retCode': 10006, 'retMsg': 'private'}):
            send.return_value = result
            with self.assertRaises(SubmissionUnknown) as caught:
                BybitDemo('key', 'secret').submit(instrument(), 'buy', '.01', 's1')
            self.assertNotIn('private', str(caught.exception))

    @patch('execution_gateway.urllib.request.build_opener')
    def test_timeout_is_unknown_for_write_sanitized_and_never_retried(self, opener):
        from execution_gateway import _send
        opener.return_value.open.side_effect = TimeoutError('private')
        request = urllib.request.Request('https://api-demo.bybit.com/v5/order/create', data=b'{}')
        with self.assertRaises(SubmissionUnknown) as caught:
            _send(request)
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_redirects_do_not_forward_credentials(self):
        from execution_gateway import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))


if __name__ == '__main__':
    unittest.main()
