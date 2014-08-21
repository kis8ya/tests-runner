import logging

class block(object):
    """Prints teamcity service messages to combine output in a single block.
    There is a config file for the module (teamcity_messages.conf);
    where you can specify some parameters (for more information see logging.config documentation).

    It can be used as a decorator:
    >>> @teamcity_messages.block()
    ... def foo(name):
    ...     print("doing something...")
    ... 
    >>> foo(name="name")
    ##teamcity[blockOpened name='foo']
    doing something...
    ##teamcity[blockClosed name='foo']
    >>>

    It can be used with the with statement:
    >>> with teamcity_messages.block("new block"):
    ...     print("some useful things...")
    ... 
    ##teamcity[blockOpened name='new block']
    some useful things...
    ##teamcity[blockClosed name='new block']
    >>> 
    """
    def __init__(self, name=None):
        self.name = name
        self.logger = logging.getLogger('teamcity_logger')

    def open_block(self):
        self.logger.info("##teamcity[blockOpened name='{0}']".format(self.name))

    def close_block(self):
        self.logger.info("##teamcity[blockClosed name='{0}']".format(self.name))

    def __call__(self, f):
        if self.name is None:
            self.name = f.func_name

        def wrapper(*args, **kwargs):
            self.open_block()
            result = f(*args, **kwargs)
            self.close_block()

            return result

        return wrapper

    def __enter__(self):
        self.open_block()

    def __exit__(self, type, value, traceback):
        self.close_block()
