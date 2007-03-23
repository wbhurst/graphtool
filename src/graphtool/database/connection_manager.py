from graphtool.tools.common import to_timestamp
from graphtool.tools.cache import Cache
from graphtool.base.xml_config import XmlConfig
import threading, cStringIO, traceback

try:  
  import cx_Oracle
  cx_Oracle.OPT_Threading = 1
  oracle_present = True
except:
  oracle_present = False
        
try:    
  import MySQLdb
  mysql_present = True
except Exception, e:
  mysql_present = False

try:
  from pysqlite2 import dbapi2 as sqlite
  sqlite_present = True
except Exception, e:
  sqlite_present = False

db_classes = { \
                 'Oracle':'OracleDatabase',
                 'MySQL' :'MySqlDatabase',
                 'sqlite' :'SqliteDatabase'
             }

class ConnectionManager( XmlConfig ):

  def __init__( self, *args, **kw ):
    self.db_info = {}
    self.db_objs = {}
    super( ConnectionManager, self ).__init__( *args, **kw )

  def parse_dom( self ):
    super( ConnectionManager, self ).parse_dom()
    if 'default' not in self.__dict__.keys():
      self.default = None
    for connection in self.dom.getElementsByTagName('connection'):
      self.parse_connection( connection )

  def parse_connection( self, conn_dom ):
    info = {}
    name = conn_dom.getAttribute('name')
    self.parse_attributes( info, conn_dom )
    if 'Interface' not in info.keys():
      raise ValueError( "Interface not specified in Connection Manager XML." )
    self.db_info[ name ] = info
    self.db_objs[ name ] = None

  def get_connection( self, name ):
    if name == None:
      try:
        name = self.default
      except:
        if self.default == None or len(self.default) == 0: raise Exception( "No default connection specified." )
        else: raise Exception( "Could not find connection named %s." % self.default )
    if name not in self.db_objs.keys():
      raise ValueError( "Unknown connection name %s" % name )
    if self.db_objs[ name ] == None:
      return self.make_connection( name )
    return self.db_objs[ name ]

  def make_connection( self, name ):
    info = self.db_info[ name ]
    try:
      dbclass = globals()[ db_classes[ info['Interface'] ] ]
    except:
      raise Exception( "Could not find DBConnection class!" )
    my_conn = dbclass( info )
    self.db_objs[ name ] = my_conn
    return my_conn

class DBConnection( Cache ):

  def __init__( self, info ):
    super( DBConnection, self ).__init__( info )
    self.info = info
    self.module = None

  def get_connection( self ):
    raise ValueError( "get_connection not implemented!" )

  def get_cursor( self ):
    raise ValueError( "get_cursor not implemented!" )

  def test_connection( self ):
    raise ValueError( "test_connection not implemented!" )

  def release_connection( self, conn ):
    pass

  def release_cursor( self, conn ):
    pass

  def execute_statement( self, statement, vars={} ):
    hash_str = self.make_hash_str( statement, **vars )
    query_lock = self.check_and_add_progress( hash_str )
    if query_lock:
      query_lock.acquire()
      results = self.check_cache( hash_str )
      query_lock.release()
      return results
    else:
      results =  self.check_cache( hash_str )
      if results:
        self.remove_progress( hash_str )
        return results
      try:
        results = self._execute_statement( statement, vars )
      except Exception, e:
        self.remove_progress( hash_str )
        st = cStringIO.StringIO()
        traceback.print_exc( file=st )
        raise Exception( "Exception caught while making SQL query:\n%s\n%s" % (str(e), st.getvalue()) )
      self.add_cache( hash_str, results )
      self.remove_progress( hash_str )
      return results

class OracleDatabase( DBConnection ):

  def __init__( self, *args, **kw ):
    super( OracleDatabase, self ).__init__( *args, **kw )
    if oracle_present:
      self.module = cx_Oracle
    else:
      raise Exception( "Oracle python module did not load correctly." )
    self._conn = None

  def test_connection( self ):
    if self._conn == None: return False
    try:
      test = 'select * from dual'
      curs = self._conn.cursor()
      curs.prepare( test )
      curs.execute( test )
      curs.fetchone()
      assert curs.rowcount > 0
      curs.close()
    except:
      return False
    return True

  def make_connection( self ):
    info = self.info
    conn_str = info['AuthDBUsername'] + '/' + info['AuthDBPassword'] + '@' + info['Database']
    self._conn = self.module.connect( conn_str )
    if ('AuthRole' in info.keys()) and ('AuthRolePassword' in info.keys()):
      curs = self._conn.cursor()
      curs.execute( 'set role ' + info['AuthRole'] + ' identified by ' + info['AuthRolePassword'] )
      curs.close()
    return self._conn

  def get_connection( self ):
    if self._conn == None:
      return self.make_connection()
    elif self.test_connection():
      return self._conn
    else:
      return self.make_connection()
      
  def get_cursor( self ):
    conn = self.get_connection()
    return conn.cursor()

  def release_connection( self, conn ): conn.close()
  
  def release_cursor( self, curs ): curs.close()

  def _execute_statement( self, statement, vars={} ):
    curs = self.get_cursor()
    curs.arraysize = 500
    curs.prepare( statement )
    curs.execute( statement, vars )
    rows = curs.fetchall()
    self.release_cursor( curs )
    return rows

