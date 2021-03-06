import warnings

from pymysql import err
from sqlalchemy import text

from ichnaea.models.wifi import WifiShard0


class TestDatabase(object):

    def test_constructors(self, db_rw, db_ro):
        assert db_rw.engine.name == 'mysql'
        assert db_ro.engine.name == 'mysql'

    def test_table_creation(self, session):
        result = session.execute('select * from cell_gsm;')
        assert result.first() is None

    def test_show_warnings_backport(self, session):
        # Fixed in PyMySQL 0.6.7
        stmt = text('DROP TABLE IF EXISTS a; DROP TABLE IF EXISTS b;')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', err.Warning)
            session.execute(stmt)

    def test_executemany_backport(self, session):
        # Fixed in PyMySQL 0.6.7
        session.add(WifiShard0(mac='000000123456'))
        session.add(WifiShard0(mac='000000abcdef'))
        session.commit()

    def test_excecutemany_on_duplicate(self, session):
        stmt = WifiShard0.__table__.insert(
            mysql_on_duplicate=u'mac = "\x00\x00\x000\x00\x00", region="\xe4"'
        )
        values = [
            {'mac': '000000100000', 'region': 'DE'},
            {'mac': '000000200000', 'region': u'\xe4'},
            {'mac': '000000200000', 'region': u'\xf6'},
        ]
        session.execute(stmt.values(values))
        rows = session.query(WifiShard0).all()
        assert (set([row.mac for row in rows]) ==
                set(['000000100000', '000000300000']))
        assert (set([row.region for row in rows]) ==
                set(['DE', u'\xe4']))
