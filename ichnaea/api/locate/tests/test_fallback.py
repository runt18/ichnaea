import colander
import mock
import pytest
import requests_mock
from redis import RedisError
from requests.exceptions import RequestException

from ichnaea.api.exceptions import LocationNotFound
from ichnaea.api.locate.constants import DataSource
from ichnaea.api.locate.fallback import (
    ExternalResult,
    FallbackCache,
    FallbackPositionSource,
    OUTBOUND_SCHEMA,
    RESULT_SCHEMA,
)
from ichnaea.api.locate.query import Query
from ichnaea.api.locate.result import (
    Position,
    PositionResultList,
)
from ichnaea.api.locate.tests.base import (
    BaseSourceTest,
    DummyModel,
)
from ichnaea.api.locate.tests.test_query import QueryTest
from ichnaea import floatjson
from ichnaea.models import Radio
from ichnaea.tests.factories import (
    ApiKeyFactory,
    BlueShardFactory,
    CellShardFactory,
    WifiShardFactory,
)


class TestExternalResult(object):

    def test_not_found(self):
        result = ExternalResult(None, None, None, None)
        assert result.not_found()

    def test_not_found_accuracy(self):
        result = ExternalResult(1.0, 1.0, None, None)
        assert result.not_found()

    def test_found(self):
        result = ExternalResult(1.0, 1.0, 10, None)
        assert not result.not_found()

    def test_found_fallback(self):
        result = ExternalResult(1.0, 1.0, 10, 'lacf')
        assert not result.not_found()

    def test_score(self):
        result = ExternalResult(1.0, 1.0, 10, None)
        assert result.score == 10.0

    def test_score_fallback(self):
        result = ExternalResult(1.0, 1.0, 10, 'lacf')
        assert result.score == 5.0


class TestResultSchema(object):

    def test_empty(self):
        with pytest.raises(colander.Invalid):
            RESULT_SCHEMA.deserialize({})

    def test_accuracy_float(self):
        data = RESULT_SCHEMA.deserialize(
            {'location': {'lat': 1.0, 'lng': 1.0}, 'accuracy': 11.6})
        assert (data ==
                {'lat': 1.0, 'lon': 1.0, 'accuracy': 11.6, 'fallback': None})

    def test_accuracy_missing(self):
        with pytest.raises(colander.Invalid):
            RESULT_SCHEMA.deserialize(
                {'location': {'lat': 1.0, 'lng': 1.0}, 'fallback': 'lacf'})

    def test_fallback(self):
        data = RESULT_SCHEMA.deserialize(
            {'location': {'lat': 1.0, 'lng': 1.0},
             'accuracy': 10.0, 'fallback': 'lacf'})
        assert (data ==
                {'lat': 1.0, 'lon': 1.0, 'accuracy': 10.0, 'fallback': 'lacf'})

    def test_fallback_invalid(self):
        data = RESULT_SCHEMA.deserialize(
            {'location': {'lat': 1.0, 'lng': 1.0},
             'accuracy': 10.0, 'fallback': 'cidf'})
        assert (data ==
                {'lat': 1.0, 'lon': 1.0, 'accuracy': 10.0, 'fallback': None})

    def test_fallback_missing(self):
        data = RESULT_SCHEMA.deserialize(
            {'location': {'lat': 1.0, 'lng': 1.0}, 'accuracy': 10.0})
        assert (data ==
                {'lat': 1.0, 'lon': 1.0, 'accuracy': 10.0, 'fallback': None})

    def test_location_incomplete(self):
        with pytest.raises(colander.Invalid):
            RESULT_SCHEMA.deserialize(
                {'location': {'lng': 1.0}, 'accuracy': 10.0,
                 'fallback': 'lacf'})

    def test_location_missing(self):
        with pytest.raises(colander.Invalid):
            RESULT_SCHEMA.deserialize({'accuracy': 10.0, 'fallback': 'lacf'})