class MySqlDatabase( DBConnection ):

  pool_size = 5

  def __init__( self, *args, **kw ):
    super( MySqlDatabase, self ).__init__( *args, **kw )
    if mysql_present:
      self.module = MySQLdb
    else:
      raise Exception( "MySQL python module did not load correctly." )

    self.conn_lock = threading.Lock()
    self._conns = [ None for i in range( self.pool_size ) ]
    self._conn_use = [ False for i in range( self.pool_size ) ]
    self.conn_sema = threading.BoundedSemaphore( self.pool_size )
    self.cursors = {}

  def make_connection( self ):
    kw = {}
    info = self.info
    assignments = {'host':'Host', 'user':'AuthDBUsername',
                   'passwd':'AuthDBPassword', 'db':'Database',
                   'port':'Port' }
    for key in assignments.keys():
      if assignments[key] in info.keys():
        kw[key] = info[ assignments[key] ]
        if key == 'port':
          kw[key] = int(kw[key])
    conn = MySQLdb.connect( **kw )
    curs = conn.cursor() 
    curs.execute( "set time_zone='+00:00'" )
    curs.close() 
    return conn

  def test_connection( self, i ):
    if self._conns[ i ] == None: return False 
    try:
      conn = self._conns[ i ]
      test = 'select 1+1'
      curs = conn.cursor()
      curs.execute( test )
      curs.fetchall()
      assert curs.rowcount > 0
      curs.close()
    except:
      return False
    return True

  def get_connection( self ):
    self.conn_sema.acquire()
    self.conn_lock.acquire()
    for i in range( self.pool_size ):
      if self._conn_use[i] == False:
        # Get connection, save it to self._conn[i]
        if self.test_connection( i ):
          self._conn_use[ i ] = True
          conn = self._conns[ i ]
        else:
          conn = self.make_connection()
          self._conn_use[ i ] = True
          self._conns[ i ] = conn
        break 
    self.conn_lock.release()
    return conn

  def get_cursor( self ):
    conn = self.get_connection()
    curs = conn.cursor()
    self.cursors[ curs ] = conn
    return curs

  def release_cursor( self, curs ):
    curs.close()
    self.release_connection( self.cursors[ curs ] )
    
  def release_connection( self, conn ):
    self.conn_lock.acquire()
    self.conn_sema.release()
    i = -1
    for i in range(self.pool_size):
      if self._conns[ i ] == conn:
        break
    self._conn_use[i] = False
    self.conn_lock.release()
  
  def _execute_statement( self, sql_string, sql_vars ):
    curs = self.get_cursor()
    curs.arraysize = 500
    my_string = str( sql_string )
    placement_dict = {}
    for var_name in sql_vars.keys():
      var_string = ':' + var_name
      placement = my_string.find( var_string )
      var_string_len = len(var_string)
      while placement >= 0:
        placement_dict[placement] = var_name
        my_string = my_string[:placement] + '%s' + my_string[placement+var_string_len:]
        placement = my_string.find( var_string )
    places = placement_dict.keys(); places.sort()
    my_tuple = ()
    for place in places:
      my_tuple += (sql_vars[placement_dict[place]],)
    curs.execute( my_string, my_tuple )
    results = curs.fetchall()
    self.release_cursor( curs )
    return results

class SqliteDatabase( DBConnection ):

  def __init__( self, *args, **kw ):
    super( SqliteDatabase, self ).__init__( *args, **kw )
    if sqlite_present:
      self.module = sqlite
    else:
      raise Exception( "sqlite python module did not load correctly." )
    self._conn = None

  def make_connection( self ):
    info = self.info
    conn_str = ['DatabaseFile']
    self._conn = self.module.connect( conn_str )
    return self._conn

  def test_connection( self ):
    if self._conn == None: return False 
    try:
      test = 'select 1+1'
      curs = self._conn.cursor()
      curs.execute( test )
      curs.fetchall()
      assert curs.rowcount > 0
      curs.close()
    except:
      return False
    return True

  def get_connection( self ):
    if self._conn == None:
      return self.make_connection()
    elif self.test_connection():
      return self._conn
    else:
      return self.make_connection()
      
  def get_cursor( self ):
    conn = self.get_connection()
    return conn.cursor()

  def release_connection( self, conn ): conn.close()
  
  def release_cursor( self, curs ): curs.close()

  def _execute_statement( self, statement, vars={} ):
    curs = self.get_cursor()
    curs.arraysize = 500
    curs.prepare( statement )
    curs.execute( statement, vars )
    rows = curs.fetchall()
    self.release_cursor( curs )
    return rows
