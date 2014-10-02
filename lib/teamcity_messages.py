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


def _escape(text):
    """Escapes special TeamCity characters."""
    # Escape escape character
    result = text.replace('|', "||")
    characters = {"'": "|'", "\n": "|n", "\r": "|r", "[": "|[", "]": "|]"}
    for char, escaped_char in characters.items():
        result = result.replace(char, escaped_char)
    return result
                                

def report_test(name, failed=False, message=None, details=None):
    """Prints service messages for TeamCity to report test result."""
    # Filter paramerts which will not be passed to TeamCity service messages
    # and escape all special TeamCity characters
    parameters = {k: _escape(v) for k, v in locals().items()
                  if k != 'failed' and v}

    logger = logging.getLogger('teamcity_logger')

    logger.info("##teamcity[testStarted name='{}']".format(name))

    if failed:
        test_failed_msg = "##teamcity[testFailed"
        # Add parameters to TeamCity service message
        for var, value in parameters.items():
            test_failed_msg += " {}='{}'".format(var, value)
        test_failed_msg += "]"

        logger.info(test_failed_msg)

    logger.info("##teamcity[testFinished name='{}']".format(name))
