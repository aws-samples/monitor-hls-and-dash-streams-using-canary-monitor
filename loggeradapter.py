import logging

class JsonLoggerAdapter(logging.LoggerAdapter):
  def process(self, msg, kwargs):
    if 'extra' in kwargs:
      kwargs['extra'].update(self.extra)
    else:
      kwargs['extra'] = self.extra
    return msg, kwargs

def getloggeradapterclass(use_json_logger):
  if use_json_logger:
    try:
      import pythonjsonlogger
      return JsonLoggerAdapter
    except ImportError:
      pass
  return logging.LoggerAdapter
