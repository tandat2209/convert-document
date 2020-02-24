import os
import logging
import traceback
from threading import RLock
from flask import Flask, request, send_file
from tempfile import mkstemp
from werkzeug.wsgi import ClosingIterator
from werkzeug.exceptions import HTTPException
from pantomime import FileName, normalize_mimetype, mimetype_extension

from convert.converter import Converter, ConversionFailure
from convert.formats import load_mime_extensions
from .document_types import *

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('convert')
lock = RLock()
extensions = load_mime_extensions()
converter = Converter()


class ShutdownMiddleware:
    def __init__(self, application):
        self.application = application

    def post_request(self):
        if app.is_dead:
            os._exit(127)

    def __call__(self, environ, after_response):
        iterator = self.application(environ, after_response)
        try:
            return ClosingIterator(iterator, [self.post_request])
        except Exception:
            traceback.print_exc()
            return iterator


app = Flask("convert")
app.is_dead = False
app.wsgi_app = ShutdownMiddleware(app.wsgi_app)


@app.route("/")
def info():
    if app.is_dead:
        return ("BUSY", 503)
    return ("OK", 200)


@app.route("/convert", methods=['POST'])
def convert():
    timeout = int(request.args.get('timeout', 1000))
    upload_file = None
    output_format = request.form.get('format')
    if not output_format in LIBREOFFICE_EXPORT_TYPES:
        return ("%s format is not supported" % (output_format), 400)
    try:
        for upload in request.files.values():
            file_name = FileName(upload.filename)
            mime_type = normalize_mimetype(upload.mimetype)
            if not file_name.has_extension:
                file_name.extension = extensions.get(mime_type)
            if not file_name.has_extension:
                file_name.extension = mimetype_extension(mime_type)
            fd, upload_file = mkstemp(suffix=file_name.safe())
            os.close(fd)
            log.info('Convert to %s: %s [%s]',
                     output_format, upload_file, mime_type)
            upload.save(upload_file)
            converter.convert_file(upload_file, output_format, timeout)
            output_filename = "%s.%s" % (converter.OUT, output_format)
            log.info("Send file %s [Mime-type: %s]" %
                     (output_filename, OUTPUT_MIME_TYPES[output_format]))
            return send_file(output_filename,
                             mimetype=OUTPUT_MIME_TYPES[output_format],
                             attachment_filename=output_filename)
        return ('No file uploaded', 400)
    except HTTPException:
        raise
    except ConversionFailure as ex:
        app.is_dead = True
        return (str(ex), 400)
    except Exception as ex:
        app.is_dead = True
        log.error('Error: %s', ex)
        return ('FAIL', 503)
    finally:
        if upload_file is not None and os.path.exists(upload_file):
            os.unlink(upload_file)
        if os.path.exists(converter.OUT):
            os.unlink(converter.OUT)