class TestOutboundSchema(object):

    def test_empty(self):
        assert OUTBOUND_SCHEMA.deserialize({}) == {}
        assert OUTBOUND_SCHEMA.deserialize({'unknown_field': 1}) == {}

    def test_fallback(self):
        assert (OUTBOUND_SCHEMA.deserialize(
            {'fallbacks': {'ipf': False}}) ==
            {'fallbacks': {}})
        assert (OUTBOUND_SCHEMA.deserialize(
            {'fallbacks': {'lacf': False}}) ==
            {'fallbacks': {'lacf': False}})
        assert (OUTBOUND_SCHEMA.deserialize(
            {'fallbacks': {'ipf': True, 'lacf': False}}) ==
            {'fallbacks': {'lacf': False}})

    def test_query(self):
        query = Query()
        data = OUTBOUND_SCHEMA.deserialize(query.json())
        assert data == {'fallbacks': {'lacf': True}}

    def test_blue(self):
        blues = BlueShardFactory.build_batch(2)
        query = Query(blue=[
            {'macAddress': blue.mac, 'age': 1500, 'name': 'beacon',
             'signalStrength': -90}
            for blue in blues])
        data = OUTBOUND_SCHEMA.deserialize(query.json())
        assert (data == {
            'bluetoothBeacons': [{
                'macAddress': blues[0].mac,
                'age': 1500,
                'name': 'beacon',
                'signalStrength': -90,
            }, {
                'macAddress': blues[1].mac,
                'age': 1500,
                'name': 'beacon',
                'signalStrength': -90,
            }],
            'fallbacks': {'lacf': True},
        })

    def test_cell(self):
        cell = CellShardFactory.build(radio=Radio.lte)
        query = Query(cell=[
            {'radioType': cell.radio,
             'mobileCountryCode': cell.mcc,
             'mobileNetworkCode': cell.mnc,
             'locationAreaCode': cell.lac,
             'cellId': cell.cid,
             'age': 1200,
             'asu': None,
             'primaryScramblingCode': 5,
             'signalStrength': -70,
             'timingAdvance': 15,
             'unknown_field': 'foo'}])
        data = OUTBOUND_SCHEMA.deserialize(query.json())
        assert (data == {
            'cellTowers': [{
                'radioType': cell.radio.name,
                'mobileCountryCode': cell.mcc,
                'mobileNetworkCode': cell.mnc,
                'locationAreaCode': cell.lac,
                'cellId': cell.cid,
                'primaryScramblingCode': 5,
                'age': 1200,
                'signalStrength': -70,
                'timingAdvance': 15,
            }],
            'fallbacks': {'lacf': True},
        })

    def test_wifi(self):
        wifis = WifiShardFactory.build_batch(2)
        query = Query(wifi=[
            {'macAddress': wifi.mac, 'age': 2000,
             'signalStrength': -90, 'ssid': 'wifi'}
            for wifi in wifis])
        data = OUTBOUND_SCHEMA.deserialize(query.json())
        assert (data == {
            'wifiAccessPoints': [{
                'macAddress': wifis[0].mac,
                'age': 2000,
                'signalStrength': -90,
                'ssid': 'wifi',
            }, {
                'macAddress': wifis[1].mac,
                'age': 2000,
                'signalStrength': -90,
                'ssid': 'wifi',
            }],
            'fallbacks': {'lacf': True},
        })


@pytest.yield_fixture(scope='function')
def cache(raven, redis, session, stats):
    yield FallbackCache(raven, redis, stats)


class TestCache(QueryTest):

    def _query(self, **kwargs):
        return Query(api_key=ApiKeyFactory(fallback_cache_expire=60), **kwargs)

    def test_get_blue(self, cache, stats):
        blues = BlueShardFactory.build_batch(2)
        query = self._query(blue=self.blue_model_query(blues))
        assert cache.get(query) is None
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:miss']),
        ])

    def test_set_blue(self, cache, stats):
        blues = BlueShardFactory.build_batch(2)
        blue = blues[0]
        query = self._query(blue=self.blue_model_query(blues))
        result = ExternalResult(blue.lat, blue.lon, blue.radius, None)
        cache.set(query, result)
        assert cache.get(query) == result
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:hit']),
        ])

    def test_get_cell(self, cache, stats):
        cells = CellShardFactory.build_batch(1)
        query = self._query(cell=self.cell_model_query(cells))
        assert cache.get(query) is None
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:miss']),
        ])

    def test_set_cell(self, cache, redis, stats):
        cell = CellShardFactory.build()
        query = self._query(cell=self.cell_model_query([cell]))
        result = ExternalResult(cell.lat, cell.lon, cell.radius, None)
        cache.set(query, result, expire=60)
        keys = redis.keys('cache:fallback:cell:*')
        assert len(keys) == 1
        assert 50 < redis.ttl(keys[0]) <= 60
        assert cache.get(query) == result
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:hit']),
        ])

    def test_set_cell_not_found(self, cache, redis, stats):
        cell = CellShardFactory.build()
        query = self._query(cell=self.cell_model_query([cell]))
        result = ExternalResult(None, None, None, None)
        cache.set(query, result)
        keys = redis.keys('cache:fallback:cell:*')
        assert len(keys) == 1
        assert redis.get(keys[0]) == b'"404"'
        assert cache.get(query) == result
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:hit']),
        ])

    def test_get_cell_multi(self, cache, stats):
        cells = CellShardFactory.build_batch(2)
        query = self._query(cell=self.cell_model_query(cells))
        assert cache.get(query) is None
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:bypassed']),
        ])

    def test_get_wifi(self, cache, stats):
        wifis = WifiShardFactory.build_batch(2)
        query = self._query(wifi=self.wifi_model_query(wifis))
        assert cache.get(query) is None
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:miss']),
        ])

    def test_set_wifi(self, cache, stats):
        wifis = WifiShardFactory.build_batch(2)
        wifi = wifis[0]
        query = self._query(wifi=self.wifi_model_query(wifis))
        result = ExternalResult(wifi.lat, wifi.lon, wifi.radius, None)
        cache.set(query, result)
        assert cache.get(query) == result
        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:hit']),
        ])

    def test_set_wifi_inconsistent(self, cache, stats):
        wifis1 = WifiShardFactory.build_batch(2)
        cache.set(
            self._query(wifi=self.wifi_model_query(wifis1)),
            ExternalResult(wifis1[0].lat, wifis1[0].lon, 100, None))

        # similar lat/lon, worse accuracy
        wifis2 = WifiShardFactory.build_batch(
            2, lat=wifis1[0].lat + 0.0001, lon=wifis1[0].lon)
        cache.set(
            self._query(wifi=self.wifi_model_query(wifis2)),
            ExternalResult(wifis2[0].lat, wifis2[0].lon, 200, None))

        # check combined query, avg lat/lon, max accuracy
        query = self._query(wifi=self.wifi_model_query(wifis1 + wifis2))
        cached = cache.get(query)
        assert cached[0] == (wifis1[0].lat + wifis2[0].lat) / 2.0
        assert cached[1] == wifis1[0].lon
        assert round(cached[2], 2) == 205.56
        assert cached[3] is None

        # different lat/lon
        wifis3 = WifiShardFactory.build_batch(2, lat=wifis1[0].lat + 10.0)
        cache.set(
            self._query(wifi=self.wifi_model_query(wifis3)),
            ExternalResult(wifis3[0].lat, wifis3[0].lon, 300, None))

        # check combined query, inconsistent result
        query = self._query(
            wifi=self.wifi_model_query(wifis1 + wifis2 + wifis3))
        assert cache.get(query) is None

        stats.check(counter=[
            ('locate.fallback.cache', 1, 1, ['status:hit']),
            ('locate.fallback.cache', 1, 1, ['status:inconsistent']),
        ])

    def test_get_mixed(self, cache, stats):
        blues = BlueShardFactory.build_batch(2)
        cells = CellShardFactory.build_batch(1)
        wifis = WifiShardFactory.build_batch(2)

        query = self._query(cell=self.cell_model_query(cells),
                            wifi=self.wifi_model_query(wifis))
        assert cache.get(query) is None

        query = self._query(blue=self.blue_model_query(blues),
                            cell=self.cell_model_query(cells))
        assert cache.get(query) is None

        query = self._query(blue=self.blue_model_query(blues),
                            wifi=self.wifi_model_query(wifis))
        assert cache.get(query) is None

        stats.check(counter=[
            ('locate.fallback.cache', 3, 1, ['status:bypassed']),
        ])


class TestFallback(BaseSourceTest):

    fallback_model = DummyModel(lat=51.5366, lon=0.03989, radius=1500.0)
    Source = FallbackPositionSource

    @property
    def fallback_result(self):
        return {
            'location': {
                'lat': self.fallback_model.lat,
                'lng': self.fallback_model.lon,
            },
            'accuracy': float(self.fallback_model.radius),
            'fallback': 'lacf',
        }

    @property
    def fallback_cached_result(self):
        return floatjson.float_dumps({
            'lat': self.fallback_model.lat,
            'lon': self.fallback_model.lon,
            'accuracy': float(self.fallback_model.radius),
            'fallback': 'lacf',
        })

    def _mock_redis_client(self):
        client = mock.Mock()
        client.pipeline.return_value = client
        client.__enter__ = mock.Mock(return_value=client)
        client.__exit__ = mock.Mock(return_value=None)
        client.expire.return_value = mock.Mock()
        client.get.return_value = mock.Mock()
        client.mget.return_value = mock.Mock()
        client.set.return_value = mock.Mock()
        client.mset.return_value = mock.Mock()
        return client

    def test_success(self, geoip_db, http_session, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell],
                fallback={
                    'lacf': True,
                    'ipf': False,
                },
            )
            results = source.search(query)
            self.check_model_results(results, [self.fallback_model])
            assert results.best().score == 5.0

            request_json = mock_request.request_history[0].json()

        assert request_json['fallbacks'] == {'lacf': True}
        stats.check(counter=[
            ('locate.fallback.lookup', ['fallback_name:fall', 'status:200']),
        ], timer=[
            ('locate.fallback.lookup', ['fallback_name:fall']),
        ])

    def test_failed_call(self, geoip_db, http_session,
                         raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            def raise_request_exception(request, context):
                raise RequestException()

            mock_request.register_uri(
                'POST', requests_mock.ANY, json=raise_request_exception)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('RequestException', 1)])

    def test_invalid_json(self, geoip_db, http_session,
                          raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=['invalid json'])

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('Invalid', 1)])

    def test_malformed_json(self, geoip_db, http_session,
                            raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, content=b'[invalid json')

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('JSONDecodeError', 1)])

    def test_403_response(self, geoip_db, http_session,
                          raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, status_code=403)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('HTTPError', 1)])
        stats.check(counter=[
            ('locate.fallback.lookup', ['fallback_name:fall', 'status:403']),
        ])

    def test_404_response(self, geoip_db, http_session,
                          raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY,
                json=LocationNotFound.json_body(),
                status_code=404)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('HTTPError', 0)])
        stats.check(counter=[
            ('locate.fallback.lookup', ['fallback_name:fall', 'status:404']),
        ])

    def test_500_response(self, geoip_db, http_session,
                          raven, session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, status_code=500)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

        raven.check([('HTTPError', 1)])
        stats.check(counter=[
            ('locate.fallback.lookup', ['fallback_name:fall', 'status:500']),
        ], timer=[
            ('locate.fallback.lookup', ['fallback_name:fall']),
        ])

    def test_api_key_disallows(self, geoip_db, http_session,
                               session, source, stats):
        api_key = ApiKeyFactory.build(allow_fallback=False)
        cells = CellShardFactory.build_batch(2)
        wifis = WifiShardFactory.build_batch(2)

        query = self.model_query(
            geoip_db, http_session, session, stats,
            cells=cells, wifis=wifis, api_key=api_key)
        self.check_should_search(source, query, False)

    def test_check_one_blue(self, geoip_db, http_session,
                            session, source, stats):
        blue = BlueShardFactory.build()

        query = self.model_query(
            geoip_db, http_session, session, stats,
            blues=[blue])
        self.check_should_search(source, query, False)

    def test_check_one_wifi(self, geoip_db, http_session,
                            session, source, stats):
        wifi = WifiShardFactory.build()

        query = self.model_query(
            geoip_db, http_session, session, stats,
            wifis=[wifi])
        self.check_should_search(source, query, False)

    def test_check_empty(self, geoip_db, http_session,
                         session, source, stats):
        query = self.model_query(
            geoip_db, http_session, session, stats)
        self.check_should_search(source, query, False)

    def test_check_invalid_cell(self, geoip_db, http_session,
                                session, source, stats):
        malformed_cell = CellShardFactory.build()
        malformed_cell.mcc = 99999

        query = self.model_query(
            geoip_db, http_session, session, stats,
            cells=[malformed_cell])
        self.check_should_search(source, query, False)

    def test_check_invalid_wifi(self, geoip_db, http_session,
                                session, source, stats):
        wifi = WifiShardFactory.build()
        malformed_wifi = WifiShardFactory.build()
        malformed_wifi.mac = 'abcd'

        query = self.model_query(
            geoip_db, http_session, session, stats,
            wifis=[wifi, malformed_wifi])
        self.check_should_search(source, query, False)

    def test_check_empty_result(self, geoip_db, http_session,
                                session, source, stats):
        wifis = WifiShardFactory.build_batch(2)

        query = self.model_query(
            geoip_db, http_session, session, stats,
            wifis=wifis)
        self.check_should_search(source, query, True)

    def test_check_geoip_result(self, london_model, geoip_db, http_session,
                                session, source, stats):
        wifis = WifiShardFactory.build_batch(2)
        results = PositionResultList(Position(
            source=DataSource.geoip,
            lat=london_model.lat,
            lon=london_model.lon,
            accuracy=float(london_model.radius),
            score=0.6))

        query = self.model_query(
            geoip_db, http_session, session, stats,
            wifis=wifis, ip=london_model.ip)
        self.check_should_search(source, query, True, results=results)

    def test_check_already_good_result(self, geoip_db, http_session,
                                       session, source, stats):
        wifis = WifiShardFactory.build_batch(2)
        results = PositionResultList(Position(
            source=DataSource.internal,
            lat=1.0, lon=1.0, accuracy=100.0, score=1.0))

        query = self.model_query(
            geoip_db, http_session, session, stats,
            wifis=wifis)
        self.check_should_search(source, query, False, results=results)

    def test_rate_limit_allow(self, geoip_db, http_session,
                              session, source, stats):
        api_key = ApiKeyFactory.build(allow_fallback=True)
        cell = CellShardFactory()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            for _ in range(api_key.fallback_ratelimit):
                query = self.model_query(
                    geoip_db, http_session, session, stats,
                    cells=[cell])
                results = source.search(query)
                self.check_model_results(results, [self.fallback_model])

    def test_rate_limit_blocks(self, geoip_db, http_session,
                               redis, session, source, stats):
        api_key = ApiKeyFactory.build(allow_fallback=True)
        cell = CellShardFactory()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            ratelimit_key = source._ratelimit_key(
                api_key.fallback_name,
                api_key.fallback_ratelimit_interval,
            )
            redis.set(ratelimit_key, api_key.fallback_ratelimit)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

    def test_rate_limit_redis_failure(self, geoip_db, http_session,
                                      session, source, stats):
        cell = CellShardFactory.build()
        mock_redis_client = self._mock_redis_client()
        mock_redis_client.pipeline.side_effect = RedisError()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            with mock.patch.object(source, 'redis_client',
                                   mock_redis_client):
                query = self.model_query(
                    geoip_db, http_session, session, stats,
                    cells=[cell])
                results = source.search(query)
                self.check_model_results(results, None)

            assert mock_redis_client.pipeline.called
            assert not mock_request.called

    def test_get_cache_redis_failure(self, geoip_db, http_session,
                                     raven, session, source, stats):
        cell = CellShardFactory.build()
        mock_redis_client = self._mock_redis_client()
        mock_redis_client.mget.side_effect = RedisError()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            with mock.patch.object(source.cache, 'redis_client',
                                   mock_redis_client):
                query = self.model_query(
                    geoip_db, http_session, session, stats,
                    cells=[cell])
                results = source.search(query)
                self.check_model_results(results, [self.fallback_model])

            assert mock_redis_client.mget.called
            assert mock_request.called

        raven.check([('RedisError', 1)])
        stats.check(counter=[
            ('locate.fallback.cache', ['status:failure']),
        ])

    def test_set_cache_redis_failure(self, geoip_db, http_session,
                                     raven, session, source, stats):
        cell = CellShardFactory.build()
        mock_redis_client = self._mock_redis_client()
        mock_redis_client.mget.return_value = []
        mock_redis_client.mset.side_effect = RedisError()
        mock_redis_client.expire.side_effect = RedisError()
        mock_redis_client.execute.side_effect = RedisError()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            with mock.patch.object(source.cache, 'redis_client',
                                   mock_redis_client):
                query = self.model_query(
                    geoip_db, http_session, session, stats,
                    cells=[cell])
                results = source.search(query)
                self.check_model_results(results, [self.fallback_model])

            assert mock_redis_client.mget.called
            assert mock_redis_client.mset.called
            assert mock_request.called

        raven.check([('RedisError', 1)])
        stats.check(counter=[
            ('locate.fallback.cache', ['status:miss']),
        ])

    def test_cache_single_cell(self, geoip_db, http_session,
                               session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            query.cell[0].signalStrength = -77
            results = source.search(query)
            self.check_model_results(results, [self.fallback_model])
            assert results.best().score == 5.0

            assert mock_request.call_count == 1
            stats.check(counter=[
                ('locate.fallback.cache', ['status:miss']),
                ('locate.fallback.lookup',
                    ['fallback_name:fall', 'status:200']),
            ], timer=[
                ('locate.fallback.lookup', ['fallback_name:fall']),
            ])

            # vary the signal strength, not part of cache key
            query.cell[0].signalStrength = -82
            results = source.search(query)
            self.check_model_results(results, [self.fallback_model])
            assert results.best().score == 5.0

            assert mock_request.call_count == 1
            stats.check(counter=[
                ('locate.fallback.cache', ['status:hit']),
                ('locate.fallback.lookup',
                    ['fallback_name:fall', 'status:200']),
            ], timer=[
                ('locate.fallback.lookup', ['fallback_name:fall']),
            ])

    def test_cache_empty_result(self, geoip_db, http_session,
                                session, source, stats):
        cell = CellShardFactory.build()

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST',
                requests_mock.ANY,
                json=LocationNotFound.json_body(),
                status_code=404
            )

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

            assert mock_request.call_count == 1
            stats.check(counter=[
                ('locate.fallback.cache', ['status:miss']),
                ('locate.fallback.lookup',
                    ['fallback_name:fall', 'status:404']),
            ])

            query = self.model_query(
                geoip_db, http_session, session, stats,
                cells=[cell])
            results = source.search(query)
            self.check_model_results(results, None)

            assert mock_request.call_count == 1
            stats.check(counter=[
                ('locate.fallback.cache', ['status:hit']),
                ('locate.fallback.lookup',
                    ['fallback_name:fall', 'status:404']),
            ])

    def test_dont_recache(self, geoip_db, http_session,
                          session, source, stats):
        cell = CellShardFactory.build()
        mock_redis_client = self._mock_redis_client()
        mock_redis_client.mget.return_value = [self.fallback_cached_result]

        with requests_mock.Mocker() as mock_request:
            mock_request.register_uri(
                'POST', requests_mock.ANY, json=self.fallback_result)

            with mock.patch.object(source.cache, 'redis_client',
                                   mock_redis_client):
                query = self.model_query(
                    geoip_db, http_session, session, stats,
                    cells=[cell])
                results = source.search(query)
                self.check_model_results(results, [self.fallback_model])

            assert mock_redis_client.mget.called
            assert not mock_redis_client.mset.called

        stats.check(counter=[
            ('locate.fallback.cache', ['status:hit']),
        ])
